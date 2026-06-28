"""Validadores reutilizables de schemas (formato de horas, tramos)."""
from __future__ import annotations

import re

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _minutes(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def validate_intervals(tramos: list) -> list:
    """Valida una lista de tramos [["09:00","14:00"], ...] (HH:MM, inicio<fin)."""
    for tramo in tramos:
        if not isinstance(tramo, (list, tuple)) or len(tramo) != 2:
            raise ValueError("Cada tramo debe ser [inicio, fin]")
        start, end = tramo
        if not _HHMM.match(str(start)) or not _HHMM.match(str(end)):
            raise ValueError(f"Hora inválida (formato HH:MM): {tramo}")
        if _minutes(start) >= _minutes(end):
            raise ValueError(f"El inicio debe ser anterior al fin: {tramo}")
    return tramos


def no_control_chars(value: str | None) -> str | None:
    """Limpia texto libre: recorta y rechaza caracteres de control / NUL.

    Defensa en profundidad sobre PII de texto (nombre, notas): aunque la BD usa
    consultas parametrizadas (no inyectables), esto evita inyección en logs/
    cabeceras y datos basura. La longitud máxima la fija `Field(max_length=...)`.
    """
    if value is None:
        return None
    v = value.strip()
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in v):
        raise ValueError("El texto contiene caracteres de control no permitidos")
    return v or None


def validate_weekly_hours(hours: dict) -> dict:
    """Valida un horario semanal {"0": [["09:00","14:00"]], ...} (día 0..6)."""
    for day, tramos in hours.items():
        if str(day) not in {"0", "1", "2", "3", "4", "5", "6"}:
            raise ValueError(f"Día de la semana inválido: {day} (usa 0..6)")
        validate_intervals(tramos)
    return hours
