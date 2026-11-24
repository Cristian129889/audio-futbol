"""Encrypted-at-rest persistence for AudioFutbol.

The public entry point is :class:`audiofutbol.security.cql.CQLStore`.
"""

from .cql import CQLStore, IntegrityError
from .crypto import derive_key, load_or_create_key, seal, unseal

__all__ = [
    "CQLStore",
    "IntegrityError",
    "derive_key",
    "load_or_create_key",
    "seal",
    "unseal",
]
