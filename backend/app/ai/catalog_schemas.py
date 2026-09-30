"""Bounded, application-owned catalog contracts; no ORM objects cross this boundary."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.schemas.catalog_attributes import Size, Color
from app.ai.schemas import LanguageStyle


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Term = Annotated[str, Field(min_length=1, max_length=64)]
AttributeName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
AttributeValue = Annotated[str, Field(min_length=1, max_length=64)]
AttributeMap = Annotated[dict[AttributeName, AttributeValue], Field(max_length=8)]


class AttributeRequest(Contract):
    """Customer/model request only. Never catalog evidence or a database field."""
    name: AttributeName
    value: AttributeValue

Operation = Literal["search", "details", "variant", "price", "stock", "compare",
                    "recommend", "explain", "select", "change", "cancel", "clarify", "social", "unsupported"]

Intent = Literal["product_search", "availability", "price", "product_details", "recommendation",
                 "comparison", "purchase", "confirmation", "cancellation", "explanation",
                 "business_question", "social", "unknown", "cart_edit", "checkout", "website_ordering"]
BusinessTopic = Literal["delivery_price", "delivery_time", "payment", "returns", "warranty",
                        "discount", "negotiation", "order_status", "other"]


CheckoutField = Literal["customer_name", "phone", "city", "address", "delivery_note", "postal_code"]


class CheckoutValue(Contract):
    name: CheckoutField
    value: str = Field(min_length=1, max_length=500)


class RequestedItem(Contract):
    reference: Literal["none", "focus", "first", "second", "third", "other"] = "none"
    terms: list[Term] = Field(default_factory=list, max_length=6)
    attributes: list[AttributeRequest] = Field(default_factory=list, max_length=8)
    quantity: int = Field(default=1, ge=1, le=99)


class CustomerIntent(Contract):
    """Semantic requests, never catalog facts or commercial authorization."""
    intent: Intent
    reference: Literal["none", "focus", "first", "second", "third", "pair", "other"] = "none"
    product_reference: str | None = Field(default=None, max_length=160)
    terms: list[Term] = Field(default_factory=list, max_length=6)
    attributes: list[AttributeRequest] = Field(default_factory=list, max_length=8)
    quantity: int | None = Field(default=None, ge=1, le=99)
    budget: str | None = Field(default=None, pattern=r"^\d{1,10}(\.\d{1,2})?$")
    purchase_intent: bool = False
    confirmation: bool = False
    speech_act: Literal["affirmative", "question", "negative", "hypothetical", "quoted", "unknown"] = "unknown"
    evidence: str | None = Field(default=None, max_length=2000)
    preferences: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=4)
    language: LanguageStyle = "french"
    business_topic: BusinessTopic | None = None
    items: list[RequestedItem] = Field(default_factory=list, max_length=6)
    checkout: list[CheckoutValue] = Field(default_factory=list, max_length=6)
    cart_action: Literal["replace", "add", "remove", "set_quantity", "increase", "decrease"] = "replace"

    @model_validator(mode="after")
    def unique_attributes(self):
        if len({a.name for a in self.attributes}) != len(self.attributes):
            raise ValueError("Conflicting attribute requests")
        return self


class SearchProducts(Contract):
    terms: list[Term] = Field(default_factory=list, max_length=6)
    category: str | None = Field(default=None, max_length=255)
    brand: str | None = Field(default=None, max_length=255)
    max_price: str | None = Field(default=None, pattern=r"^\d{1,10}(\.\d{1,2})?$")
    in_stock_only: bool = True
    size: Size = None
    color: Color = None
    limit: int = Field(default=3, ge=1, le=5)

    @field_validator("terms")
    @classmethod
    def clean_terms(cls, terms):
        if any(not t.strip() for t in terms):
            raise ValueError("Empty term")
        normalized = list(dict.fromkeys(t.strip().casefold() for t in terms))
        if any(len(t) > 64 for t in normalized):
            raise ValueError("Normalized term too long")
        return normalized


class GetProducts(Contract):
    product_ids: list[UUID] = Field(default_factory=list, max_length=3)
    variant_ids: list[UUID] = Field(default_factory=list, max_length=6)
    size: Size = None
    color: Color = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.product_ids and not self.variant_ids:
            raise ValueError("At least one ID is required")
        return self


class VariantDTO(Contract):
    id: UUID
    product_id: UUID
    name: str
    sku: str
    price: str
    size: str | None
    color: str | None
    stock_quantity: int
    availability: Literal["in_stock", "out_of_stock"]


class ProductDTO(Contract):
    id: UUID
    name: str
    description: str | None
    brand: str | None
    category: str | None
    variants: list[VariantDTO]
    has_more_variants: bool = False


class CatalogResult(Contract):
    status: Literal["found", "not_found"]
    currency: Literal["MAD"]
    products: list[ProductDTO]
    has_more: bool = False


class ProductRef(Contract):
    product_id: UUID
    variant_id: UUID | None = None


class Selection(ProductRef):
    source_message_id: UUID | None = None
    resolution: Literal["product", "variant"]


class CatalogRefs(Contract):
    version: Literal[1] = 1
    presented: list[ProductRef] = Field(default_factory=list, max_length=3)
    focus: ProductRef | None = None
    selection: Selection | None = None


class ResponsePlan(Contract):
    action: Literal["show", "compare", "select", "clarify", "no_matches", "unsupported", "explain"]
    items: list[ProductRef] = Field(max_length=3)
    question: Literal["budget", "preference", "product", "variant"] | None
    explanation_topic: Literal["state", "out_of_stock", "price", "availability"] | None = None
    operation: Operation | None = None
    requested_attributes: list[AttributeRequest] = Field(default_factory=list, max_length=8)
    customer_intent: CustomerIntent | None = None
    # A model can propose reference resolution, but cannot supply historical IDs.
    reference: Literal["none", "focus", "first", "second", "third", "pair", "other"]


def strict_schema(model):
    """Responses strict JSON schema requires every property, nullable for optional fields."""
    schema = model.model_json_schema()
    def visit(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    return schema
