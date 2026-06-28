"""Genera una API key aleatoria y su hash SHA-256 para configurarla en reposo.

Uso:  python -m scripts.hash_key

Guarda SOLO el hash en el servidor (ADMIN_API_KEY_HASHES) y entrega la clave en
claro al cliente. Así, un leak de la configuración no revela una clave usable.
Para rotar, genera otra y añádela (varios hashes separados por comas).
"""
from __future__ import annotations

import secrets

from app.security import hash_key


def main() -> None:
    key = secrets.token_urlsafe(32)
    print("API key (entrégala al cliente; NO la guardes en el servidor):")
    print(f"  {key}")
    print("\nHash para ADMIN_API_KEY_HASHES (esto sí va en el servidor):")
    print(f"  {hash_key(key)}")


if __name__ == "__main__":
    main()
