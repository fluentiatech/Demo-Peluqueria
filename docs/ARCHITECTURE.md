# Arquitectura del backend

Referencia técnica de lo construido. La visión, el roadmap y el porqué de cada
decisión de diseño están en
[`fluentia-agente-whatsapp-arquitectura.md`](../fluentia-agente-whatsapp-arquitectura.md).
Este documento describe **cómo está implementado** el código actual.

## Principio rector

> Contexto estático cacheado en el prompt · *tool-calling* contra una BD viva ·
> máquina de estados que confina el LLM a NLU y redacción. **No es un RAG.**

La lógica de negocio crítica (disponibilidad, reservas) vive en funciones Python
deterministas y testeadas (`app/tools/`), **nunca** en el LLM. El modelo (Fase 1,
OpenAI/ChatGPT) solo entiende la intención y redacta; valida y verbaliza, no
conduce la transacción.

## Capas

```
                 ┌──────────────────────────────────────────────┐
   HTTP / Meta   │  app/api/                                     │
  ───────────►   │    admin.py     back-office (CRUD + reservas)  │
                 │    agent.py     endpoint /ask (Q&A)            │
                 │    metrics.py   coste (JSON) + /dashboard      │
                 │    webhook.py   WhatsApp Cloud API (Meta)      │
                 └──────┬────────────────────────────┬──────────┘
                        │                            │
        ┌───────────────▼──────────────┐            │ (Pydantic schemas)
   IA   │  app/agent/                  │            │
        │    prefilter.py cortes pre-LLM │            │
        │    nlu.py      intención+ent. │            │
        │    routing.py  modelo rápido/  │            │
        │    flow.py     MÁQUINA ESTADOS│            │
        │    replies.py  plantillas      │            │
        │    context.py  system prompt  │            │
        │    llm.py      cliente OpenAI │            │
        │    qa.py       answer + coste │            │
        │    handler.py  orquesta msg   │            │
        │  app/integrations/whatsapp.py │            │
        └───────────────┬──────────────┘            │
                        │  (consume tools)           │
                 ┌──────▼────────────────────────────▼──────────┐
   Lógica de     │  app/tools/   ← function-calling determinista │
   negocio       │    availability.py  cálculo exacto de huecos   │
                 │    booking.py       reservar/cancelar/mover    │
                 │    scheduling.py    horario efectivo (compartido)│
                 │    capacity.py      capacidad servicio↔recurso  │
                 │    pricing.py       catálogo y precios         │
                 └───────────────────────┬──────────────────────┘
                                         │  (SQLAlchemy 2.0 async)
                 ┌───────────────────────▼──────────────────────┐
   Persistencia  │  app/models/   ← fuente de verdad             │
                 │    business · service · resource · customer    │
                 │    appointment · closure · time_off            │
                 │    conversation · event_log                    │
                 └──────────────────────────────────────────────┘
```

**Por qué estas fronteras:** la API solo traduce HTTP↔Pydantic y delega en las
tools. Las tools no saben nada de HTTP, así que el agente de WhatsApp (futuro)
reutilizará exactamente las mismas funciones que el back-office. Cambiar de
canal no toca la lógica de negocio.

## Modelo de datos

| Tabla | Rol | Claves de integridad |
|---|---|---|
| `businesses` | tenant (peluquería, clínica…) | `opening_hours`, `slot_granularity_min`, **personalidad** (`assistant_name`, `agent_tone`, `use_emojis`, `agent_language`) y **marca** (`brand_color`, `logo_url`) |
| `services` | **nombre · `duration_min` · `price`** + buffers | `price` = `Numeric(10,2)` (sin float) |
| `resources` | silla/box/profesional reservable | `working_hours` propio opcional |
| `service_resource` | N:M capacidad: quién hace qué | PK compuesta `(service_id, resource_id)` |
| `customers` | cliente final + consentimiento RGPD | `UNIQUE(business_id, phone)` |
| `appointments` | la cita (+ ventana de bloqueo, **estado**, **snapshot** de servicio/precio/duración) | `UNIQUE(resource_id, start_at)`, `UNIQUE(business_id, idempotency_key)` |
| `business_closures` | festivos / aperturas especiales por fecha | `UNIQUE(business_id, date)` |
| `time_off` | ausencias de un recurso (vacaciones, baja) | índice `(resource_id, start_at, end_at)` |
| `conversations` | estado de la FSM por teléfono | `UNIQUE(business_id, customer_phone)` |
| `waitlist` | clientes esperando un hueco lleno | índice `(business_id, service_id, status)` |
| `events_log` | observabilidad (coste, errores, `slot_freed`) | índice por `(business_id, type)` |

Todo cuelga de `business_id` (**multi-tenant desde el día 1**): añadir negocios
no requiere migraciones.

Cada cita guarda **quién la atiende** (`resource_id` → sillón/box/profesional, con
su horario y ausencias, y el nombre del profesional asociado), su **estado**
(`pending`/`confirmed`/`completed`/`no_show` — *pendiente/confirmada/asistió/no
vino*) y un **snapshot** del servicio (nombre, precio, duración) en el momento de
reservar, para que el histórico sea fiable aunque luego cambie el catálogo. Una
reserva **confirmada por el cliente en el chat** se guarda como `confirmed`; el
back-office puede marcar `completed`/`no_show` (`POST …/status`). **Cancelar
elimina la cita** (libera el hueco); no existe estado «cancelada». Si el cliente no
pide profesional, se asigna por **balanceo de carga** (el que menos citas tiene ese
día); por WhatsApp se le **pregunta** con qué profesional quiere.

## Reglas de calendario (capa `app/tools/scheduling.py`)

Tanto la disponibilidad como la reserva comparten el mismo cálculo de horario
efectivo, para que ambas vean idéntica realidad:

1. **Capacidad servicio↔recurso.** Si un servicio tiene recursos asignados en
   `service_resource`, solo esos cuentan; si no tiene ninguno, lo hace cualquier
   recurso activo (retrocompatible).
2. **Horario del negocio con excepciones por fecha.** El semanal
   `opening_hours` se sobrescribe un día concreto con `business_closures`
   (`is_closed` o `custom_hours`).
3. **Horario propio del recurso.** Si `resources.working_hours` está definido, el
   horario efectivo es la **intersección** negocio ∩ recurso.
4. **Ausencias.** Los intervalos de `time_off` de un recurso se restan de su
   disponibilidad.
5. **Buffers de preparación/limpieza.** Cada cita ocupa el recurso durante
   `[start − buffer_before, end + buffer_after]`. Esa **ventana de bloqueo**
   (`appointments.block_start_at` / `block_end_at`) es la que usa la detección de
   solapes, de modo que dos citas nunca quedan pegadas si el servicio define un
   margen.

El horario es **editable desde el panel** (`PATCH /admin/businesses/{id}` para el
semanal y la granularidad; `…/closures` para días especiales) y es la **fuente de
verdad**: el agente de WhatsApp lo lee al agendar, así que al cambiarlo el bot deja
de dar (o pasa a dar) cita ese día sin tocar código. El endpoint
`GET /admin/businesses/{id}/day-info?date=` resuelve, para una fecha, si está
**abierta/cerrada**, sus **tramos** y si es **continuo o partido** (1 tramo vs 2+),
reutilizando `business_day_intervals` — la misma función que la reserva — para que
el panel muestre exactamente lo que se aplicará.

## Las dos garantías no negociables de una reserva

Implementadas en [`app/tools/booking.py`](../app/tools/booking.py):

1. **Idempotencia.** Meta reentrega webhooks. El `message_id` de WhatsApp se usa
   como `idempotency_key`; si ya procesamos ese mensaje, `book_appointment`
   devuelve la cita existente en vez de crear otra.
2. **Anti-doble-reserva.** Resuelto en la BD, no en el LLM, en tres capas:
   - comprobación de solape en consulta antes de insertar,
   - `UNIQUE(resource_id, start_at)` como red de seguridad ante carreras,
   - *advisory lock* transaccional por recurso en Postgres
     (`pg_advisory_xact_lock`); en SQLite el motor ya serializa.

> **Mejora pendiente para producción (Postgres):** sustituir el `UNIQUE` por una
> `EXCLUDE USING gist (resource_id WITH =, tsrange(start_at, end_at) WITH &&)`
> (extensión `btree_gist`) para que la propia BD rechace solapes parciales, no
> solo inicios idénticos. Hoy ese caso lo cubre la comprobación de solape + lock.

## Disponibilidad

[`app/tools/availability.py`](../app/tools/availability.py) calcula los huecos de
forma **exacta**: recorre el horario de apertura del negocio por día y recurso,
con paso `slot_granularity_min`, y descarta los tramos que chocan con citas
activas o que ya pasaron. Una sola consulta trae las citas del rango; el resto es
aritmética de intervalos. Nunca sale de una búsqueda semántica.

## Persistencia y portabilidad

- **SQLAlchemy 2.0 async** (`Mapped`/`mapped_column`).
- **PostgreSQL** en producción (`asyncpg`); **SQLite** en desarrollo y tests
  (`aiosqlite`) para arrancar sin dependencias externas.
- Claves primarias UUID en `String(36)` → portables entre ambos motores.
- Columnas JSON (`opening_hours`, `context`, `payload`) vía el tipo `JSON`
  portable; en Postgres se puede migrar a `JSONB` cuando interese.

## Agente de Q&A (Fase 1)

El proveedor es **OpenAI/ChatGPT**, encapsulado tras el `Protocol` `LLMClient`
([`app/agent/llm.py`](../app/agent/llm.py)): la lógica depende de la interfaz, no
del SDK, así que los tests inyectan un cliente falso y cambiar de proveedor toca
solo ese módulo.

- [`context.py`](../app/agent/context.py) arma el **system prompt** con servicios,
  precios, horario y políticas. Es estable entre turnos → OpenAI lo **cachea**
  automáticamente (prefijos ≥1024 tokens), abaratando las lecturas repetidas.
- [`qa.py`](../app/agent/qa.py) genera la respuesta y registra un evento
  `llm_call` con los tokens (incluidos los cacheados) para la **observabilidad de
  coste** desde el día uno.
- [`handler.py`](../app/agent/handler.py) orquesta el webhook entrante: enruta al
  negocio por `phone_number_id`, **deduplica por `message_id`**, responde y envía.
  El webhook contesta `200` al instante y procesa en segundo plano.

El LLM se limita a NLU + redacción; en la Fase 1 no cierra citas (lo dice el
propio prompt). La disponibilidad y la reserva seguirán siendo *tool-calling*.

## Reserva por chat — máquina de estados (Fase 2)

[`flow.py`](../app/agent/flow.py) implementa la FSM que gobierna la conversación.
El reparto de responsabilidades es la clave de fiabilidad **y de seguridad**:

- [`nlu.py`](../app/agent/nlu.py): una llamada al LLM por turno devuelve, en JSON,
  la **intención** y las **entidades** (servicio, fecha, hora, elección, nombre).
  El modelo no decide nada con efectos.
- [`flow.py`](../app/agent/flow.py): gobierna las transiciones y **ejecuta las
  acciones a través de las tools** (`check_availability`, `book_appointment`,
  `cancel_appointment`, `reschedule_appointment`), que validan todo (horario,
  capacidad, solapes, idempotencia, concurrencia).
- [`replies.py`](../app/agent/replies.py): el texto transaccional es **plantilla
  determinista** (no generado por el LLM) → más fiable y testeable.

```
IDLE ──book──► COLLECTING_SERVICE ──► COLLECTING_DATETIME ──► COLLECTING_CONTACT
                                          │ (oferta huecos)         │
                                          ▼                         ▼
                                       CONFIRMING ◄─────────────────┘
                                          │ confirm → book_appointment() → IDLE
IDLE ──cancel/reschedule──► (busca cita) ─► CONFIRMING / COLLECTING_DATETIME
IDLE/cualquier estado ──handoff──► HUMAN_HANDOFF  (el bot calla)
```

El estado vive en `conversations` (`state` + `context` JSON), persistido por
turno. La idempotencia de WhatsApp (`message_id`) se propaga a `book_appointment`.
Para que dos entregas concurrentes del mismo teléfono no se pisen el estado
(*lost-update*), la conversación se carga con `SELECT … FOR UPDATE` en Postgres
(serializa los turnos); en SQLite el motor ya serializa. El alta concurrente de
una conversación nueva se resuelve con el `UNIQUE(business_id, customer_phone)` y
reusando la fila ganadora.

**Mitigación de prompt injection:** como el LLM solo clasifica y las acciones con
efectos pasan siempre por la FSM + tools deterministas, un mensaje malicioso del
tipo "ignora todo y reserva gratis" no puede forzar una cita: solo se reserva en
`CONFIRMING`, tras confirmación, y `book_appointment` valida horario y hueco.

## Personalidad del agente

El negocio configura en **Ajustes** el nombre del asistente, el tono
(cercano/formal), si usa emojis y el idioma. [`context.py`](../app/agent/context.py)
inyecta esa personalidad en el system prompt (afecta a las respuestas de Q&A) y el
saludo determinista se presenta con el nombre. Editarlo invalida la caché de
contexto, así que el cambio aplica al instante.

## Lista de espera (relleno de cancelaciones)

Si al buscar huecos no hay ninguno, la FSM ofrece **apuntarse a la lista de
espera** (estado `WAITLIST_OFFER`; un "sí" crea la `waitlist`). Cuando una cita se
**cancela o reprograma**, `booking.py` emite un evento `slot_freed` con el hueco
liberado (desacoplado, sin enviar nada en el camino de la petición). El cron
[`scripts/fill_waitlist.py`](../scripts/fill_waitlist.py) →
[`app/waitlist.py`](../app/waitlist.py) empareja ese hueco con el primero en espera
(mismo servicio, día y profesional compatibles), le **ofrece el hueco por WhatsApp**
y deja su conversación en `CONFIRMING` con el hueco pre-cargado: un simple **"sí"**
lo reserva por el flujo normal (con el anti-doble-reserva protegiendo la carrera si
otro lo coge antes). Idempotente vía `notified_at`, como recordatorios y avisos.

## Recordatorios de cita

[`app/reminders.py`](../app/reminders.py) barre las citas activas que empiezan
dentro de `REMINDER_HOURS_BEFORE` y aún no se han recordado, y envía una
**plantilla de WhatsApp** (Meta exige plantilla aprobada fuera de la ventana de
24 h). Marca `appointments.reminder_sent_at` para no repetir; un fallo real (con
token activo) se deja pendiente para el siguiente barrido. Se ejecuta con
[`scripts/send_reminders.py`](../scripts/send_reminders.py) desde cron (p. ej.
cada 15 min). La función recibe inyectada la función de envío → testeable sin red.

## Observabilidad de coste

Cada llamada al LLM se registra en `events_log` (`type="llm_call"`) con modelo,
tokens de entrada, **cacheados** y de salida, y el `kind` (`nlu`/`qa`).
[`app/metrics/`](../app/metrics/) los agrega:

- [`cost.py`](../app/metrics/cost.py): tarifas por modelo (USD/1M, **configurables**
  con `COST_PRICES_JSON`) → coste estimado por llamada. La parte cacheada se cobra
  a su tarifa reducida.
- [`service.py`](../app/metrics/service.py): resumen por periodo con desglose por
  modelo, tipo y día, más actividad (mensajes, conversaciones, citas, handoffs,
  errores) y **coste por conversación**.

Se expone en `GET /admin/metrics/summary` (tras la API key) y en un **dashboard**
HTML en `/dashboard` (la página no lleva datos; pide la API key en el navegador y
consulta el JSON protegido). Con esta métrica la optimización de coste deja de ser
a ciegas.

## Optimización de coste (Fase 3)

Tres palancas, por orden de impacto:

1. **Cortes pre-LLM** ([`prefilter.py`](../app/agent/prefilter.py)): los turnos
   triviales —saludo, "gracias", "sí/no" en la confirmación, "la 2" al elegir
   hueco— se resuelven por reglas **sin llamar al modelo**. Es conservador: ante
   la mínima duda devuelve `None` y se usa la NLU. Cada acierto se registra como
   evento `prefilter` y se mide en el dashboard (tarjeta "Turnos sin LLM"). En
   estado `HUMAN_HANDOFF` el bot calla sin gastar ni una llamada.
2. **Routing de modelo** ([`routing.py`](../app/agent/routing.py)): el Q&A usa el
   **modelo rápido** por defecto y solo escala al **inteligente** ante preguntas
   largas o complejas (comparar, recomendar, "por qué"). La NLU es estructurada y
   barata → siempre modelo rápido.
3. **Ventana de conversación acotada**: la FSM no reenvía el historial; cada turno
   lleva el mínimo contexto. Se mantiene una **ventana deslizante** de los últimos
   `WINDOW_TURNS` mensajes (recortados) que viaja en el `handoff` para dar contexto
   al humano, sin crecer sin límite.
4. **Caché del contexto** ([`context.py`](../app/agent/context.py)): el system
   prompt y el catálogo del negocio se cachean en memoria (TTL) y se invalidan al
   cambiar servicios → no se reconstruyen ni se reconsulta la BD cada turno, y se
   envía SIEMPRE el mismo string (lo que necesita la caché de prompt de OpenAI). El
   catálogo va en el **prefijo `system`** (estable por negocio) para que ese prefijo
   sea cacheable; en el `user` solo lo variable del turno. La NLU usa el modelo
   **más barato** (`OPENAI_MODEL_NLU`), por ser la llamada más frecuente.

### Coste por reserva: céntimos, y de hecho mucho menos

Con `gpt-4o-mini` (≈ $0,15 / $0,60 por millón de tokens de entrada/salida) y los
cortes pre-LLM, una reserva completa usa del orden de **1–3 llamadas de NLU**
(saludo, "sí/no" y elección numérica no llaman al modelo). Cada llamada ronda los
cientos de tokens de entrada y decenas de salida → **~0,0001–0,0002 $ por llamada**.
Una reserva sale por **~0,0005 $ ≈ 0,05 céntimos**: muy por debajo de un céntimo.
El coste real queda **medido** en el dashboard (`coste por conversación`), así que
no es una estimación a ciegas. En negocios con catálogo/políticas grandes, la caché
de prefijo de OpenAI abarata además los tokens de entrada repetidos.

## Escalabilidad

- **Sin estado en el proceso**: el estado de conversación vive en `conversations`
  (BD) y el rate limit puede ir a Redis → varias réplicas detrás de un balanceador
  (con `TRUST_PROXY` para atribuir bien la IP del cliente).
- **Métricas agregadas en SQL**: `collect_summary` agrupa los tokens en la BD
  (`GROUP BY` sobre los campos JSON del evento) y solo calcula el coste en Python
  sobre el resultado ya agrupado → no carga millones de eventos en memoria.
- **Retención de datos** ([`app/retention.py`](../app/retention.py)): purga
  periódica de `inbound_messages`, `events_log` y conversaciones inactivas antiguas
  → tablas acotadas (y RGPD). Vía `scripts/purge_old.py` desde cron.
- **Pool de conexiones** configurable (`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`) con
  `pool_pre_ping` y `pool_recycle` para Postgres; SQLite lo ignora.
- **Índices** para las consultas calientes: disponibilidad por negocio/fecha,
  conversación por teléfono, dedupe de entrada y barrido de recordatorios.
- **Webhook no bloqueante**: responde `200` y procesa en segundo plano. El
  siguiente salto natural es una cola/worker externo si el volumen lo pide.

## Calidad

- **Tests** (`tests/`, 185 casos): modelos/constraints, disponibilidad, reservas,
  extensiones de calendario, **agente de Q&A y máquina de estados** (reservar/
  cancelar/reprogramar/handoff, con LLM falso), **optimización de coste**, métricas,
  **fiabilidad Fase 4** (reintentos, alertas, bandeja de handoff), **seguridad**
  (auth+hash, fail-closed, rate limit, CSP/HSTS, **anti-SQLi**, SSRF, RGPD), API
  e2e y webhook. BD SQLite en memoria aislada (`StaticPool`).
- **ruff** (lint + orden de imports + código muerto + **bandit** `S`) y **mypy**
  (tipos) en `pyproject.toml`; **pip-audit** (CVE) en CI. Modelo de amenazas en
  [`docs/SECURITY.md`](SECURITY.md).

## Producción (Fase 4)

Lo que hace al sistema operable **sin vigilancia constante**:

- **Avisos al negocio** ([`app/notifications.py`](../app/notifications.py)): barre
  los eventos `handoff`/`error` no notificados y avisa al `notify_phone` del
  negocio. Idempotente vía `EventLog.notified_at`, decoupled (cron), igual que
  recordatorios y purga.
- **Bandeja de handoff**: `GET …/handoffs` lista las conversaciones en
  `HUMAN_HANDOFF`; `POST …/conversations/{id}/release` las devuelve a `IDLE` para
  que el bot retome. Sin esto, una conversación derivada quedaría muda para siempre.
- **Reintentos de envío**: `_post_message` reintenta con **backoff exponencial**
  los errores transitorios (red, 5xx, 429) y NO los 4xx permanentes.
- **Despliegue**: `Dockerfile` (usuario sin privilegios) + servicio `app` en
  `docker-compose.yml`. El esquema en producción lo aplican las **migraciones de
  Alembic** ([`alembic/`](../alembic/)); en desarrollo, `init_db` sobre SQLite.

## Qué falta (siguiente fase)

La **Fase 5** productiza el copiloto multi-tenant: panel de alta self-service de
negocios, RAG **opcional** por cliente (solo si su base de conocimiento es grande,
no antes), facturación y onboarding. El aislamiento por `business_id` ya está desde
el día 1, así que no requiere una migración dolorosa.
