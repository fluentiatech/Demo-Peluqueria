FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencias primero (capa cacheada).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código.
COPY app ./app
COPY scripts ./scripts
COPY frontend ./frontend
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

# Usuario sin privilegios (seguridad).
RUN useradd -m appuser
USER appuser

EXPOSE 8000

# En producción el esquema lo aplican las migraciones de Alembic.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
