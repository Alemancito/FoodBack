from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .audit import obtener_request_id, registrar_evento_auditoria
from .authz import require_tenant_roles
from .legal_content import SECURITY_PUBLIC, SUPPORT_PUBLIC
from .models import (
    AceptacionLegalTenant,
    AuditEvent,
    DocumentoLegal,
    Membership,
)


_TIPO_POR_SLUG = {
    "terminos": DocumentoLegal.Tipo.TERMINOS,
    "privacidad": DocumentoLegal.Tipo.PRIVACIDAD,
    "cookies": DocumentoLegal.Tipo.COOKIES,
}


def _documento_vigente(tipo):
    return (
        DocumentoLegal.objects
        .filter(tipo=tipo, vigente=True)
        .order_by("-publicado_en", "-creado_en")
        .first()
    )


@require_GET
@never_cache
def legal_center_view(request):
    documentos = {
        "terminos": _documento_vigente(DocumentoLegal.Tipo.TERMINOS),
        "privacidad": _documento_vigente(DocumentoLegal.Tipo.PRIVACIDAD),
        "cookies": _documento_vigente(DocumentoLegal.Tipo.COOKIES),
    }
    return render(
        request,
        "legal/center.html",
        {"documentos": documentos},
    )


@require_GET
@never_cache
def legal_document_view(request, slug):
    tipo = _TIPO_POR_SLUG.get(slug)
    if tipo is None:
        raise Http404("Documento legal no encontrado.")

    documento = _documento_vigente(tipo)
    if documento is None:
        raise Http404("No hay una versión publicada de este documento.")

    return render(
        request,
        "legal/document.html",
        {"documento": documento, "slug": slug},
    )


@require_GET
@never_cache
def security_public_view(request):
    return render(
        request,
        "legal/info.html",
        {
            "titulo": "Seguridad",
            "contenido": SECURITY_PUBLIC,
        },
    )


@require_GET
@never_cache
def support_public_view(request):
    return render(
        request,
        "legal/info.html",
        {
            "titulo": "Soporte",
            "contenido": SUPPORT_PUBLIC,
        },
    )


@require_http_methods(["GET", "POST"])
@never_cache
@login_required(login_url="login_custom")
@require_tenant_roles(Membership.ROLE_OWNER)
def legal_acceptance_view(request):
    tenant = request.tenant
    membership = request.membership

    terminos = _documento_vigente(DocumentoLegal.Tipo.TERMINOS)
    privacidad = _documento_vigente(DocumentoLegal.Tipo.PRIVACIDAD)

    if terminos is None or privacidad is None:
        messages.error(
            request,
            "Los documentos legales vigentes todavía no están disponibles.",
        )
        return redirect("legal_center")

    documentos = [terminos, privacidad]
    aceptados = set(
        AceptacionLegalTenant.objects
        .filter(tenant=tenant, documento__in=documentos)
        .values_list("documento_id", flat=True)
    )

    if request.method == "POST":
        if not (
            request.POST.get("acepta_terminos") == "on"
            and request.POST.get("acepta_privacidad") == "on"
        ):
            messages.error(
                request,
                "Debes confirmar Términos y Privacidad para registrar la aceptación.",
            )
        else:
            request_id = obtener_request_id(request)
            creadas = []

            with transaction.atomic():
                for documento in documentos:
                    _, creada = AceptacionLegalTenant.objects.get_or_create(
                        tenant=tenant,
                        documento=documento,
                        defaults={
                            "actor_usuario": request.user,
                            "actor_username": request.user.username[:150],
                            "actor_role": membership.rol,
                            "version_snapshot": documento.version,
                            "contenido_sha256_snapshot": documento.contenido_sha256,
                            "request_id": request_id,
                        },
                    )
                    if creada:
                        creadas.append(documento.tipo)

            registrar_evento_auditoria(
                request=request,
                evento="legal.documents.accepted",
                categoria=AuditEvent.Categoria.ADMINISTRACION,
                severidad=AuditEvent.Severidad.INFO,
                resultado=AuditEvent.Resultado.EXITO,
                descripcion="Aceptación versionada de documentos legales del Tenant.",
                actor_role=membership.rol,
                objeto_tipo="Tenant",
                objeto_id=str(tenant.public_id),
                metadata={
                    "documentos_nuevos": creadas,
                    "version_terminos": terminos.version,
                    "version_privacidad": privacidad.version,
                },
            )

            if creadas:
                messages.success(
                    request,
                    "La aceptación legal quedó registrada correctamente.",
                )
            else:
                messages.info(
                    request,
                    "Estas versiones ya habían sido aceptadas por el restaurante.",
                )
            return redirect("legal_acceptance")

    aceptacion_terminos = terminos.id in aceptados
    aceptacion_privacidad = privacidad.id in aceptados

    return render(
        request,
        "legal/acceptance.html",
        {
            "terminos": terminos,
            "privacidad": privacidad,
            "aceptacion_terminos": aceptacion_terminos,
            "aceptacion_privacidad": aceptacion_privacidad,
            "todo_aceptado": aceptacion_terminos and aceptacion_privacidad,
        },
    )
