"""Small schema boundary for attribute requests and verified catalog DTOs.

Recognition is not capability: storage/volume/etc. can be requested, but only
the explicit field registry below can produce verified values or DB filters.
No product-category branches, ORM introspection or model-controlled field access.
"""
import re
from itertools import combinations
from dataclasses import dataclass, field
from typing import Callable

from app.ai.scope import normalize
from app.schemas.catalog_attributes import normalize_attribute


@dataclass(frozen=True)
class CatalogAttribute:
    read: Callable
    normalize: Callable
    aliases: str
    value_pattern: str
    numeric_ellipsis: bool = False


@dataclass
class AttributeInput:
    requested: dict[str, str] = field(default_factory=dict)
    explicit: set[str] = field(default_factory=set)
    unresolved_value: str | None = None
    followup: bool = False
    ambiguous: bool = False


class UnsupportedAttribute(ValueError):
    pass


class CatalogAttributeAdapter:
    """Concrete V1 bridge. Future schema work extends this boundary, not turns."""

    fields = {
        "size": CatalogAttribute(lambda row: row.size,
            lambda value: normalize_attribute(value, size=True),
            r"taille|size|pointure|مقاس", r"xxs|xs|s|m|l|xl|xxl|xxxl|[2-6]xl|[0-9]{1,3}", True),
        "color": CatalogAttribute(lambda row: row.color, normalize_attribute,
            r"couleur|color|colour|لون", r"[\w-]{1,64}"),
    }
    symbolic_sizes = r"xxs|xs|s|m|l|xl|xxl|xxxl|[2-6]xl"
    colors = {"noir", "blanc", "bleu", "rouge", "vert", "jaune", "rose", "gris", "beige", "marron", "violet", "orange"}
    # Recognition vocabulary only. None of these imply a verified DB capability.
    request_labels = {
        "shade": r"shade|teinte|درجة",
        "frame_color": r"frame[_ ]color|couleur (?:du )?cadre|cadre",
        "frame_shape": r"frame[_ ]shape|forme (?:du )?cadre",
        "storage": r"storage|stockage",
        "volume": r"volume|contenance",
        "capacity": r"capacity|capacité|capacite",
        "material": r"material|matériau|materiau|matière|matiere",
        "dimensions": r"dimensions?|longueur|length",
        "scent": r"scent|senteur",
        "flavor": r"flavor|flavour|saveur",
        "pack_size": r"pack[_ ]size|lot de",
        "compatibility": r"compatibility|compatibilité|compatible (?:avec|with)",
        "finish": r"finish|finition",
        "ram": r"ram",
    }
    unit_attributes = {
        "storage": r"gb|tb|go|to",
        "volume": r"ml|cl|litres?|liters?",
        "dimensions": r"cm|mm|mètres?|metres?",
        "weight": r"kg|grammes?|grams?|g",
    }

    @property
    def supported(self):
        return tuple(self.fields)

    def normalize_values(self, requested):
        if len(requested) > 8:
            raise ValueError("Too many attributes")
        result = {}
        for key, value in requested.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", key):
                raise ValueError("Invalid attribute key")
            if not isinstance(value, str) or not value.strip() or len(value) > 64:
                raise ValueError("Invalid attribute value")
            result[key] = self.fields[key].normalize(value) if key in self.fields else normalize_attribute(value, size=True).casefold()
        return result

    def verified(self, variant):
        return {key: spec.read(variant) for key, spec in self.fields.items() if spec.read(variant) is not None}

    def filters(self, requested):
        unknown = set(requested) - self.fields.keys()
        if unknown:
            raise UnsupportedAttribute("Catalog cannot verify requested attributes")
        return self.normalize_values(requested)

    def from_query(self, query):
        return self.verified(query) if query is not None else {}

    def apply(self, query, requested):
        # Revalidate values rather than using model_copy to bypass validators.
        return type(query).model_validate(query.model_dump() | self.filters(requested))

    def matches(self, variant, requested):
        expected = self.filters(requested)
        actual = self.verified(variant)
        return all(actual.get(key) == value for key, value in expected.items())

    def transition(self, variant, changed):
        changed = self.filters(changed)
        preserved = {key: value for key, value in self.verified(variant).items() if key not in changed} if variant else {}
        return preserved, preserved | changed

    def relaxation_filters(self, changed, preserved, hard):
        """At most three queries; never relax this turn's changes or hard limits."""
        changed, preserved, hard = map(self.filters, (changed, preserved, hard))
        if any(key in changed and changed[key] != value for key, value in hard.items()):
            return []
        relaxable = sorted(preserved.keys() - hard.keys() - changed.keys())
        queries = []
        for count in range(1, len(relaxable) + 1):
            for relaxed in combinations(relaxable, count):
                queries.append({k: v for k, v in preserved.items() if k not in relaxed} | hard | changed)
                if len(queries) == 3:
                    return queries
        return queries

    def differences(self, variant, requested):
        actual = self.verified(variant)
        return {key: (value, actual.get(key)) for key, value in requested.items()
                if actual.get(key) != value}

    @staticmethod
    def short_body(text):
        value = normalize(text).strip(" ?؟!.")
        value = re.sub(r"^(?:ah\s+)?ok(?:ay)?\s*[,،]?\s*", "", value)
        return re.sub(r"^(?:(?:w|et|and)\s+)?(?:en\s+)?", "", value)

    def supported_request(self, text):
        """Explicit labels or short/suffix values; don't strip color from names."""
        value = normalize(text).strip(" ?؟!.")
        requested, explicit = {}, set()
        for key, spec in self.fields.items():
            match = re.search(r"\b(?:" + spec.aliases + r")\s+(" + spec.value_pattern + r")\b", value)
            if match:
                requested[key] = spec.normalize(match[1])
                explicit.add(key)
                value = value[:match.start()] + " " + value[match.end():]
        # Symbolic size is a known V1 shorthand. A bare NUMBER is never one.
        match = re.search(r"\b(" + self.symbolic_sizes + r")\s*$", value)
        if match and "size" in self.fields:
            requested.setdefault("size", self.fields["size"].normalize(match[1]))
            value = value[:match.start()]
        tokens = re.findall(r"[\w-]+", value)
        if "color" in self.fields and tokens and normalize_attribute(tokens[-1]) in self.colors:
            requested.setdefault("color", normalize_attribute(tokens[-1]))
        return requested, explicit

    def query_parts(self, text):
        requested, _ = self.supported_request(text)
        value = normalize(text)
        for key, spec in self.fields.items():
            value = re.sub(r"\b(?:" + spec.aliases + r")\s+(?:" + spec.value_pattern + r")\b", " ", value)
        if "size" in requested:
            value = re.sub(r"\b" + re.escape(requested["size"].lower()) + r"\b\s*$", "", value)
        if "color" in requested:
            value = re.sub(r"\b[\w-]+\s*$", lambda match: "" if normalize_attribute(match[0].strip()) == requested["color"] else match[0], value)
        filler = {"un", "une", "le", "la", "les", "a", "an", "the", "en"}
        return [t for t in re.findall(r"[\w-]+", value) if t not in filler], requested

    def inspect(self, text, active_attribute=None):
        try:
            return self._inspect(text, active_attribute)
        except ValueError:
            return AttributeInput(followup=True, ambiguous=True)

    def _inspect(self, text, active_attribute=None):
        value = normalize(text)
        body = self.short_body(text)
        requested = {}
        for key, labels in self.request_labels.items():
            match = re.search(r"\b(?:" + labels + r")\s+([\w.-]+(?:\s*(?:gb|ml|cm))?)\b", value)
            if match:
                requested[key] = match[1]
        for key, units in self.unit_attributes.items():
            match = re.search(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(" + units + r")\b", value)
            if match:
                requested.setdefault(key, match[1] + match[2])
        if requested:
            return AttributeInput(self.normalize_values(requested), set(requested), followup=True)
        supported, explicit = self.supported_request(text)
        terms, _ = self.query_parts(body)
        short = not terms and bool(supported)
        # Following an unverified shade/frame/etc. request, 'w rose' must not
        # silently become a different supported field merely sharing its value.
        if active_attribute and active_attribute not in self.fields and not explicit and (short or re.fullmatch(r"[\w.-]{1,64}", body)):
            return AttributeInput(self.normalize_values({active_attribute: body}), followup=True)
        if short:
            return AttributeInput(supported, explicit, followup=True)
        if re.fullmatch(r"\d+(?:[.,]\d+)?", body):
            if len(body) > 64:
                return AttributeInput(followup=True, ambiguous=True)
            return AttributeInput(unresolved_value=body, followup=True)
        if re.fullmatch(r"(?:w|et|and)\s+[\w.-]{1,64}[ ?؟!.]*", value):
            return AttributeInput(unresolved_value=body, followup=True)
        # An unlabeled option request is a constraint, not evidence of a field.
        match = re.search(r"\b(?:kayn f|disponible en)\s+([\w.-]{1,64})[ ?؟!.]*$", value)
        if match:
            return AttributeInput(unresolved_value=match[1], followup=True)
        return AttributeInput(supported, explicit)

    def infer_value(self, value, verified):
        """Only a unique compatible, actually populated field can give a bare value meaning."""
        candidates = [key for key, current in verified.items()
                      if self.fields[key].numeric_ellipsis and current.isdecimal() and value.isdecimal()]
        return self.filters({candidates[0]: value}) if len(candidates) == 1 else {}


CATALOG_ATTRIBUTES = CatalogAttributeAdapter()
