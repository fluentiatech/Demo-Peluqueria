"""Zona horaria del negocio (España por defecto), consciente del cambio de hora.

Las citas y la disponibilidad se manejan SIEMPRE en la hora local del negocio:
"las 9" son las 9 en España tanto en verano (CEST, +02:00) como en invierno
(CET, +01:00). `ZoneInfo` resuelve el desfase correcto según la fecha, así que el
cambio de hora se aplica solo.

Las marcas de sistema (created_at, auditoría, métricas) siguen en UTC aparte.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.config import settings


def tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def now() -> datetime:
    """Hora actual con la zona del negocio (consciente de DST)."""
    return datetime.now(tz())


def local(d: date, t: time) -> datetime:
    """Combina fecha + hora como hora local del negocio (con zona)."""
    return datetime.combine(d, t, tzinfo=tz())


def aware(dt: datetime) -> datetime:
    """Asegura zona local del negocio si el datetime viene sin zona (naive)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=tz())


def to_local(dt: datetime) -> datetime:
    """Pasa cualquier datetime a hora local del negocio (para mostrarlo).

    Los datetime leídos de la BD vuelven en UTC; hay que convertirlos a España
    antes de formatear, o "las 9" se verían como "las 7".
    """
    return dt.astimezone(tz()) if dt.tzinfo is not None else dt.replace(tzinfo=tz())
