"""Tipos de columna que cifran PII de forma transparente para el ORM.

El atributo Python sigue siendo texto en claro; solo la BD almacena el cifrado.
Así el resto del código (lecturas, escrituras, plantillas) no cambia.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.types import TypeDecorator

from app import crypto


class EncryptedString(TypeDecorator):
    """Cifrado aleatorio (Fernet). Para campos que se muestran pero no se buscan."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return crypto.encrypt(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return crypto.decrypt(value)


class DeterministicString(TypeDecorator):
    """Cifrado determinista (AES-SIV). Mantiene buscabilidad por igualdad y UNIQUE."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return crypto.det_encrypt(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return crypto.det_decrypt(value)
