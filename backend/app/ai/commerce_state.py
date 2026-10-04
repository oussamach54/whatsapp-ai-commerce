"""Bounded application memory. Offer quotes are consent snapshots, never current catalog authority."""
from typing import Literal
from datetime import datetime
from uuid import UUID
from pydantic import Field
from app.ai.catalog_schemas import Contract, SearchProducts, ProductRef, Operation, AttributeMap, AttributeName, AttributeValue, CustomerIntent
from app.ai.schemas import LanguageStyle
from app.ai.checkout_state import Cart
from app.ai.cancellation_state import PendingCancellation

class Pending(Contract):
    operation: Operation
    missing: Literal["target", "variant", "attribute", "size", "color"]
    attribute_name: AttributeName | None = None
    authorization_message_id: UUID | None = None
    remaining_turns: int = Field(default=2, ge=0, le=2)
    candidates: list[ProductRef] = Field(default_factory=list, max_length=3)
    created_at: datetime | None = None
    purchase_requested: bool = False
    quantity: int = Field(default=1, ge=1, le=99)


class AttemptedTransition(Contract):
    """Unfulfilled intent, never an assertion that a matching variant exists."""
    target: ProductRef
    changed: AttributeMap = Field(default_factory=dict)
    preserved: AttributeMap = Field(default_factory=dict)
    hard: AttributeMap = Field(default_factory=dict)
    alternatives: list[ProductRef] = Field(default_factory=list, max_length=3)
    outcome: Literal["exact_miss", "alternatives", "constraint_miss"] = "exact_miss"


class PurchaseIntent(Contract):
    """A consent record, NOT a price authority, reservation, payment or order."""
    target: ProductRef
    quantity: int = Field(default=1, ge=1, le=99)
    status: Literal["awaiting_confirmation", "confirmed"] = "awaiting_confirmation"
    # What was offered, used only to detect a price change before confirmation.
    quoted_unit_price: str = Field(pattern=r"^\d{1,10}(\.\d{1,2})?$")
    currency: Literal["MAD"] = "MAD"
    request_message_id: UUID
    offered_at: datetime
    confirmation_message_id: UUID | None = None
    confirmed_at: datetime | None = None


class CommerceState(Contract):
    version: Literal[1] = 1
    operation: Operation = "details"
    constraints: SearchProducts = Field(default_factory=lambda: SearchProducts(in_stock_only=False))
    pending: Pending | None = None
    language: LanguageStyle = "french"
    previous_intent: Operation | None = None
    response_kind: Operation = "details"
    # Bounded identities only; useful when comparing recently discussed siblings.
    discussed: list[ProductRef] = Field(default_factory=list, max_length=3)
    # Requests are never evidence, even when their key is supported by the DB.
    requested_attributes: AttributeMap = Field(default_factory=dict)
    changed_attributes: AttributeMap = Field(default_factory=dict)
    # Only copied from freshly read DTOs. Re-read again before factual use.
    preserved_attributes: AttributeMap = Field(default_factory=dict)
    active_attribute: AttributeName | None = None
    unresolved_attribute_value: AttributeValue | None = None
    attempted: AttemptedTransition | None = None
    verified_attributes: AttributeMap = Field(default_factory=dict)
    hard_attributes: AttributeMap = Field(default_factory=dict)
    commercial_at: datetime | None = None
    commercial_ref: ProductRef | None = None
    explanation_context: Literal["product", "definition", "uncertain"] = "uncertain"
    intervening_turns: int = Field(default=0, ge=0, le=100)
    # A preserved focus is not necessarily the target of the latest list/question.
    target_ambiguous: bool = False
    # A failed image/link target must not borrow an older focus on a bare size.
    unresolved_input_reference: bool = False
    customer_intent: CustomerIntent | None = None
    purchase: PurchaseIntent | None = None
    cart: Cart | None = None
    cancellation: PendingCancellation | None = None


class Interpretation(Contract):
    """A model proposal, never an action authorization or factual evidence."""
    operation: Operation
    reference: Literal["none", "focus", "first", "second", "third", "pair", "other"] = "none"
    query: SearchProducts = Field(default_factory=SearchProducts)
    requested_attributes: AttributeMap = Field(default_factory=dict)
