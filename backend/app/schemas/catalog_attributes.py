"""Canonical attributes shared by admin writes and catalog query validation."""
import unicodedata
from typing import Annotated
from pydantic import BeforeValidator, Field


def normalize_attribute(value, *, size=False):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Attribute must be text")
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Attribute contains control characters")
    value = " ".join(unicodedata.normalize("NFKC", value).split())
    if not value:
        return None
    if size:
        return value.upper()
    value = value.casefold()
    aliases = {"black": "noir", "k7el": "noir", "كحل": "noir", "أسود": "noir",
               "white": "blanc", "byed": "blanc", "بيض": "blanc", "أبيض": "blanc",
               "blue": "bleu", "zre9": "bleu", "زرق": "bleu", "أزرق": "bleu",
               "red": "rouge", "7mer": "rouge", "حمر": "rouge", "أحمر": "rouge",
               "green": "vert", "khder": "vert", "خضر": "vert", "أخضر": "vert"}
    return aliases.get(value, value)


Size = Annotated[str | None, Field(max_length=64), BeforeValidator(lambda v: normalize_attribute(v, size=True))]
Color = Annotated[str | None, Field(max_length=64), BeforeValidator(normalize_attribute)]
