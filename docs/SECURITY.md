# Seguridad

Estado del endurecimiento del backend y guía de despliegue seguro.

## Controles implementados

| Área | Control | Dónde |
|---|---|---|
| **AuthN/AuthZ** | API key obligatoria en `/admin/*`, `/ask` y métricas vía `X-API-Key`. Admite **varias claves** (rotación) y **hash SHA-256 en reposo** (`ADMIN_API_KEY_HASHES`). Exige **≥24 caracteres** en producción. **Fail-closed** si no hay credencial válida. Alternativa: **redes de confianza** (`TRUSTED_ADMIN_CIDRS`, LAN/VPN) que acceden sin clave. Comparación en tiempo constante; fallos logueados. | [`app/security.py`](../app/security.py), `main.py` |
| **Sesión del panel (navegador)** | El panel cambia la API key por una **cookie de sesión firmada** (HMAC-SHA256) **HttpOnly + SameSite=Strict + Secure** (`POST /admin/session`): la clave no se guarda en el navegador, así un XSS no puede robarla. Caduca (`SESSION_TTL_MIN`). El header `X-API-Key` sigue valiendo para clientes no-navegador. | [`app/sessions.py`](../app/sessions.py), `app/api/auth.py` |
| **CSRF** | Las escrituras autenticadas por cookie exigen `X-CSRF-Token` (doble defensa sobre SameSite=Strict). El token se emite en el login y el panel lo guarda **en memoria** (no en almacenamiento). | `app/security.py`, `frontend/js/api.js` |
| **Bloqueo de login** | Tras `LOGIN_MAX_ATTEMPTS` fallos por IP, el login se bloquea `LOGIN_LOCKOUT_MIN` minutos (frena la fuerza bruta de claves, más allá del rate-limit). | [`app/sessions.py`](../app/sessions.py) |
| **2FA (TOTP)** | Segundo factor opcional del panel (`ADMIN_TOTP_SECRET`, RFC 6238, compatible con Google Authenticator/Authy). Stdlib, sin dependencias. | [`app/totp.py`](../app/totp.py) |
| **Auditoría a prueba de manipulación** | Registro **append-only encadenado por hash** (`audit_log`) de toda **escritura admin** y de los **eventos de seguridad** (401/403/429). Guarda IP + **huella** de la API key (nunca la clave), método, ruta y estado; sin PII. Alterar o borrar una fila pasada rompe la cadena → `GET /admin/audit/verify` lo detecta (`GET /admin/audit` para consultarla). | [`app/audit.py`](../app/audit.py), `app/models/audit_log.py`, `main.py` |
| **Alertas de seguridad** | Si los eventos de seguridad superan `SECURITY_ALERT_THRESHOLD` por ventana, `scan_security` emite un aviso que el cron de notificaciones entrega al negocio (reutiliza la entrega idempotente existente). | [`app/audit.py`](../app/audit.py), `scripts/send_alerts.py` |
| **CSP del panel** | `/panel/*` se sirve con **Content-Security-Policy** estricta: `script-src 'self'` (sin inline → corta XSS), sin CDNs externos, `frame-ancestors 'none'`. | `app/main.py` |
| **Endurecimiento del contenedor** | Imagen non-root + en compose: `read_only` (raíz inmutable), `cap_drop: ALL`, `no-new-privileges`, `tmpfs /tmp`. Reduce el daño de un RCE. | `Dockerfile`, `docker-compose.yml` |
| **Escaneo de código** | `ruff` ejecuta el conjunto **bandit** (`S`): detecta patrones inseguros (asserts en producción, hashes débiles, SQL por cadenas, secretos embebidos…). | `pyproject.toml` |
| **Escaneo de dependencias** | `pip-audit` audita `requirements.txt` contra la base de CVE en **CI** (en cada push/PR y semanalmente); **Dependabot** abre PRs de actualización (pip, GitHub Actions, Docker). | `.github/workflows/ci.yml`, `.github/dependabot.yml` |
| **Fuerza bruta** | El rate-limit corre **antes** de la autenticación en los endpoints admin → frena el barrido de claves por IP. | `app/main.py` |
| **Cabeceras / transporte** | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control`, `Permissions-Policy`, `Cross-Origin-Opener/Resource-Policy`. En producción además **HSTS** (`Strict-Transport-Security`). | `app/main.py` |
| **Dashboard** | Oculto en producción (`DASHBOARD_ENABLED`). Sirve con **CSP basada en nonce**, **sin CDN externo** (gráfico dibujado en cliente) → sin XSS ni riesgo de cadena de suministro. | `app/api/metrics.py` |
| **Webhook** | Verificación de firma `X-Hub-Signature-256` (HMAC-SHA256). **Obligatoria en producción**; sin secreto se rechaza. Comparaciones en tiempo constante (`hmac.compare_digest`). | [`app/api/webhook.py`](../app/api/webhook.py) |
| **Abuso de coste / DoS** | Rate limiting por IP (ventana fija) en `/ask` y webhook, con backend **memoria o Redis** (compartido entre réplicas, con *fallback* a memoria si Redis falla). IP real del cliente vía `X-Forwarded-For` **solo** si `TRUST_PROXY=true` (si no, no spoofeable). Respuesta del LLM acotada (`max_tokens`). Mensaje entrante truncado (`max_inbound_chars`), respuesta saliente acotada (`max_outbound_chars`), tope de mensajes por payload (`max_messages_per_payload`) y de tamaño de cuerpo (`max_body_bytes` → `413`). | `app/security.py`, `app/main.py`, `app/agent/` |
| **Endurecimiento HTTP** | Cabeceras de seguridad (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control`). En producción: `TrustedHostMiddleware` (`ALLOWED_HOSTS`) y documentación/`openapi.json` deshabilitados. | `app/main.py` |
| **Validación de identidad** | Teléfono del cliente validado como **E.164**; `phone_number_id`/destinatario numéricos antes de llamar a Graph. | `app/schemas/appointment.py`, `app/integrations/whatsapp.py` |
| **Dedupe de entrada** | Cada `message_id` se reclama de forma **atómica** vía `UNIQUE(business_id, message_id)` en `inbound_messages` (savepoint insert-or-skip): una reentrega de Meta no se responde dos veces, sin ventana de carrera. | `app/agent/handler.py`, `app/models/inbound_message.py` |
| **DoS de CPU** | Rango de disponibilidad acotado (`availability_max_days`). | `app/schemas/appointment.py` |
| **Validación de entrada** | Formato `HH:MM` y tramos validados en horarios, horarios de recurso y cierres. Longitudes máximas en campos de texto. | [`app/schemas/validators.py`](../app/schemas/validators.py) |
| **SSRF / inyección de ruta** | `phone_number_id` y destinatario deben ser numéricos antes de llamar a Graph. | [`app/integrations/whatsapp.py`](../app/integrations/whatsapp.py) |
| **Inyección SQL** | 100% ORM/consultas parametrizadas (incluido el advisory lock con *bound param* y el acceso JSON). **Ninguna cabecera ni parámetro se concatena en SQL**: viajan como literales vinculados. El valor de `X-Forwarded-For` se **valida como IP** (`ipaddress`) antes de usarse como clave del limitador. | toda la capa de datos, `app/security.py` |
| **Validación de entrada (defensa en profundidad)** | Sobre la parametrización: los **identificadores de ruta** (`*_id`) deben ser **UUID** válidos (si no, 422 antes de tocar la BD); el **texto libre** (nombre, notas) se recorta, se acota en longitud y **rechaza caracteres de control/NUL**; los filtros se restringen a conjuntos cerrados (p. ej. `action` ∈ {mutation, security}) y los rangos numéricos tienen tope. | `app/security.py` (`validate_path_ids`), `app/schemas/validators.py` |
| **No revelar la BD** | Un `SQLAlchemyError` (o cualquier excepción no controlada) devuelve un `500` genérico (`{"detail":"Error interno"}`); el esquema, la consulta y la traza solo van al **log del servidor**. `debug` desactivado. | `app/main.py` |
| **Cifrado de PII en reposo** | Con `PII_ENCRYPTION_KEY`, el **nombre** y las **notas** se cifran con Fernet (IV aleatorio) y el **teléfono** con AES-SIV **determinista** (sigue siendo buscable por igualdad y respeta el UNIQUE, sin reescribir consultas). Capa por encima del cifrado de disco: un volcado de la BD no revela PII. Sin clave, passthrough; migración con `scripts/encrypt_pii.py`. | [`app/crypto.py`](../app/crypto.py), `app/models/types.py` |
| **RGPD / retención** | `consent_at` al crear cliente; PII tras la API key. **Purga** periódica de mensajes, eventos y conversaciones inactivas antiguas (`RETENTION_DAYS`) — acota datos personales y tamaño de tablas. | `app/tools/booking.py`, `app/retention.py` |
| **XSS** | El dashboard escapa todo dato antes de inyectarlo en el DOM (defensa en profundidad; los datos son del sistema, no del usuario). | `app/api/metrics.py` |
| **Secretos** | Fuera de git (`.env` en `.gitignore`); cargados por entorno. | `.gitignore`, `config.py` |

## Configuración obligatoria en producción

```bash
APP_ENV=production
ADMIN_API_KEY=<cadena aleatoria de >=24 chars>  # admite varias por comas (rotación)
WHATSAPP_APP_SECRET=<app secret de Meta>     # si falta, el webhook rechaza todo
WHATSAPP_VERIFY_TOKEN=<token secreto>
RATE_LIMIT_PER_MIN=120                        # ajustar al tráfico esperado
RATE_LIMIT_BACKEND=redis                      # si hay más de una instancia
REDIS_URL=redis://<host>:6379/0
ALLOWED_HOSTS=api.tudominio.com               # restringe el Host aceptado
TRUST_PROXY=true                              # SOLO si hay un proxy de confianza delante
RETENTION_DAYS=90                             # purga de datos con PII (vía cron)
AUDIT_ENABLED=true                            # registro de auditoría (hash-chain)
SECURITY_ALERT_THRESHOLD=20                   # picos de 401/403/429 por ventana → alerta
SECURITY_ALERT_WINDOW_MIN=60
PII_ENCRYPTION_KEY=<cadena de alta entropía>  # cifra nombre/teléfono/notas en reposo
SESSION_SECRET=<cadena de alta entropía>      # firma la cookie de sesión del panel
SESSION_TTL_MIN=720                           # caducidad de la sesión (12 h)
LOGIN_MAX_ATTEMPTS=5                           # fallos por IP antes de bloquear
LOGIN_LOCKOUT_MIN=15
ADMIN_TOTP_SECRET=<secreto base32>            # opcional: 2FA del panel
```

Tras activar `PII_ENCRYPTION_KEY` sobre datos existentes, recifra una sola vez:

```bash
PII_ENCRYPTION_KEY=... python -m scripts.encrypt_pii
```

> El cifrado cambia el tipo/longitud de `customers.phone/name`,
> `conversations.customer_phone` y `appointments.notes`. En producción (Alembic),
> genera la migración de esquema antes de desplegar. **Rotar la clave** exige
> recifrar (descifra con la antigua, cifra con la nueva).

Programa la purga de retención a diario (RGPD + tamaño de tablas):

```bash
30 3 * * * cd /app && python -m scripts.purge_old
```

Con `APP_ENV=production`: `/docs`, `/redoc`, `/openapi.json` y `/dashboard` quedan
ocultos; se activa `TrustedHostMiddleware` (si `ALLOWED_HOSTS` ≠ `*`) y se emite
HSTS. Para exponer el dashboard en producción, `DASHBOARD_ENABLED=true`.

## Riesgos conocidos / pendientes

- **AuthZ de grano fino**: hoy la API key es de operador (acceso a todos los
  tenants). Para self-service multi-tenant hará falta auth por negocio (token por
  tenant u OAuth) y filtrado por `business_id` del sujeto autenticado.
- **Prompt injection** *(mitigado por diseño en Fase 2)*: el LLM solo clasifica
  intención y extrae entidades (`nlu.py`); las acciones con efectos pasan siempre
  por la máquina de estados (`flow.py`) y las tools deterministas, que validan
  horario, hueco, capacidad, idempotencia y concurrencia. Un mensaje malicioso no
  puede forzar una cita: solo se reserva en `CONFIRMING` y `book_appointment`
  rechaza lo inválido. **Mantener esta frontera** al añadir nuevas acciones: nunca
  ejecutar efectos a partir del texto libre del modelo.
- **Transferencia internacional (OpenAI, EE. UU.)**: para datos sensibles, valorar
  DPA y un fallback a un modelo con residencia UE, como anota la arquitectura.

### Rate limiting con Redis

En multi-instancia configura `RATE_LIMIT_BACKEND=redis` y `REDIS_URL`. El recuento
es compartido entre réplicas (ventana fija con `INCR`/`EXPIRE`). Si Redis no está
disponible, el limitador cae al contador en memoria de cada proceso (degradado
pero no se cae la API). `docker-compose.yml` ya levanta Redis.

## Cómo verificar

```bash
python -m pytest tests/test_security.py   # auth, webhook, rate limit, validación, SSRF, RGPD
```
