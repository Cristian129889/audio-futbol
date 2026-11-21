"""Text normalization and tokenization utilities."""

from __future__ import annotations

import unicodedata


def normalize_text(text: str) -> str:
    """Lowercase and strip diacritical marks from text."""
    decomposed = unicodedata.normalize("NFD", str(text).lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def tokenize(text: str) -> list[str]:
    """Split normalized text into contiguous alphabetic tokens."""
    tokens: list[str] = []
    current: list[str] = []
    for ch in normalize_text(text):
        if ch.isalpha():
            current.append(ch)
        elif current:
            tokens.append("".join(current))
            current.clear()
    if current:
        tokens.append("".join(current))
    return tokens
