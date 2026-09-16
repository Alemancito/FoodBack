from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q, Subquery
from django.utils import timezone

from pedidos.models import AuditEvent, SecurityIncident


class Command(BaseCommand):
    help = (
        "Limpia auditoría histórica e incidentes resueltos de FoodBack "
        "según la política de retención. Por defecto solo simula."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help=(
                "Ejecuta borrado real. Sin esta bandera el comando "
                "solo informa cuántos registros serían eliminados."
            ),
        )
        parser.add_argument(
            "--audit-days",
            type=int,
            default=None,
        )
        parser.add_argument(
            "--incident-days",
            type=int,
            default=None,
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=None,
        )

    def handle(self, *args, **options):
        audit_days = (
            options["audit_days"]
            if options["audit_days"] is not None
            else settings.FOODBACK_AUDIT_RETENTION_DAYS
        )
        incident_days = (
            options["incident_days"]
            if options["incident_days"] is not None
            else settings.FOODBACK_SECURITY_INCIDENT_RETENTION_DAYS
        )
        batch_size = (
            options["batch_size"]
            if options["batch_size"] is not None
            else settings.FOODBACK_AUDIT_CLEANUP_BATCH_SIZE
        )

        if audit_days < 1:
            raise CommandError("--audit-days debe ser >= 1.")

        if incident_days < audit_days:
            raise CommandError(
                "--incident-days debe ser >= --audit-days para conservar "
                "el resumen de incidentes por más tiempo que el detalle crudo."
            )

        if not (1 <= batch_size <= 10000):
            raise CommandError("--batch-size debe estar entre 1 y 10000.")

        ahora = timezone.now()
        audit_cutoff = ahora - timedelta(days=audit_days)
        incident_cutoff = ahora - timedelta(days=incident_days)

        incidentes_purgables = SecurityIncident.objects.filter(
            estado=SecurityIncident.Estado.RESUELTO,
            resuelto_en__lt=incident_cutoff,
        )

        # Preservamos incidentes activos siempre y, de los incidentes que
        # aún quedan por retención, conservamos sus eventos inicial/último.
        incidentes_retenidos = SecurityIncident.objects.exclude(
            Q(
                estado=SecurityIncident.Estado.RESUELTO,
                resuelto_en__lt=incident_cutoff,
            )
        )

        fingerprints_activos = SecurityIncident.objects.filter(
            estado__in=[
                SecurityIncident.Estado.ABIERTO,
                SecurityIncident.Estado.RECONOCIDO,
            ]
        ).values("fingerprint")

        ids_evento_inicial_retenidos = incidentes_retenidos.filter(
            evento_inicial_id__isnull=False
        ).values("evento_inicial_id")

        ids_evento_ultimo_retenidos = incidentes_retenidos.filter(
            evento_ultimo_id__isnull=False
        ).values("evento_ultimo_id")

        auditoria_purgable = (
            AuditEvent.objects
            .filter(
                creado_en__lt=audit_cutoff,
            )
            .exclude(
                fingerprint__in=Subquery(
                    fingerprints_activos
                )
            )
            .exclude(
                pk__in=Subquery(
                    ids_evento_inicial_retenidos
                )
            )
            .exclude(
                pk__in=Subquery(
                    ids_evento_ultimo_retenidos
                )
            )
        )

        incident_count = incidentes_purgables.count()
        audit_count = auditoria_purgable.count()

        mode = "EJECUCIÓN" if options["execute"] else "SIMULACIÓN"

        self.stdout.write(
            self.style.WARNING(
                f"[{mode}] AuditEvent a eliminar: {audit_count} | "
                f"SecurityIncident resueltos a eliminar: {incident_count}"
            )
        )

        self.stdout.write(
            f"Audit cutoff: {audit_cutoff.isoformat()} | "
            f"Incident cutoff: {incident_cutoff.isoformat()}"
        )

        if not options["execute"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "No se eliminó nada. Usa --execute para aplicar la limpieza."
                )
            )
            return

        deleted_incidents = self._delete_batches(
            queryset=incidentes_purgables,
            batch_size=batch_size,
        )

        # Recalculamos el queryset después de borrar incidentes viejos para
        # que los Subquery de referencias retenidas reflejen el estado real.
        incidentes_retenidos = SecurityIncident.objects.exclude(
            Q(
                estado=SecurityIncident.Estado.RESUELTO,
                resuelto_en__lt=incident_cutoff,
            )
        )

        ids_evento_inicial_retenidos = incidentes_retenidos.filter(
            evento_inicial_id__isnull=False
        ).values("evento_inicial_id")

        ids_evento_ultimo_retenidos = incidentes_retenidos.filter(
            evento_ultimo_id__isnull=False
        ).values("evento_ultimo_id")

        auditoria_purgable = (
            AuditEvent.objects
            .filter(
                creado_en__lt=audit_cutoff,
            )
            .exclude(
                fingerprint__in=Subquery(
                    fingerprints_activos
                )
            )
            .exclude(
                pk__in=Subquery(
                    ids_evento_inicial_retenidos
                )
            )
            .exclude(
                pk__in=Subquery(
                    ids_evento_ultimo_retenidos
                )
            )
        )

        deleted_audit = self._delete_batches(
            queryset=auditoria_purgable,
            batch_size=batch_size,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Limpieza completada. "
                f"SecurityIncident eliminados: {deleted_incidents}. "
                f"AuditEvent eliminados: {deleted_audit}."
            )
        )

    @staticmethod
    def _delete_batches(*, queryset, batch_size):
        total = 0

        while True:
            ids = list(
                queryset
                .order_by("pk")
                .values_list("pk", flat=True)[:batch_size]
            )

            if not ids:
                break

            with transaction.atomic():
                deleted, _ = queryset.model.objects.filter(
                    pk__in=ids
                ).delete()

            total += deleted

        return total
