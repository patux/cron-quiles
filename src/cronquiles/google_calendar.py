"""
Módulo para publicar eventos directamente en Google Calendar.

Requiere autenticación OAuth2 de Google.
"""

import logging
import os
from datetime import datetime
from typing import List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .ics_aggregator import EventNormalized

logger = logging.getLogger(__name__)

# Scopes necesarios para Google Calendar API
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class GoogleCalendarPublisher:
    """Publicador de eventos en Google Calendar."""

    def __init__(
        self,
        credentials_file: str = "config/credentials.json",
        token_file: str = "config/token.json",
        calendar_id: str = "primary",
    ):
        """
        Inicializa el publicador de Google Calendar.

        Args:
            credentials_file: Ruta al archivo de credenciales OAuth2
            token_file: Ruta donde guardar/leer el token de acceso
            calendar_id: ID del calendario donde publicar (default: "primary")
        """
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.calendar_id = calendar_id
        self.service = None

    def authenticate(self) -> bool:
        """
        Autentica con Google Calendar API usando OAuth2.

        Returns:
            True si la autenticación fue exitosa, False en caso contrario
        """
        creds = None

        # Cargar token existente si existe
        if os.path.exists(self.token_file):
            try:
                creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
            except Exception as e:
                logger.warning(f"Error cargando token: {e}")

        # Si no hay credenciales válidas, solicitar autorización
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    logger.error(f"Error refrescando token: {e}")
                    creds = None

            if not creds:
                if not os.path.exists(self.credentials_file):
                    logger.error(
                        f"Archivo de credenciales no encontrado: {self.credentials_file}"
                    )
                    logger.info(
                        "Por favor, descarga las credenciales OAuth2 desde "
                        "Google Cloud Console y guárdalas en config/credentials.json"
                    )
                    return False

                try:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.credentials_file, SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                except Exception as e:
                    logger.error(f"Error en flujo OAuth2: {e}")
                    return False

            # Guardar credenciales para la próxima vez
            try:
                os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
                with open(self.token_file, "w") as token:
                    token.write(creds.to_json())
            except Exception as e:
                logger.warning(f"Error guardando token: {e}")

        try:
            self.service = build("calendar", "v3", credentials=creds)
            logger.info("✓ Autenticación con Google Calendar exitosa")
            return True
        except Exception as e:
            logger.error(f"Error construyendo servicio de Google Calendar: {e}")
            return False

    def event_to_google_format(self, event: EventNormalized) -> dict:
        """
        Convierte un EventNormalized a formato de Google Calendar API.

        Args:
            event: Evento normalizado

        Returns:
            Diccionario en formato de Google Calendar API
        """
        google_event = {
            "summary": str(event.original_event.get("summary", "")),
        }

        # Descripción
        if event.description:
            google_event["description"] = event.description
            if event.url:
                google_event["description"] += f"\n\nURL: {event.url}"
            if event.source_url:
                google_event["description"] += f"\nFuente: {event.source_url}"

        # Fechas
        if event.dtstart:
            google_event["start"] = {
                "dateTime": event.dtstart.isoformat(),
                "timeZone": (
                    str(event.dtstart.tzinfo) if event.dtstart.tzinfo else "UTC"
                ),
            }
        if event.dtend:
            google_event["end"] = {
                "dateTime": event.dtend.isoformat(),
                "timeZone": str(event.dtend.tzinfo) if event.dtend.tzinfo else "UTC",
            }

        # Ubicación
        if event.location:
            google_event["location"] = event.location

        # URL del evento
        if event.url:
            google_event["source"] = {"url": event.url, "title": "Ver evento"}

        # Tags como color (opcional)
        if event.tags:
            # Mapear algunos tags a colores de Google Calendar
            color_map = {
                "python": "6",  # Naranja
                "ai": "9",  # Azul
                "cloud": "1",  # Lavanda
                "devops": "5",  # Amarillo
                "data": "10",  # Verde
                "security": "11",  # Rojo
            }
            for tag in event.tags:
                if tag in color_map:
                    google_event["colorId"] = color_map[tag]
                    break

        return google_event

    def publish_event(self, event: EventNormalized) -> Optional[str]:
        """
        Publica un evento en Google Calendar.

        Args:
            event: Evento normalizado a publicar

        Returns:
            ID del evento creado en Google Calendar, o None si falla
        """
        if not self.service:
            logger.error("Servicio de Google Calendar no inicializado")
            return None

        try:
            google_event = self.event_to_google_format(event)
            created_event = (
                self.service.events()
                .insert(calendarId=self.calendar_id, body=google_event)
                .execute()
            )
            event_id = created_event.get("id")
            logger.debug(
                f"Evento publicado: {google_event['summary']} (ID: {event_id})"
            )
            return event_id
        except HttpError as e:
            logger.error(
                f"Error publicando evento '{event.original_event.get('summary', '')}': {e}"
            )
            return None
        except Exception as e:
            logger.error(f"Error inesperado publicando evento: {e}")
            return None

    def publish_events(
        self, events: List[EventNormalized], dry_run: bool = False
    ) -> dict:
        """
        Publica múltiples eventos en Google Calendar.

        Args:
            events: Lista de eventos normalizados
            dry_run: Si es True, solo simula sin publicar realmente

        Returns:
            Diccionario con estadísticas: {'success': int, 'failed': int, 'skipped': int}
        """
        if not self.service and not dry_run:
            logger.error("Servicio de Google Calendar no inicializado")
            return {"success": 0, "failed": 0, "skipped": len(events)}

        stats = {"success": 0, "failed": 0, "skipped": 0}

        if dry_run:
            logger.info(f"[DRY RUN] Simulando publicación de {len(events)} eventos")
            for event in events:
                google_event = self.event_to_google_format(event)
                logger.debug(f"[DRY RUN] Publicaría: {google_event['summary']}")
                stats["success"] += 1
            return stats

        logger.info(f"Publicando {len(events)} eventos en Google Calendar...")

        for i, event in enumerate(events, 1):
            event_id = self.publish_event(event)
            if event_id:
                stats["success"] += 1
            else:
                stats["failed"] += 1

            # Log de progreso cada 10 eventos
            if i % 10 == 0:
                logger.info(f"Progreso: {i}/{len(events)} eventos procesados")

        logger.info(
            f"Publicación completada: {stats['success']} exitosos, "
            f"{stats['failed']} fallidos"
        )

        return stats

    def clear_calendar(self, confirm: bool = False) -> bool:
        """
        Limpia todos los eventos del calendario (¡CUIDADO!).

        Args:
            confirm: Debe ser True para confirmar la acción

        Returns:
            True si se limpió exitosamente, False en caso contrario
        """
        if not confirm:
            logger.error("Debes confirmar la limpieza del calendario")
            return False

        if not self.service:
            logger.error("Servicio de Google Calendar no inicializado")
            return False

        try:
            # Obtener todos los eventos
            events_result = (
                self.service.events()
                .list(calendarId=self.calendar_id, maxResults=2500)
                .execute()
            )
            events = events_result.get("items", [])

            logger.warning(f"Eliminando {len(events)} eventos del calendario...")

            for event in events:
                self.service.events().delete(
                    calendarId=self.calendar_id, eventId=event["id"]
                ).execute()

            logger.info("✓ Calendario limpiado exitosamente")
            return True
        except Exception as e:
            logger.error(f"Error limpiando calendario: {e}")
            return False
