"""Capa de tools (function calling): lógica de negocio determinista.

Toda la lógica crítica de reservas vive aquí, no en el prompt del LLM.
El agente sólo invoca estas funciones; nunca decide él la transacción.
"""
from app.tools.availability import check_availability, free_resources_at
from app.tools.booking import (
    BookingError,
    OutOfHoursError,
    SlotTakenError,
    book_appointment,
    cancel_appointment,
    reschedule_appointment,
)
from app.tools.capacity import (
    assign_service_resources,
    list_service_resources,
)
from app.tools.pricing import get_pricing, list_services
from app.tools.waitlist import (
    add_to_waitlist,
    list_waitlist,
    match_for_freed_slot,
    remove_from_waitlist,
)

__all__ = [
    "BookingError",
    "OutOfHoursError",
    "SlotTakenError",
    "add_to_waitlist",
    "assign_service_resources",
    "book_appointment",
    "cancel_appointment",
    "check_availability",
    "free_resources_at",
    "get_pricing",
    "list_service_resources",
    "list_services",
    "list_waitlist",
    "match_for_freed_slot",
    "remove_from_waitlist",
    "reschedule_appointment",
]
