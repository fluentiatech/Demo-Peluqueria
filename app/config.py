"""Configuración central de la aplicación, cargada desde variables de entorno / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Entorno ---
    app_env: str = "development"
    log_level: str = "INFO"
    # Zona horaria del negocio: TODAS las horas de cita/disponibilidad se manejan
    # en esta zona (consciente de DST), para que "las 9" sea siempre las 9 en España.
    timezone: str = "Europe/Madrid"
    sql_echo: bool = False  # vuelca el SQL generado por SQLAlchemy (debug)

    # --- Seguridad ---
    # API key(s) para la API de administración y el agente. Admite varias separadas
    # por comas (rotación sin downtime). Vacía = abierta solo en desarrollo; en
    # producción es obligatoria y cada clave debe ser larga (si no, se rechaza).
    admin_api_key: str = ""
    # Hashes SHA-256 (hex) de las API keys, separados por comas. Recomendado en
    # producción: la config no contiene la clave en claro, solo su hash. Genera
    # par clave/hash con `python -m scripts.hash_key`.
    admin_api_key_hashes: str = ""
    # Mostrar el dashboard HTML. Por defecto: solo fuera de producción.
    dashboard_enabled: bool | None = None
    # Peticiones por minuto y por IP en endpoints públicos (/ask, webhook). 0 = sin límite.
    rate_limit_per_min: int = 120
    # Backend del limitador: "memory" (una instancia) o "redis" (multi-instancia).
    rate_limit_backend: str = "memory"
    # Ventana máxima (días) consultable de disponibilidad, para evitar DoS de CPU.
    availability_max_days: int = 92
    # Longitud máxima del mensaje entrante que se manda al LLM.
    max_inbound_chars: int = 2000
    # Longitud máxima de la respuesta enviada por WhatsApp (límite de Meta ~4096).
    max_outbound_chars: int = 4000
    # Mensajes procesados como máximo por payload de webhook (anti-abuso).
    max_messages_per_payload: int = 20
    # Tamaño máximo del cuerpo de una petición (bytes).
    max_body_bytes: int = 1_000_000
    # Hosts permitidos (CSV) cuando APP_ENV=production. "*" = cualquiera.
    allowed_hosts: str = "*"
    # Confiar en X-Forwarded-For (solo si hay un proxy/balanceador de confianza
    # delante que lo fija); si no, el rate limit usaría una IP spoofeable.
    trust_proxy: bool = False
    # Redes de confianza (CIDR, CSV) que acceden al admin/panel SIN API key: la
    # LAN del local o la VPN (p. ej. Tailscale "100.64.0.0/10"). Fuera de ellas se
    # exige la clave. Requiere TRUST_PROXY si hay un proxy delante.
    trusted_admin_cidrs: str = ""
    # Retención de datos operativos con PII (días): mensajes y logs antiguos.
    retention_days: int = 90
    # Clave maestra para cifrar PII en reposo (nombre, teléfono, notas). Vacía =
    # sin cifrado (texto claro). Cualquier cadena de alta entropía; deriva por
    # separado el material de Fernet y AES-SIV. Rotar requiere recifrar los datos.
    pii_encryption_key: str = ""

    # --- Sesiones del panel y segundo factor ---
    # Secreto para firmar la cookie de sesión del panel. Vacío = se deriva de las
    # claves admin (estable si están fijadas) o se genera por proceso (dev).
    session_secret: str = ""
    session_ttl_min: int = 720  # caducidad de la sesión (12 h)
    # Forzar cookie Secure (solo HTTPS). None = activado en producción.
    cookie_secure: bool | None = None
    # Segundo factor TOTP del panel (secreto base32). Vacío = sin 2FA.
    admin_totp_secret: str = ""
    # Bloqueo de login: nº de fallos por IP y minutos de bloqueo.
    login_max_attempts: int = 5
    login_lockout_min: int = 15

    # --- Auditoría y detección ---
    # Registro de auditoría append-only (hash-chain) de escrituras admin y eventos
    # de seguridad (401/403/429). Capa de detección; desactivable si molesta.
    audit_enabled: bool = True
    # Pico de eventos de seguridad por ventana que dispara una alerta al negocio.
    # 0 = desactiva las alertas de seguridad.
    security_alert_threshold: int = 20
    security_alert_window_min: int = 60

    # --- Escalabilidad (pool de BD; ignorado en SQLite) ---
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- Base de datos ---
    # Por defecto SQLite para que el proyecto arranque sin dependencias externas.
    database_url: str = "sqlite+aiosqlite:///./citas.db"

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- OpenAI / ChatGPT ---
    openai_api_key: str = ""
    # Modelo rutinario (clasificación, Q&A) y modelo para casos complejos.
    openai_model_fast: str = "gpt-4o-mini"
    openai_model_smart: str = "gpt-4o"
    # Modelo específico de NLU (clasificar/extraer). Vacío = usa el rápido. Aquí
    # conviene el modelo MÁS barato disponible, porque es el que más se invoca.
    openai_model_nlu: str = ""
    openai_timeout_s: float = 30.0
    # TTL (s) de la caché en memoria del contexto del negocio (prompt + catálogo).
    context_cache_ttl_s: int = 300

    # --- WhatsApp Cloud API ---
    whatsapp_verify_token: str = "cambia-esto"  # noqa: S105 (placeholder, no secreto)
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""

    # --- Recordatorios de cita ---
    reminder_hours_before: int = 24       # antelación con la que se recuerda
    reminder_template: str = "appointment_reminder"  # plantilla aprobada en Meta
    reminder_lang: str = "es"

    # --- Fiabilidad de envío y alertas (Fase 4) ---
    send_max_retries: int = 3             # intentos de envío ante error transitorio
    send_retry_backoff_s: float = 0.5     # backoff base (exponencial) entre intentos
    alert_lookback_hours: int = 24        # ventana de eventos a alertar al negocio

    # --- Observabilidad de coste ---
    # Sobrescribe las tarifas por modelo (USD por 1M de tokens). JSON, p. ej.:
    # {"gpt-4o-mini": {"input": 0.15, "cached": 0.075, "output": 0.6}}
    cost_prices_json: str = ""
    metrics_max_days: int = 365           # tope del rango consultable de métricas

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def allowed_hosts_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def admin_api_keys_list(self) -> list[str]:
        return [k.strip() for k in self.admin_api_key.split(",") if k.strip()]

    @property
    def trusted_admin_cidrs_list(self) -> list[str]:
        return [c.strip() for c in self.trusted_admin_cidrs.split(",") if c.strip()]

    @property
    def admin_api_key_hashes_list(self) -> list[str]:
        return [
            h.strip().lower()
            for h in self.admin_api_key_hashes.split(",")
            if h.strip()
        ]

    @property
    def dashboard_on(self) -> bool:
        if self.dashboard_enabled is not None:
            return self.dashboard_enabled
        return not self.is_production

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Singleton de configuración (cacheado)."""
    return Settings()


settings = get_settings()
