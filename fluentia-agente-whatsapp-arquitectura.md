# Agente conversacional de WhatsApp para reservas y consultas
### Arquitectura técnica, roadmap y planificación — fluentIA SL

> Documento de diseño. Objetivo: un backend de IA que conversa por WhatsApp en lenguaje natural, asigna citas, resuelve dudas de horario/precios y mantiene el coste de tokens bajo control.

---

## 0. La decisión que hay que tomar antes de programar: ¿RAG o no?

La tentación es montar un RAG porque suena a "lo que se hace ahora". Para este caso concreto **es casi siempre sobreingeniería**. El razonamiento:

| Tipo de información | Tamaño real | Mejor forma de servirla |
|---|---|---|
| Horarios, precios, servicios, políticas (cancelación, ubicación) | Cientos de tokens, estructurado | **System prompt cacheado** |
| Huecos disponibles, citas existentes | Cambia cada minuto | **Tool-calling contra la BD/calendario** |
| FAQ libre y extensa, catálogos de miles de ítems, documentos largos | Miles+ de tokens, no estructurado | RAG (solo aquí merece la pena) |

Puntos clave:

- **El RAG resuelve un problema que aquí no tienes.** RAG existe para cuando no te cabe el corpus en el contexto. La ficha de una peluquería o una clínica cabe entera y sobra. Meterla en el prompt es más barato (sin coste de *embeddings*, sin BD vectorial, sin latencia de *retrieval*), más fiable (no hay riesgo de recuperar el *chunk* equivocado) y más simple de operar.
- **La disponibilidad de citas NUNCA debe venir de una búsqueda semántica.** Tiene que ser un dato vivo y exacto. Si el agente "recuerda" por similitud que el martes hay hueco, te genera dobles reservas. Eso es una *function call* a una tabla con bloqueo, no un *vector search*.
- **RAG entra en escena más tarde**, en la versión productizada multi-tenant, y solo para los clientes que de verdad tengan una base de conocimiento grande. Diseña el sistema para poder enchufarlo después, pero no lo construyas el día uno.

**Conclusión:** MVP sin RAG. Contexto estático en prompt cacheado + tools para lo dinámico. RAG como módulo opcional en fase posterior.

---

## 1. ¿Agente "agéntico" libre o máquina de estados? (importa para coste y fiabilidad)

Hay dos extremos y la respuesta correcta es un híbrido inclinado hacia el control:

- **Agente libre puro** (bucle de razonamiento + tools sin restricciones): flexible, pero impredecible, caro en tokens y propenso a alucinar pasos en una transacción crítica como una reserva.
- **Máquina de estados pura**: barata y fiable, pero rígida y con conversaciones que suenan a robot.

**Recomendación:** una **máquina de estados que gobierna el flujo de la reserva**, con el LLM confinado a tareas acotadas: entender la intención (NLU), extraer entidades (servicio, fecha, hora) y redactar la respuesta en lenguaje natural. El LLM *no conduce* la transacción; la valida y la verbaliza. Esto reduce tokens y elimina la mayoría de fallos de fiabilidad.

```
Estados del flujo:
  IDLE
   ├─ intención: CONSULTA  → responder desde contexto/tools → IDLE
   ├─ intención: RESERVAR  → COLLECTING_SERVICE
   │                          → COLLECTING_DATETIME (check_availability)
   │                          → COLLECTING_CONTACT
   │                          → CONFIRMING
   │                          → book_appointment() → CONFIRMED → IDLE
   ├─ intención: CANCELAR/MODIFICAR → MANAGE_BOOKING
   └─ intención: NO_RESUELTA / enfado / urgencia → HUMAN_HANDOFF
```

El LLM en cada estado solo tiene un trabajo pequeño (clasificar, extraer un campo, confirmar). Eso es lo que mantiene el coste bajo y el comportamiento predecible.

---

## 2. Arquitectura técnica

Alineada con tu stack habitual (Python/FastAPI, PostgreSQL, Hetzner, Claude API).

```
WhatsApp (usuario)
        │
        ▼
[Meta WhatsApp Cloud API]  ──webhook──►  [FastAPI: /webhook]
                                              │
                                              ▼
                                   ┌─────────────────────────┐
                                   │  Orquestador (FSM)       │
                                   │  - carga sesión (Redis)  │
                                   │  - router de intención   │
                                   │  - gestiona estado        │
                                   └───────────┬─────────────┘
                                               │
                 ┌─────────────────────────────┼─────────────────────────────┐
                 ▼                             ▼                             ▼
        [Claude API]                  [Capa de Tools]              [Plantillas/respuestas]
   - Haiku: intención/NLU             check_availability()         - mensajes salientes
   - Sonnet: casos complejos          book_appointment()
   - prompt caching ON                cancel/reschedule()
                                      get_pricing() [opcional]
                                               │
                                               ▼
                                   [PostgreSQL]  ──  servicios, citas,
                                   clientes, sesiones, logs/observabilidad
```

**Componentes:**

- **FastAPI** — recibe el webhook de Meta, verifica firma, encola/procesa el mensaje, devuelve `200` rápido (Meta reintenta si tardas).
- **Redis** (opcional pero recomendado) — estado de sesión y ventana de conversación. Evita reconstruir todo el contexto en cada turno.
- **PostgreSQL** — fuente de verdad de servicios, citas y clientes. Aquí viven los *constraints* que impiden dobles reservas.
- **Capa de Tools** — funciones Python deterministas que el LLM invoca vía *tool use*. Toda la lógica de negocio crítica vive aquí, no en el prompt.
- **Claude API** — Haiku para clasificación/NLU rutinaria, Sonnet solo cuando hace falta razonamiento. *Prompt caching* activado sobre el bloque de contexto del negocio.

---

## 3. Las tools (function calling)

Define el contrato del agente con el mundo. La info estática (horario base, precios) puede ir en el prompt; lo demás son tools:

```python
check_availability(service_id: str, date_from: date, date_to: date) -> list[Slot]
book_appointment(service_id: str, slot: datetime, customer: Customer,
                 idempotency_key: str) -> Booking
cancel_appointment(booking_id: str) -> bool
reschedule_appointment(booking_id: str, new_slot: datetime) -> Booking
# get_pricing / get_hours: solo como tool si el catálogo es grande;
# si es pequeño, va directo en el system prompt.
```

**Dos cosas que no se pueden omitir en la reserva:**

1. **Idempotencia** — Meta reentrega webhooks. Usa el `message_id` de WhatsApp como `idempotency_key` para no crear la misma cita dos veces.
2. **Concurrencia** — dos personas pidiendo el mismo hueco a la vez. Resuélvelo en la BD: `UNIQUE (resource_id, slot)` + `SELECT ... FOR UPDATE` o *advisory lock* al confirmar. Nunca confíes en que "el agente lo controle".

---

## 4. Estrategia de coste de tokens (el núcleo de tu pregunta)

Por orden de impacto:

1. **Prompt caching.** El bloque de contexto del negocio (horario, servicios, precios, tono) es idéntico en cada turno. Cacheado, las lecturas cuestan una fracción del precio normal. Es la palanca número uno y casi gratis de implementar.
2. **Routing de modelos.** Clasificación de intención y respuestas rutinarias → Haiku. Solo escalas a Sonnet cuando la consulta es ambigua o multi-paso. La mayoría de mensajes son rutinarios.
3. **Máquina de estados (sección 1).** Al acotar el trabajo del LLM por estado, cada llamada lleva el mínimo de contexto y de tokens de salida.
4. **Gestión de la ventana de conversación.** No reenvíes el historial completo en cada turno. Ventana deslizante (últimos N turnos) o resumen comprimido del estado de la conversación. Una reserva no necesita recordar el saludo de hace 8 mensajes.
5. **Cortar antes de llamar al LLM cuando se pueda.** Saludos, "gracias", confirmaciones de un solo carácter → respuestas plantilla o reglas, sin pasar por el modelo.

**Orden de magnitud:** con caching + Haiku para lo rutinario, una conversación de reserva completa (5-10 turnos) cuesta del orden de céntimos. El coste por conversación deja de ser tu cuello de botella; lo será la fiabilidad y el alta de clientes.

---

## 5. Integración con WhatsApp (lo que sí o sí debes saber)

- **Proveedor.** La **Cloud API de Meta** (directa) es la más barata. Twilio añade *markup*; un BSP europeo como 360dialog es buena opción si quieres soporte/RGPD en la UE. Empieza con la Cloud API.
- **Ventana de 24 h.** Puedes responder libremente dentro de las 24 h desde el último mensaje del usuario. Fuera de esa ventana (recordatorios proactivos de cita) necesitas **plantillas aprobadas** por Meta, que se facturan por conversación. Tenlo en cuenta para los recordatorios.
- **Webhooks.** Verificación del *token*, manejo de *callbacks* de estado (entregado/leído) y responder `200` rápido para que Meta no reintente.

---

## 6. Esquema de datos (mínimo viable)

```sql
businesses      (id, name, timezone, system_context, ...)   -- multi-tenant ya desde el día 1
services        (id, business_id, name, duration_min, price)
resources       (id, business_id, name)                     -- silla/box/profesional
appointments    (id, business_id, service_id, resource_id,
                 slot tstamptz, customer_id, status,
                 UNIQUE(resource_id, slot))                  -- antidoble-reserva
customers       (id, business_id, phone, name, consent_at)  -- RGPD: consentimiento
conversations   (id, business_id, customer_phone, state, window jsonb, updated_at)
events_log      (id, business_id, type, payload jsonb, ts)  -- observabilidad/coste
```

Diseñar `business_id` desde el principio te ahorra una migración dolorosa cuando productices el copiloto multi-tenant.

---

## 7. Roadmap por fases

### Fase 0 — Fundaciones
- Cuenta de WhatsApp Business + número en *sandbox* de la Cloud API.
- Esqueleto FastAPI con `/webhook` (verificación + eco).
- Esquema de BD y despliegue básico en Hetzner.
- **Entregable:** el bot recibe y responde un mensaje fijo.

### Fase 1 — Q&A (sin reservas, sin RAG)
- Contexto del negocio en system prompt + prompt caching.
- Responde horario, precios, servicios, ubicación en lenguaje natural.
- **Entregable:** demo real respondiendo dudas de *un* negocio. Esto ya es vendible/enseñable.

### Fase 2 — Reservas
- Tools `check_availability` / `book_appointment` contra la BD.
- Máquina de estados del flujo de reserva.
- Idempotencia + control de concurrencia.
- Cancelar/reprogramar.
- **Entregable:** ciclo completo de reserva por chat, sin dobles citas.

### Fase 3 — Optimización de coste
- Routing Haiku/Sonnet.
- Ventana de conversación / resumen.
- Cortes pre-LLM (saludos, confirmaciones).
- **Entregable:** coste por conversación medido y bajo control.

### Fase 4 — Producción
- *Human handoff* (avisar al negocio cuando el agente no resuelve o detecta enfado/urgencia).
- Recordatorios de cita (plantillas Meta).
- Observabilidad: logs de coste por conversación, *dashboard* básico, alertas de error.
- Manejo de errores y reintentos.
- **Entregable:** desplegable con un cliente real sin vigilancia constante.

### Fase 5 — Productización multi-tenant (el copiloto)
- Panel de alta de negocio (carga su contexto, servicios, horario).
- RAG **opcional** por cliente, solo si su base de conocimiento es grande.
- Aislamiento por `business_id`, facturación, onboarding self-service.
- **Entregable:** producto replicable, no proyecto a medida.

---

## 8. Planificación temporal (ritmo part-time)

Estimación honesta asumiendo dedicación parcial (Appian full-time desde septiembre). Son semanas de calendario, no de esfuerzo continuo:

| Fase | Esfuerzo orientativo | Hito |
|---|---|---|
| 0 — Fundaciones | 1 semana | Webhook vivo |
| 1 — Q&A | 1–2 semanas | **Demo enseñable a clientes** |
| 2 — Reservas | 2–3 semanas | Reserva end-to-end fiable |
| 3 — Coste | ~1 semana | Coste medido y bajo |
| 4 — Producción | 2 semanas | Primer cliente real desplegado |
| 5 — Multi-tenant | 3–4 semanas | Producto replicable |

**Atajo comercial:** al final de la Fase 1 ya tienes una demo que vender. No esperes a la Fase 5 para enseñarlo. Un bot respondiendo dudas de *su* peluquería convence más que cualquier presentación.

---

## 9. Consideraciones de producción

- **RGPD.** Manejas datos personales (teléfono, nombre, citas). Registra consentimiento, define retención y ten un DPA con cada proveedor. La API de Anthropic es de EE. UU.: valora la transferencia internacional y, si un cliente lo exige, ten preparado un *fallback* a un modelo con residencia/DPA europeo (Mistral, etc.) para las partes sensibles.
- **Fiabilidad sobre flexibilidad.** En una reserva, prefiere preguntar de más a confirmar de menos. Un "¿confirmo entonces martes a las 17:00?" explícito evita el 90% de las quejas.
- **Handoff humano.** Define disparadores claros (no resuelve en N turnos, palabras de enfado, urgencia) y notifica al negocio. Un agente que sabe cuándo callarse genera más confianza que uno que improvisa.
- **Observabilidad de coste.** Loguea tokens y coste por conversación desde el día uno. Sin esa métrica, "optimizar tokens" es a ciegas.

---

## 10. Riesgos y cómo mitigarlos

| Riesgo | Mitigación |
|---|---|
| Dobles reservas | `UNIQUE` + bloqueo en BD, no en el LLM |
| Webhooks duplicados | Idempotencia por `message_id` |
| Coste descontrolado | Caching + Haiku + ventana + métrica desde el inicio |
| Alucinación en transacción | LLM acotado por la máquina de estados; tools deterministas |
| RGPD / transferencia de datos | Consentimiento, DPA, *fallback* a modelo UE para datos sensibles |
| Sobreingeniería (RAG prematuro) | No construir RAG hasta que un cliente real lo justifique |

---

### TL;DR técnico

No es un RAG. Es **contexto estático cacheado + tool-calling contra una BD viva + máquina de estados que confina el LLM a NLU y redacción**. Eso te da lenguaje natural, reservas fiables y coste por conversación de céntimos. El RAG queda como módulo opcional para la versión productizada, y solo cuando un cliente tenga una base de conocimiento que de verdad lo pida.
