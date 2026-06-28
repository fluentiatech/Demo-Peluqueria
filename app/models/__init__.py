"""Registro de modelos. Importar desde aquí garantiza que el metadata los conoce."""
from app.models.appointment import Appointment, AppointmentStatus
from app.models.associations import service_resource
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.business import Business
from app.models.closure import BusinessClosure
from app.models.conversation import Conversation, ConversationState
from app.models.customer import Customer
from app.models.event_log import EventLog
from app.models.inbound_message import InboundMessage
from app.models.resource import Resource
from app.models.service import Service
from app.models.time_off import TimeOff
from app.models.waitlist import WaitlistEntry, WaitlistStatus

__all__ = [
    "Appointment",
    "AppointmentStatus",
    "AuditLog",
    "Base",
    "Business",
    "BusinessClosure",
    "Conversation",
    "ConversationState",
    "Customer",
    "EventLog",
    "InboundMessage",
    "Resource",
    "Service",
    "TimeOff",
    "WaitlistEntry",
    "WaitlistStatus",
    "service_resource",
]
