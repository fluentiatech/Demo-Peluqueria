# Guía de desarrollo

## Puesta en marcha

```bash
pip install -r requirements.txt
cp .env.example .env          # opcional; por defecto usa SQLite
python -m scripts.seed        # negocio demo de ejemplo
python -m uvicorn app.main:app --reload
```

API interactiva: <http://localhost:8000/docs>.

> **Nota del entorno:** en esta máquina `pip` falla por SSL al crear un venv.
> Se usa el Python global, que ya trae las dependencias; para paquetes que falten
> añade `--trusted-host pypi.org --trusted-host files.pythonhosted.org`.

## Comandos de calidad

| Acción | Comando |
|---|---|
| Tests | `python -m pytest` |
| Tests con cobertura por verbose | `python -m pytest -v` |
| Lint + escaneo de código (bandit) | `python -m ruff check .` |
| Lint + autofix | `python -m ruff check --fix .` |
| Formateo | `python -m ruff format .` |
| Tipos | `python -m mypy app` |
| Escaneo de dependencias (CVE) | `python -m pip_audit -r requirements.txt --strict` |

**Antes de dar por terminado un cambio:** los tres en verde — `pytest`,
`ruff check .` y `mypy app`. El escaneo de dependencias (`pip-audit`) corre en
**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)); en local requiere
salida a internet (en esta máquina el TLS está interceptado, igual que `pip`).

## Convenciones

- **La lógica de negocio vive en `app/tools/`**, no en la capa API ni (nunca) en
  el prompt del LLM. Si un endpoint necesita una regla nueva de reservas, va en
  una tool y el endpoint la invoca.
- **Toda tabla cuelga de `business_id`.** No introduzcas entidades sin tenant.
- **Dinero con `Decimal`/`Numeric`**, jamás `float`.
- **Errores de negocio** → excepciones de `app/tools/booking.py`
  (`BookingError`, `SlotTakenError`), que la API traduce a códigos HTTP.
- **Cada cambio de comportamiento lleva su test.** Mira `tests/conftest.py` para
  las fixtures (`seed`, `db_session`, `client`, `fake_llm`).
- **El LLM se inyecta tras un `Protocol`** (`app/agent/llm.py`). Los tests usan
  `FakeLLM`; nunca llaman a OpenAI. Para probar el agente en vivo, exporta
  `OPENAI_API_KEY` y usa `POST /admin/businesses/{id}/ask`.

## Estructura

Ver [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) para el detalle de capas y el
modelo de datos.

## Migraciones (producción)

En desarrollo el esquema se crea solo al arrancar (`init_db`). En producción la
fuente de verdad serán las migraciones de Alembic (pendiente de inicializar en
la fase de despliegue).
