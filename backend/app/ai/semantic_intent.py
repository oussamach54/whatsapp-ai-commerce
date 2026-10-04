"""Translate a validated planner proposal into the existing Turn operations.

This is not another conversation engine: identity, alternatives, commit/rollback,
confirmation and factual rendering remain application-owned Turn operations.
"""
from decimal import Decimal
import re

from app.ai.attribute_adapter import CATALOG_ATTRIBUTES as attributes
from app.ai.catalog_schemas import SearchProducts
from app.ai.catalog_renderer import local_catalog, generic_definition
from app.ai.commerce_state import Pending
from app.ai.schemas import SalesReply
from app.ai.scope import normalize


OPERATIONS = {
    "product_search": "search", "availability": "stock", "price": "price",
    "product_details": "details", "recommendation": "recommend", "comparison": "compare",
    "purchase": "select", "confirmation": "select", "cancellation": "cancel",
    "explanation": "explain", "business_question": "unsupported", "social": "social",
    "unknown": "clarify",
    "cart_edit": "details", "checkout": "details", "website_ordering": "select",
}


def execute_intent(turn, intent, text):
    from app.ai.catalog_orchestrator import resolve, budget_from_text
    from app.ai.business_knowledge import answer_business_question
    if getattr(turn, "availability_only", False) and (intent.intent != "availability" or intent.purchase_intent):
        return turn.clarify("stock")
    # Discovery/detail labels can coexist with an explicit desire to buy. Keep
    # that independent semantic signal; facts and final consent stay app-owned.
    if intent.purchase_intent and intent.intent in ("product_search", "product_details", "availability", "price"):
        intent = intent.model_copy(update={"intent": "purchase"})
    website = intent.intent == "website_ordering"
    has_target = bool(intent.items or intent.terms or intent.product_reference or intent.attributes or intent.reference != "none")
    new_unspecified_purchase = (intent.intent == "purchase" and not has_target
        and turn.state.cart is not None and turn.state.cart.status == "completed")
    if website or new_unspecified_purchase:
        from app.ai.checkout import phrase, reply as cart_reply
        if intent.speech_act in ("negative", "quoted") or not intent.evidence or normalize(intent.evidence) != normalize(text):
            return turn.clarify("details")
        if not has_target:
            turn.catalog.semantic_handled = True
            turn.style = intent.language
            turn.state.customer_intent = intent
            if turn.state.cart and turn.state.cart.status == "completed":
                turn.state.cart = None
                turn.state.purchase = None
                turn.checkout_dirty = True
            if turn.state.cart and turn.state.cart.items and turn.state.cart.status not in ("cancelled", "blocked"):
                return cart_reply(turn, "missing" if turn.state.cart.status == "confirmed" else "offer")
            # Reuse the existing target-clarification state, not a new checkout.
            turn.pending = Pending(operation="search", missing="target", purchase_requested=True)
            return SalesReply(phrase(turn.style,
                "Pas de souci 😊 Vous pouvez commander directement ici. Que souhaitez-vous prendre ?",
                "No problem 😊 You can order directly here. What would you like?",
                "Pas de souci 😊 T9der tcommandi directement hna. Chno bghiti takhod?",
                "ما كاين مشكل 😊 تقدر تدوز الطلبية مباشرة هنا. شنو بغيتي تاخد؟"),
                catalog_refs=turn.refs.model_dump(mode="json"))
        intent = intent.model_copy(update={"intent": "purchase", "purchase_intent": True})
    turn.catalog.semantic_handled = True
    turn.operation = OPERATIONS[intent.intent]
    turn.style = intent.language
    turn.state.language = intent.language
    turn.state.customer_intent = intent
    # Policy requests must precede attribute validation: quantity is not a
    # catalog attribute, even if a model redundantly emits it in attributes.
    if intent.intent == "business_question":
        turn.pending = None
        turn.state.requested_attributes = {}
        text = answer_business_question(intent.business_topic, intent.quantity, turn.style, turn.catalog.settings)
        if (turn.state.cart and turn.state.cart.status == "awaiting_confirmation"
                and not (turn.state.cancellation and turn.state.cancellation.status in ("choosing", "awaiting_confirmation"))):
            # Keep the policy interruption flow, but make consent refer to a
            # newly delivered explicit checkout offer rather than an older one.
            from app.ai.checkout import render_cart
            text += "\n\n" + render_cart(turn)
        return SalesReply(text,
                          catalog_refs=turn.refs.model_dump(mode="json"))
    from app.ai.checkout import propose, receive_fields
    if intent.intent in ("cart_edit", "checkout") or (intent.intent == "purchase" and intent.items):
        return propose(turn, intent, text)
    if intent.checkout:
        receive_fields(turn, intent.checkout, text)
    requested = attributes.normalize_values({a.name: a.value for a in intent.attributes})
    if set(requested) - set(attributes.supported):
        turn.pending = Pending(operation="details", missing="attribute")
        turn.state.requested_attributes = requested
        return local_catalog("attribute_unverified", turn.style)
    explicit_budget = budget_from_text(text)
    budget = explicit_budget if explicit_budget is not None else (
        Decimal(turn.state.constraints.max_price) if turn.state.constraints.max_price else None)
    if intent.budget is not None:
        budget = min(budget, Decimal(intent.budget)) if budget is not None else Decimal(intent.budget)
    if budget is not None:
        turn.state.constraints.max_price = str(budget)
    if intent.intent == "confirmation":
        # Model confirmation labels cannot authorize a selection. The application
        # requires an explicit affirmative answer to its own current offer.
        return turn.confirm_purchase(text)
    if intent.intent == "cancellation":
        # A model may propose a request, never an order identity or consent.
        # Only literal current evidence and affirmative request speech can prompt.
        from app.ai.order_cancellation import request
        from app.services.checkout_service import answer
        if (intent.speech_act not in ("affirmative", "question") or not intent.evidence
                or normalize(intent.evidence) != normalize(text) or answer(text) is not None):
            return turn.clarify("details")
        if (intent.speech_act in ("affirmative", "question") and intent.evidence
                and normalize(intent.evidence) == normalize(text)
                and (not turn.state.cart or turn.state.cart.status == "completed"
                     or re.search(r"\b(?:order|commande)\b", normalize(text)))):
            return request(turn, text)
        if turn.state.cart:
            from app.ai.checkout import confirm, reply
            if turn.state.cart.status == "completed":
                return reply(turn, "existing")
            # Semantic cancellation can abandon a draft, never a persisted order.
            return confirm(turn, "cancel")
        # A semantic cancellation clears an unconfirmed proposal, never an order
        # or an existing selection without the existing local authorization.
        turn.operation = "details"
        turn.state.purchase = None
        return SalesReply({"english": "We can leave that choice for now.", "french": "On peut laisser ce choix de côté.",
            "darija_latin": "Nkhalliw had choix daba.", "darija_arabic": "نخليو هاد الاختيار دابا."}[
                "darija_latin" if turn.style == "mixed" else turn.style], catalog_refs=turn.refs.model_dump(mode="json"))
    if intent.intent in ("unknown", "social"):
        return turn.clarify("details")
    if intent.intent == "explanation":
        if turn.relevant() and not turn.state.target_ambiguous:
            return turn.detail([turn.refs.focus])
        return generic_definition(turn.style, turn.refs) if turn.state.explanation_context == "definition" else turn.clarify("explain")

    terms = intent.terms
    if not terms and intent.product_reference:
        from app.ai.conversation_engine import query_body
        named = query_body(intent.product_reference)
        terms = named.terms if named else []
    query = SearchProducts(terms=terms, in_stock_only=False,
                           max_price=str(budget) if budget is not None else None, **attributes.filters(requested))
    targets = resolve(intent.reference, turn.refs) if intent.reference != "none" else []
    if intent.intent == "purchase" and not intent.terms and len(turn.refs.presented) == 1:
        targets = turn.refs.presented
    if intent.reference == "focus" and turn.state.target_ambiguous and not requested:
        return turn.clarify(turn.operation)
    if intent.intent in ("recommendation", "comparison"):
        if targets or (not intent.terms and (turn.refs.focus or turn.state.discussed)):
            if requested:
                turn.state.constraints = attributes.apply(turn.state.constraints, requested)
                turn.state.hard_attributes.update(requested)
            return turn.comparison(targets if intent.reference == "pair" else None)
        query.in_stock_only = True
        return turn.named(query, False)

    purchase = intent.intent == "purchase"
    if purchase and (intent.speech_act != "affirmative" or not intent.evidence
                     or normalize(intent.evidence) != normalize(text)):
        return turn.clarify("details")
    turn.purchase_requested = purchase
    turn.purchase_quantity = intent.quantity or 1
    if requested:
        turn.state.requested_attributes = requested
    # The model may identify a desire to buy, but cannot create/change selection.
    # A resolved proposal is summarized and requires a subsequent customer answer.
    if targets:
        reply = turn.detail(targets, changes=requested or None)
    elif intent.reference != "none":
        reply = turn.clarify(turn.operation)
    elif query.terms:
        reply = turn.named(query, False)
    elif turn.refs.focus and requested:
        reply = turn.detail([turn.refs.focus], changes=requested)
    else:
        reply = turn.clarify(turn.operation)
    return reply
