"""Plantillas de respuesta del flujo de reserva.

El texto transaccional es determinista (no lo genera el LLM): más fiable y
testeable. El LLM solo redacta las respuestas de Q&A libre.
"""
from __future__ import annotations

from datetime import datetime

from app import timez

_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def fmt_slot(dt: datetime) -> str:
    # Siempre en hora de España (los datetime de BD vuelven en UTC).
    dt = timez.to_local(dt)
    return f"{_DAYS[dt.weekday()]} {dt.day:02d}/{dt.month:02d} a las {dt:%H:%M}"


def _first_name(name: str | None) -> str | None:
    """Primer nombre, para un trato cercano sin soltar el apellido completo."""
    if not name:
        return None
    first = name.strip().split()[0] if name.strip() else ""
    return first or None


def greeting(
    name: str | None = None,
    assistant_name: str | None = None,
    use_emojis: bool = False,
) -> str:
    """Saludo inicial; usa el nombre del cliente y el del asistente si los hay."""
    first = _first_name(name)
    wave = "👋 " if use_emojis else ""
    hola = f"¡Hola, {first}!" if first else "¡Hola!"
    intro = f" Soy {assistant_name}." if assistant_name else ""
    return f"{wave}{hola}{intro} ¿Quieres pedir cita o tienes alguna duda?"


def ask_service(services: list[str]) -> str:
    catalogo = "\n".join(f"· {s}" for s in services)
    return f"¿Para qué servicio quieres la cita?\n{catalogo}"


def service_not_found(services: list[str]) -> str:
    catalogo = ", ".join(services)
    return f"No reconozco ese servicio. Ofrecemos: {catalogo}. ¿Cuál te interesa?"


def ask_professional(names: list[str]) -> str:
    lista = "\n".join(f"· {n}" for n in names)
    return f"¿Con qué profesional la quieres?\n{lista}\n(o dime «me da igual»)"


def professional_not_found(names: list[str]) -> str:
    return f"No reconozco a ese profesional. Tenemos a: {', '.join(names)}. ¿Con quién?"


def ask_date(service_name: str) -> str:
    return f"Perfecto, {service_name}. ¿Qué día te viene bien?"


def no_slots() -> str:
    return "No encuentro huecos para esas fechas. ¿Quieres probar otro día?"


def ask_waitlist(service_name: str) -> str:
    return (
        f"Ahora mismo no me quedan huecos de {service_name}. "
        "¿Quieres que te avise en cuanto se libere uno? (sí / no)"
    )


def waitlist_added(service_name: str) -> str:
    return (
        f"¡Hecho! Te aviso en cuanto se libere un hueco de {service_name}. "
        "En cuanto pase, te escribo y con un «sí» te lo reservo."
    )


def offer_slots(slots: list[str]) -> str:
    opciones = "\n".join(f"{i}. {s}" for i, s in enumerate(slots, 1))
    return f"Estos son los huecos disponibles:\n{opciones}\n¿Cuál prefieres?"


def ask_choice_again() -> str:
    return "No me ha quedado claro. Dime el número de la opción o la hora que prefieres."


def ask_name() -> str:
    return "¿A nombre de quién pongo la cita?"


def confirm_booking(service_name: str, when: str) -> str:
    return f"¿Confirmo entonces {service_name} el {when}? (sí / no)"


def booking_done(service_name: str, when: str, name: str | None = None) -> str:
    first = _first_name(name)
    saludo = f"¡Listo, {first}!" if first else "¡Listo!"
    return f"{saludo} Tu cita de {service_name} queda el {when}. ¡Te esperamos!"


def slot_taken() -> str:
    return "Vaya, ese hueco se acaba de ocupar. Te busco otros disponibles."


def confirm_cancel(service_name: str, when: str) -> str:
    return f"¿Confirmas que cancelo tu cita de {service_name} del {when}? (sí / no)"


def cancel_done() -> str:
    return "Hecho, tu cita queda cancelada. Aquí estamos cuando quieras volver."


def no_appointments() -> str:
    return "No veo ninguna cita próxima a tu nombre. ¿Quieres reservar una?"


def reschedule_ask_date(service_name: str, when: str) -> str:
    return (
        f"Tu cita actual es {service_name} el {when}. "
        "¿Para qué nuevo día y hora la quieres?"
    )


def reschedule_ask_professional(
    service_name: str, when: str, current: str | None, names: list[str]
) -> str:
    ahora = f" (ahora con {current})" if current else ""
    lista = "\n".join(f"· {n}" for n in names)
    return (
        f"Tu cita actual es {service_name} el {when}{ahora}.\n"
        f"¿Con qué profesional la quieres ahora?\n{lista}\n"
        "(o dime «me da igual»)"
    )


def reschedule_done(service_name: str, when: str) -> str:
    return f"Cambiada: {service_name} pasa al {when}. ¡Gracias!"


def aborted() -> str:
    return "Vale, lo dejamos. Si necesitas algo más, aquí estoy."


def handoff() -> str:
    return "Te paso con una persona del equipo, que te atenderá enseguida."


def fallback() -> str:
    return "Perdona, no te he entendido. ¿Quieres reservar, consultar o cancelar una cita?"
