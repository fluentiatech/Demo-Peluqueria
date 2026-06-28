"""NLU: clasifica la intención y extrae entidades de un mensaje del cliente.

Una sola llamada al LLM por turno. El modelo NO decide la transacción: solo
devuelve datos estructurados que la máquina de estados valida e interpreta.
"""
from __future__ import annotations

import enum
from datetime import date

from app.agent.llm import Extraction, LLMClient
from app.config import settings


class Intent(enum.StrEnum):
    GREETING = "greeting"        # saludo/charla
    QUESTION = "question"        # duda de horario/precio/servicios
    BOOK = "book"                # quiere reservar
    CANCEL = "cancel"            # cancelar una cita
    RESCHEDULE = "reschedule"    # cambiar una cita
    CONFIRM = "confirm"          # "sí, confirmo"
    DENY = "deny"                # "no"
    CHOOSE = "choose"            # elige un servicio/hueco
    PROVIDE_NAME = "provide_name"
    HANDOFF = "handoff"          # enfado/urgencia/"hablar con persona"
    OTHER = "other"


_VALID = {i.value for i in Intent}

_SYSTEM = """\
Eres el módulo NLU de un asistente de reservas de citas. Clasificas el ÚLTIMO
mensaje del cliente y extraes entidades. Respondes SIEMPRE con un único objeto
JSON válido, sin texto adicional, con EXACTAMENTE estas claves:

  intent: una de [greeting, question, book, cancel, reschedule, confirm, deny,
                  choose, provide_name, handoff, other]
  service: nombre EXACTO del catálogo que mejor encaje con lo que pide, o null
  date: "YYYY-MM-DD" resolviendo expresiones relativas (hoy, mañana, "el viernes",
        "la semana que viene") con la fecha de hoy indicada, o null
  time: "HH:MM" en 24h si menciona una hora ("a las 5" de tarde => "17:00"), o null
  choice_index: número (1..N) si elige una opción de una lista numerada, o null
  professional: nombre EXACTO del profesional del listado si lo pide; "any" si le
                da igual / cualquiera / el que sea; o null si no lo menciona
  name: nombre propio del CLIENTE si lo facilita, o null
  question: si intent=question, el texto de la duda; si no, null

Criterios de intención (elige el más específico):
- book: quiere pedir/reservar una cita. Extrae service/date/time si aparecen.
- cancel: quiere anular una cita existente.
- reschedule: quiere cambiar/mover una cita existente.
- confirm: aceptación ("sí", "vale", "perfecto", "confirmo", "ok", "👍").
- deny: rechazo o negativa ("no", "mejor no", "cancela eso").
- choose: solo selecciona de una lista o indica una hora/servicio concreto sin
          otra intención (p. ej. "la 2", "el de las 17:00").
- provide_name: el mensaje es básicamente un nombre propio.
- question: duda sobre horario, precio, servicios, ubicación o políticas.
- greeting: saludo o charla sin petición.
- handoff: enfado, urgencia, queja seria o pedir hablar con una persona.
- other: nada de lo anterior.

Reglas:
- No inventes servicios: usa un nombre del catálogo SOLO si coincide razonablemente;
  si no, service=null.
- Un mismo mensaje puede traer varias entidades ("corte mañana a las 10") => intent
  book, service="Corte", date=la de mañana, time="10:00".
- Devuelve null (no cadenas vacías) cuando no hay dato.

Ejemplos:
- "hola buenas" => {"intent":"greeting","service":null,"date":null,"time":null,
  "choice_index":null,"name":null,"question":null}
- "¿cuánto vale el tinte?" => {"intent":"question","service":"Tinte","date":null,
  "time":null,"choice_index":null,"name":null,"question":"¿cuánto vale el tinte?"}
- "quiero corte el viernes por la tarde" => {"intent":"book","service":"Corte",
  "date":"<viernes>","time":null,"choice_index":null,"name":null,"question":null}
- "la 2" => {"intent":"choose","service":null,"date":null,"time":null,
  "choice_index":2,"name":null,"question":null}
"""


async def classify(
    llm: LLMClient,
    *,
    services: list[str],
    state: str,
    text: str,
    today: date,
    professionals: list[str] | None = None,
    model: str | None = None,
) -> Extraction:
    # El catálogo va en el SYSTEM (prefijo estable por negocio entre turnos), para
    # que la caché de prompt de OpenAI pueda reutilizarlo. En el USER solo lo que
    # cambia cada turno (fecha, estado, mensaje): mínimo de tokens variables.
    catalog = ", ".join(services) if services else "(sin servicios)"
    pros = ", ".join(professionals) if professionals else "(sin profesionales)"
    system = (
        f"{_SYSTEM}\n\n# Catálogo de servicios del negocio\n{catalog}"
        f"\n\n# Profesionales del negocio\n{pros}"
    )
    user = (
        f"Hoy es {today.isoformat()} (día de la semana {today.weekday()}, 0=lunes).\n"
        f"Estado de la conversación: {state}.\n"
        f'Mensaje del cliente: """{text}"""'
    )
    # El modelo más barato disponible: es la llamada más frecuente.
    used_model = model or settings.openai_model_nlu or settings.openai_model_fast
    extraction = await llm.extract(system=system, user=user, model=used_model)
    # Normaliza el intent a un valor válido.
    intent = str(extraction.data.get("intent", "other"))
    if intent not in _VALID:
        extraction.data["intent"] = Intent.OTHER.value
    return extraction
