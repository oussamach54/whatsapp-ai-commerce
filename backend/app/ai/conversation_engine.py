"""Application-owned turn transitions around the bounded semantic catalog planner.

Customer language identifies a query, database DTOs establish facts, and only
locally authorized choice acts can change selection. No order actions exist here.
"""
import json
import re
from datetime import datetime
from decimal import Decimal
from time import monotonic

from app.ai.catalog_schemas import CatalogRefs, GetProducts, ProductRef, ResponsePlan, SearchProducts, Selection
from app.ai.catalog_renderer import (
    local_catalog, render, words, render_alternatives, attribute_difference,
    generic_definition, render_explanation, target_question,
)
from app.ai.commerce_state import CommerceState, Pending, AttemptedTransition, PurchaseIntent
from app.ai.schemas import SalesReply
from app.ai.scope import affirmative_selection, explanation_topic, explanation_target, normalize
from app.ai.attribute_adapter import CATALOG_ATTRIBUTES as attribute_adapter, AttributeInput

CHOICE = r"(?:(?:safi\s+)?(?:nakhod|khod lia|bghit|je prends|je choisis|je veux commander|je veux|i choose|i take|i want to order|بغيت|ناخد)\s+)"
CORRECTION = r"(?:la(?: la)?|non)\s*,?\s*(?:finalement\s*)?"
CANCEL = re.compile(r"^(?:ma b9itch bghito|annule (?:choix dyali|mon choix|ma selection)|cancel (?:my )?selection|لغي الاختيار)[.! ]*$")
PRICE = re.compile(r"^(?:ch7al(?: taman)?|combien|quel prix|how much|price|prix|شحال(?: الثمن)?)[ ؟?!.]*$")
STOCK = re.compile(r"^(?:wach )?kayn[ ؟?!.]*$|^(?:available|in stock|disponible|متوفر)[ ؟?!.]*$")


def confirmation_answer(text):
    """Small authorization vocabulary for an application-issued offer, not NLU."""
    value = normalize(text).strip(" .!")
    if value in ("oui", "yes", "je confirme", "i confirm", "نعم", "نأكد", "ايه"):
        return True
    if value in ("non", "no", "لا"):
        return False
    return None


def choice_body(text, selected=False):
    value = normalize(text).rstrip(" .!")
    if selected:
        value = re.sub("^" + CORRECTION, "", value)
        value = re.sub(r"\s+finalement$", "", value)
        value = re.sub(r"^(?:change-le pour|change to|بدلو ب)\s+", "je prends ", value)
    if re.search(r"[?؟\"'«»;:\n]|\b(?:pas|ne|not|if|si|mais|but|ou|or|et|and|example|exemple|non|la|machi|واش|لا|ما|مثال)\b", value):
        return None
    if "," in value:
        return None
    match = re.match("^" + CHOICE, value)
    if match:
        return value[match.end():] or None
    if selected and re.match("^" + CORRECTION, normalize(text)):
        return value or None
    match = re.fullmatch(r"safi\s+(.+)", value)
    return match[1] if match else None


def attributes(text):
    return attribute_adapter.supported_request(text)[0]


def query_body(body):
    """Conservative lexical query for explicit names; unfamiliar syntax is semantic."""
    terms, attrs = attribute_adapter.query_parts(body)
    if len(terms) > 6 or any(len(t) > 64 for t in terms):
        return None
    return SearchProducts(terms=terms, in_stock_only=False, **attrs)


def discovery_query(text):
    """A short explicit product query works the same way for every category.

Gifts, budgets, broadening and descriptive needs retain semantic interpretation.
"""
    value = normalize(text).strip(" .!?")
    match = re.fullmatch(r"(?:bghit|je cherche|i am looking for)\s+([\w\sàâéèêîôùûç'-]+)", value)
    if not match or re.search(r"\d|\b(?:cadeau|gift|budget|tous|toutes|all|autre|autres|other|quelque|something|moins|under|produits?|products?|options?)\b", match[1]):
        return None
    query = query_body(match[1])
    return query if query and 1 <= len(query.terms) <= 6 else None


def contextual_shortcut(text):
    from app.ai.catalog_orchestrator import attribute_change, reference_hint
    return bool(attribute_adapter.inspect(text).followup or attribute_adapter.inspect(text).requested or attribute_change(text) or PRICE.fullmatch(normalize(text)) or STOCK.fullmatch(normalize(text))
                or CANCEL.fullmatch(normalize(text)) or choice_body(text, True)
                or reference_hint(text) != "none" or re.search(r"\b(?:arkhess|ahsan|katnsa7ni)\b", normalize(text))
                or re.fullmatch(r"3ndi\s+\d+\s*(?:dh|mad)", normalize(text)))


def load_memory(catalog):
    from app.services.conversation_service import recent_catalog_refs
    from app.models import Message
    with catalog.sessions() as db:
        catalog.authorize(db)
        refs, source = recent_catalog_refs(db, catalog.target.conversation_id, catalog.target.inbound_id)
        row = db.get(Message, source) if source else None
        try:
            state = CommerceState.model_validate_json(json.dumps((row.metadata_ or {}).get("commerce_state", {}))) if row else CommerceState()
        except (ValueError, TypeError):
            state = CommerceState()
        anchor = db.get(Message, catalog.target.inbound_id)
        def received(message):
            return datetime.fromisoformat(message.metadata_["received_at"]) if (message.metadata_ or {}).get("received_at") else message.created_at
        catalog.turn_time = received(anchor)
        from app.services.checkout_service import latest_cart
        cart, catalog.checkout_checkpoint = latest_cart(db, catalog)
        if catalog.checkout_checkpoint is not None:
            state.cart = cart
        if state.purchase and not 0 <= (catalog.turn_time - state.purchase.offered_at).total_seconds() <= 900:
            state.purchase = None
        if state.pending and state.pending.created_at and not 0 <= (catalog.turn_time - state.pending.created_at).total_seconds() <= 900:
            state.pending = None
        # Pending consent is tied to a real customer utterance, not an LLM label.
        authorized = False
        if state.pending and state.pending.remaining_turns:
            origin = db.get(Message, state.pending.authorization_message_id) if state.pending.authorization_message_id else None
            from app.models.enums import MessageDirection, SenderType
            authorized = bool(origin and origin.conversation_id == catalog.target.conversation_id
                and origin.direction == MessageDirection.INBOUND and origin.sender_type == SenderType.CUSTOMER
                and 0 <= (received(anchor) - received(origin)).total_seconds() <= 900
                and (affirmative_selection(origin.content or "") or choice_body(origin.content or "", True)))
        return refs, source, state, authorized


def carry_social(catalog, reply, style):
    refs, _, state, _ = load_memory(catalog)
    if not refs.presented and not refs.focus and not refs.selection and not state.pending:
        return reply
    state.previous_intent, state.operation, state.response_kind, state.language = state.operation, "social", "social", style
    state.intervening_turns = min(100, state.intervening_turns + 1)
    if state.pending:
        state.pending.remaining_turns -= 1
        if not state.pending.remaining_turns:
            state.pending = None
    reply.catalog_refs = refs.model_dump(mode="json")
    reply.commerce_state = state.model_dump(mode="json")
    return reply


class Turn:
    def __init__(self, catalog, refs, source, state, style):
        self.catalog, self.refs, self.source, self.state, self.style = catalog, refs, source, state, style
        self.operation, self.path = "details", "deterministic"
        self.lookup, self.selected, self.pending = False, False, None
        self.replace_focus = False
        self.trusted = state.model_copy(deep=True)
        self.outcome = "unchanged"
        self.invalid = []
        self.published_alternatives = False
        self.explicit_explanation = False
        self.new_hard = {}
        self.purchase_requested = False
        self.purchase_quantity = 1
        self.confirming = False
        self.checkout_checkpoint = getattr(catalog, "checkout_checkpoint", None)
        self.checkout_dirty = False

    def offer_from_reply(self, reply):
        from app.ai.catalog_renderer import purchase_reply
        if self.outcome != "verified" or not reply.catalog_refs:
            return reply
        focus = reply.catalog_refs.get("focus")
        if self.purchase_requested and (not focus or not focus.get("variant_id")):
            missing = "variant" if focus else "target"
            self.pending = Pending(operation="select", missing=missing,
                purchase_requested=True, quantity=self.purchase_quantity)
            reply.text += "\n" + words(self.style)["variant" if focus else "product"]
            return reply
        if not focus:
            return reply
        ref = ProductRef.model_validate_json(json.dumps(reply.catalog_refs["focus"]))
        if not ref.variant_id:
            return reply
        result = self.catalog.get(GetProducts(product_ids=[ref.product_id], variant_ids=[ref.variant_id]))
        self.lookup = True
        if not result.products:
            return self.invalidate(ref)
        product, variant = result.products[0], result.products[0].variants[0]
        if self.state.constraints.max_price and Decimal(variant.price) > Decimal(self.state.constraints.max_price):
            self.state.purchase = None
            return local_catalog("no_matches", self.style)
        if variant.stock_quantity < self.purchase_quantity:
            self.state.purchase = None
            reply.catalog_refs["selection"] = self.refs.model_dump(mode="json")["selection"]
            reply.text = purchase_reply(product, variant, self.style, self.purchase_quantity, "unavailable")
            return reply
        self.state.purchase = PurchaseIntent(target=ref, quantity=self.purchase_quantity,
            quoted_unit_price=variant.price, request_message_id=self.catalog.target.inbound_id,
            offered_at=self.catalog.turn_time)
        from app.ai.checkout import offer_single
        reply.text = offer_single(self, product, variant, self.purchase_quantity)
        return reply

    def confirm_purchase(self, text):
        if self.state.cart:
            from app.ai.checkout import confirm
            return confirm(self, text)
        from app.ai.catalog_renderer import purchase_reply
        purchase = self.state.purchase
        answer = confirmation_answer(text)
        if not purchase or answer is None:
            return self.clarify("details")
        if not answer:
            self.state.purchase = None
            return SalesReply({"english": "Okay, we can leave that choice for now.", "french": "D’accord, on laisse ce choix de côté.",
                "darija_latin": "Wakha, nkhalliw had choix daba.", "darija_arabic": "واخا، نخليو هاد الاختيار دابا."}[
                    "darija_latin" if self.style == "mixed" else self.style], catalog_refs=self.refs.model_dump(mode="json"))
        # The consent origin must be a real inbound customer message in this conversation.
        from app.models import Message
        from app.models.enums import MessageDirection, SenderType
        with self.catalog.sessions() as db:
            self.catalog.authorize(db)
            origin = db.get(Message, purchase.request_message_id)
            if not origin or origin.conversation_id != self.catalog.target.conversation_id or origin.direction != MessageDirection.INBOUND or origin.sender_type != SenderType.CUSTOMER:
                self.state.purchase = None
                return self.clarify("details")
        self.operation = "select"
        self.confirming = True
        ref = purchase.target
        result = self.catalog.get(GetProducts(product_ids=[ref.product_id], variant_ids=[ref.variant_id]))
        self.lookup = True
        if not result.products:
            self.state.purchase = None
            return self.invalidate(ref)
        product, variant = result.products[0], result.products[0].variants[0]
        if variant.stock_quantity < purchase.quantity:
            self.state.purchase = None
            return SalesReply(purchase_reply(product, variant, self.style, purchase.quantity, "unavailable"),
                              catalog_refs=self.refs.model_dump(mode="json"))
        # Legacy Step 6 choice metadata is not a delivered cart/version. Upgrade
        # it to a fresh verified order proposal, never claim a choice/order was
        # confirmed or bypass the normal cart confirmation provenance.
        from app.ai.checkout import offer_single
        self.outcome = "verified"
        self.state.verified_attributes = attribute_adapter.verified(variant)
        self.state.target_ambiguous = False
        self.state.attempted = None
        return SalesReply(offer_single(self, product, variant, purchase.quantity),
                          catalog_refs=self.refs.model_dump(mode="json"))

    def relevant(self):
        return bool(self.state.commercial_at and self.state.commercial_ref == self.refs.focus
                    and self.state.explanation_context == "product"
                    and self.state.intervening_turns <= 3
                    and 0 <= (self.catalog.turn_time - self.state.commercial_at).total_seconds() <= 900)

    def invalidate(self, ref):
        if ref.variant_id:
            parent = self.catalog.get(GetProducts(product_ids=[ref.product_id]))
            if not parent.products:
                ref = ProductRef(product_id=ref.product_id)
        self.invalid.append(ref)
        self.outcome = "invalid"
        return local_catalog("product_unavailable", self.style)

    def miss(self, ref, changes, preserved, budget=None):
        """Keep the unsuccessful request apart from the committed commercial state."""
        self.outcome = "miss"
        attempted = AttemptedTransition(target=ref, changed=changes or {}, preserved=preserved,
                                         hard=self.state.hard_attributes)
        self.state.attempted = attempted
        if not changes or not preserved:
            return local_catalog("no_matches" if budget is not None else "variant_not_found", self.style)
        candidates = {}
        product = None
        truncated = False
        for filters in attribute_adapter.relaxation_filters(changes, preserved, attempted.hard):
            result = self.catalog.get(GetProducts(product_ids=[ref.product_id], **filters))
            if not result.products:
                return self.invalidate(ProductRef(product_id=ref.product_id))
            product = result.products[0]
            truncated |= product.has_more_variants
            for variant in product.variants:
                if budget is None or Decimal(variant.price) <= budget:
                    candidates[variant.id] = variant
        requested = preserved | changes
        ranked = sorted(candidates.values(), key=lambda v: (
            len(attribute_adapter.differences(v, requested)), v.stock_quantity <= 0, Decimal(v.price), str(v.id)))
        if not ranked:
            return local_catalog("no_matches" if budget is not None else "variant_not_found", self.style)
        attempted.alternatives = [ProductRef(product_id=ref.product_id, variant_id=v.id) for v in ranked[:3]]
        attempted.outcome = "alternatives"
        self.state.target_ambiguous = True
        self.published_alternatives = True
        updated = self.refs.model_copy(deep=True, update={"presented": attempted.alternatives})
        return render_alternatives(product, ranked[:3], requested, updated, self.style,
                                   self.catalog.settings.catalog_currency, truncated or len(ranked) > 3)

    def pending_target(self, text, hint, candidates):
        from app.ai.catalog_orchestrator import resolve
        if hint != "none":
            return resolve(hint, CatalogRefs(presented=candidates, focus=self.refs.focus))
        query = query_body(text)
        if not query:
            return []
        matched = []
        for ref in candidates[:3]:
            result = self.catalog.get(GetProducts(product_ids=[ref.product_id], variant_ids=[ref.variant_id] if ref.variant_id else []))
            self.lookup = True
            if not result.products:
                self.invalid.append(ref)
                continue
            product = result.products[0]
            for variant in product.variants:
                label = normalize(product.name + " " + variant.name)
                if (all(term in label for term in query.terms)
                        and attribute_adapter.matches(variant, attribute_adapter.from_query(query))):
                    matched.append(ProductRef(product_id=product.id, variant_id=variant.id))
        return list({(r.product_id, r.variant_id): r for r in matched}.values())

    def clarify(self, operation, missing="target", authorization=None):
        candidates = self.refs.presented[:3]
        self.pending = Pending(operation=operation, missing=missing, authorization_message_id=authorization,
                               candidates=candidates, created_at=self.catalog.turn_time,
                               purchase_requested=self.purchase_requested, quantity=self.purchase_quantity)
        reply = local_catalog("product" if missing == "target" else "variant", self.style)
        if missing == "target" and candidates and operation in ("price", "stock"):
            labels = []
            for ref in candidates:
                result = self.catalog.get(GetProducts(product_ids=[ref.product_id], variant_ids=[ref.variant_id] if ref.variant_id else []))
                self.lookup = True
                if result.products:
                    product = result.products[0]
                    labels.append(product.name + (" — " + product.variants[0].name if ref.variant_id and product.variants else ""))
                else:
                    self.invalid.append(ref)
            if labels:
                reply.text = target_question(self.style) + "\n" + "\n".join(f"{i}. {label}" for i, label in enumerate(labels, 1))
        return reply

    def detail(self, targets, changes=None, select=False, budget=None, available_only=False):
        if len(targets) != 1:
            return self.clarify("select" if select else self.operation, authorization=self.catalog.target.inbound_id if select else None)
        ref = targets[0]
        self.lookup = True
        preserved = {}
        if changes and ref.variant_id:
            old = self.catalog.get(GetProducts(product_ids=[ref.product_id], variant_ids=[ref.variant_id]))
            if not old.products:
                return self.invalidate(ref)
            preserved, _ = attribute_adapter.transition(old.products[0].variants[0], changes)
        if changes:
            self.state.changed_attributes = dict(changes)
            self.state.preserved_attributes = preserved
        requested = attribute_adapter.filters(preserved | (changes or {}))
        if budget is None and self.state.constraints.max_price:
            budget = Decimal(self.state.constraints.max_price)
        result = self.catalog.get(GetProducts(product_ids=[ref.product_id],
            variant_ids=[ref.variant_id] if ref.variant_id and not changes else [],
            **requested))
        if not result.products:
            return self.invalidate(ref)
        product = result.products[0]
        exact_exists = bool(product.variants)
        if budget is not None:
            product.variants = [v for v in product.variants if Decimal(v.price) <= budget]
        if available_only:
            product.variants = [v for v in product.variants if v.stock_quantity > 0]
        if not product.variants:
            if exact_exists:
                self.outcome = "miss"
                self.state.attempted = AttemptedTransition(target=ref, changed=changes or {},
                    preserved=preserved, hard=self.state.hard_attributes, outcome="constraint_miss")
                return local_catalog("no_matches", self.style)
            return self.miss(ref, changes, preserved, budget)
        self.outcome = "verified"
        self.state.attempted = None
        self.state.target_ambiguous = False
        variant = product.variants[0] if len(product.variants) == 1 and not product.has_more_variants else None
        product_choice = select and ref.variant_id is None and not changes
        if select and variant and variant.stock_quantity <= 0 and not product_choice:
            select = False
        if select and (variant is None or product_choice):
            self.pending = Pending(operation="select", missing="variant", authorization_message_id=self.catalog.target.inbound_id)
            select = product_choice
        target = ProductRef(product_id=product.id, variant_id=variant.id if variant and not product_choice else None)
        plan = ResponsePlan(action="select" if select else "show", items=[target], question=None, reference="focus")
        provenance = self.source if not changes and not self.replace_focus else self.catalog.target.inbound_id
        reply = render(plan, {product.id: product}, self.style, result.currency, provenance, budget)
        self.selected = select
        if variant and not select:
            if self.operation == "price":
                reply.text = f"{variant.price} {result.currency}."
            elif self.operation == "explain":
                reply = render_explanation(product, variant, CatalogRefs.model_validate_json(json.dumps(reply.catalog_refs)),
                                           "out_of_stock" if self.explicit_explanation else "state", self.style, result.currency)
            else:
                reply.text = f"{product.name} — {variant.name}: {variant.price} {result.currency}; {words(self.style)[variant.availability]}."
        if variant:
            self.state.verified_attributes = attribute_adapter.verified(variant)
        if self.pending and not select:
            reply.text += "\n" + words(self.style)["variant"]
        return reply

    def named(self, query, select, authorization=None, semantic_fallback=False):
        self.lookup = True
        if query.max_price is None:
            query.max_price = self.state.constraints.max_price
        result = self.catalog.search(query)
        if semantic_fallback and len(query.terms) > 1 and not result.products:
            # A lexical choice prefix does not prove its remaining words are a
            # product name. Let the existing semantic planner interpret unknown
            # verbs/phrasing, but keep single-name misses and a known product's
            # variant miss local without an unnecessary model call.
            parents = self.catalog.search(SearchProducts(terms=query.terms, in_stock_only=False))
            if not parents.products:
                self.operation = "details"
                return None
        self.replace_focus = True
        self.state.constraints = query
        self.state.requested_attributes = attribute_adapter.from_query(query)
        self.state.active_attribute = next(iter(self.state.requested_attributes)) if len(self.state.requested_attributes) == 1 else None
        self.state.changed_attributes = {}
        self.state.preserved_attributes = {}
        self.state.unresolved_attribute_value = None
        self.state.attempted = None
        self.state.hard_attributes = dict(self.new_hard)
        if not result.products:
            self.outcome = "miss"
            return local_catalog("no_matches", self.style)
        self.outcome = "verified"
        if len(result.products) != 1 or result.has_more:
            plan = ResponsePlan(action="show", items=[ProductRef(product_id=p.id) for p in result.products[:3]], question=None, reference="none")
            reply = render(plan, {p.id: p for p in result.products}, self.style, result.currency)
            if select:
                self.pending = Pending(operation="select", missing="target", authorization_message_id=authorization or self.catalog.target.inbound_id)
                reply.text += "\n" + words(self.style)["product"]
            return reply
        product = result.products[0]
        # Descriptions and SKUs may find candidates, but cannot authorize a named
        # product choice when the name/category/brand does not match the query.
        if select and any(t not in normalize(" ".join(filter(None, (product.name, product.category, product.brand)))) for t in query.terms):
            return self.clarify("select", authorization=authorization or self.catalog.target.inbound_id)
        reply = self.detail([ProductRef(product_id=product.id)],
                            attribute_adapter.from_query(query), select,
                            Decimal(query.max_price) if query.max_price else None, query.in_stock_only)
        if self.pending and authorization:
            self.pending.authorization_message_id = authorization
        return reply

    def comparison(self, explicit_targets=None):
        if set(self.state.requested_attributes) - set(attribute_adapter.supported):
            self.pending = Pending(operation="search", missing="attribute", attribute_name=self.state.active_attribute)
            return local_catalog("attribute_unverified", self.style)
        attempted = self.state.attempted
        targets = self.state.discussed or self.refs.presented
        if attempted:
            targets = [attempted.target] + attempted.alternatives
        elif self.refs.focus and self.relevant():
            targets = [self.refs.focus] + targets
        if not targets:
            return self.clarify("search")
        self.lookup = True
        candidates = []
        budget = Decimal(self.state.constraints.max_price) if self.state.constraints.max_price else None
        requested = attribute_adapter.from_query(self.state.constraints)
        # Compare the dimension the customer changed while preserving the other
        # verified dimensions. This works for any adapter-supported attribute.
        comparison_constraints = ({} if attempted else {key: value for key, value in requested.items()
                                  if key not in self.state.changed_attributes}) | self.state.hard_attributes
        queries = [GetProducts(product_ids=[parent], **attribute_adapter.filters(comparison_constraints))
                   for parent in list(dict.fromkeys(ref.product_id for ref in targets))[:3]]
        if explicit_targets:
            queries = [GetProducts(product_ids=[ref.product_id], variant_ids=[ref.variant_id] if ref.variant_id else []) for ref in explicit_targets[:3]]
        for query in queries:
            result = self.catalog.get(query)
            if not result.products:
                self.invalid.append(ProductRef(product_id=query.product_ids[0], variant_id=query.variant_ids[0] if query.variant_ids else None))
            for product in result.products:
                for variant in product.variants:
                    if attribute_adapter.matches(variant, comparison_constraints) and (budget is None or Decimal(variant.price) <= budget):
                        candidates.append((product, variant))
        if attempted and not explicit_targets:
            filters = [attempted.preserved | attempted.changed | attempted.hard]
            filters += attribute_adapter.relaxation_filters(attempted.changed, attempted.preserved, attempted.hard)
            for attrs in filters:
                result = self.catalog.get(GetProducts(product_ids=[attempted.target.product_id], **attribute_adapter.filters(attrs)))
                for product in result.products:
                    for variant in product.variants:
                        if attribute_adapter.matches(variant, comparison_constraints) and (budget is None or Decimal(variant.price) <= budget):
                            candidates.append((product, variant))
        # A bounded product read may omit a known alternative; re-read its exact ID.
        if attempted and not explicit_targets:
            for ref in attempted.alternatives:
                result = self.catalog.get(GetProducts(product_ids=[ref.product_id], variant_ids=[ref.variant_id]))
                if not result.products:
                    self.invalid.append(ref)
                for product in result.products:
                    for variant in product.variants:
                        if attribute_adapter.matches(variant, comparison_constraints) and (budget is None or Decimal(variant.price) <= budget):
                            candidates.append((product, variant))
        candidates = list({v.id: (p, v) for p, v in candidates}.values())
        if not candidates:
            return local_catalog("no_matches", self.style)
        candidates = sorted(candidates, key=lambda pair: (
            len(attribute_adapter.differences(pair[1], attempted.changed)) if attempted else 0,
            pair[1].stock_quantity <= 0, Decimal(pair[1].price), str(pair[1].id)))[:3]
        lang = "darija_latin" if self.style == "mixed" else self.style
        intro = {"english": "By current price and availability (your preference matters):",
                 "french": "Selon le prix et le stock actuels (ça dépend de vos préférences) :",
                 "darija_latin": "7sab taman w stock daba; l choix 7sab chno katfeddel:",
                 "darija_arabic": "حسب الثمن والمخزون دابا؛ الاختيار حسب شنو كتفضل:"}[lang]
        lines = [intro]
        if len(candidates) >= 2:
            from app.ai.catalog_renderer import price_difference
            difference = price_difference(candidates[0][1], candidates[1][1], self.style)
            if difference:
                lines = [difference]
        shown = []
        for product, variant in candidates:
            ref = ProductRef(product_id=product.id, variant_id=variant.id)
            if ref in shown:
                continue
            shown.append(ref)
            mismatch = attribute_difference(variant, attempted.preserved | attempted.changed, self.style) if attempted else ""
            lines.append(f"{len(shown)}. {product.name} — {variant.name}: {variant.price} {self.catalog.settings.catalog_currency}; {words(self.style)[variant.availability]}{mismatch}.")
        updated = self.refs.model_copy(update={"presented": shown})
        self.state.target_ambiguous = bool(attempted) or len(shown) > 1
        return SalesReply("\n".join(lines), catalog_refs=updated.model_dump(mode="json"))

    def finish(self, reply):
        if reply.text == words(self.style)["unavailable"]:
            reply.catalog_refs = self.refs.model_dump(mode="json")
            reply.commerce_state = self.trusted.model_dump(mode="json")
            return reply
        if reply.text in (words(self.style)["no_matches"], words(self.style)["variant_not_found"],
                           words(self.style)["attribute_variant_not_found"]):
            self.outcome = "miss"
        if self.outcome in ("miss", "invalid"):
            attempt = self.state.attempted
            budget = self.state.constraints.max_price
            intent = self.state.customer_intent
            self.state = self.trusted.model_copy(deep=True)
            self.state.customer_intent = intent
            self.state.attempted = attempt
            self.state.constraints.max_price = budget
            self.state.hard_attributes = self.state.hard_attributes | self.new_hard
            self.state.target_ambiguous = bool(attempt and attempt.alternatives)
            if self.replace_focus:
                self.state.commercial_ref = self.state.commercial_at = None
                self.state.target_ambiguous = True
        new = CatalogRefs.model_validate_json(json.dumps(reply.catalog_refs)) if reply.catalog_refs else self.refs.model_copy(deep=True)
        if not new.focus and not new.presented and (not self.replace_focus or self.outcome == "miss"):
            new = self.refs.model_copy(deep=True)
        if self.operation != "cancel" and new.selection is None:
            new.selection = self.refs.selection
        if self.operation == "cancel":
            new.selection = None
        # Price/stock/explanation and sibling browsing do not renumber a result list.
        if not self.published_alternatives and not self.replace_focus and self.operation in ("price", "stock", "variant", "explain", "select", "change", "details") and self.refs.presented and new.focus:
            new.presented = self.refs.presented
        if self.operation == "search" and self.outcome != "miss":
            self.state.discussed = new.presented[:3]
        if new.focus and new.focus not in self.state.discussed:
            self.state.discussed = (self.state.discussed + [new.focus])[-3:]
        self.state.previous_intent, self.state.operation = self.state.operation, self.operation
        self.state.language, self.state.response_kind = self.style, self.operation
        self.state.pending = self.pending
        def valid(ref):
            return ref is not None and not any(ref.product_id == bad.product_id and
                (bad.variant_id is None or ref.variant_id == bad.variant_id) for bad in self.invalid)
        if self.invalid:
            new.focus = new.focus if valid(new.focus) else None
            new.selection = new.selection if valid(new.selection) else None
            new.presented = [ref for ref in new.presented if valid(ref)]
            self.state.discussed = [ref for ref in self.state.discussed if valid(ref)]
            if self.state.pending:
                self.state.pending.candidates = [ref for ref in self.state.pending.candidates if valid(ref)]
            if self.state.attempted:
                self.state.attempted.alternatives = [ref for ref in self.state.attempted.alternatives if valid(ref)]
                if not valid(self.state.attempted.target):
                    self.state.attempted = None
            if not valid(self.state.commercial_ref):
                self.state.commercial_ref = self.state.commercial_at = None
                self.state.verified_attributes = {}
            if self.state.purchase and not valid(self.state.purchase.target):
                self.state.purchase = None
        if self.state.purchase and not self.confirming and (
                new.focus != self.state.purchase.target or self.state.target_ambiguous
                or self.operation in ("cancel", "recommend", "compare", "unsupported") or self.outcome == "miss"):
            self.state.purchase = None
        if self.outcome == "verified" and new.focus:
            self.state.commercial_at = self.catalog.turn_time
            self.state.commercial_ref = new.focus
            self.state.intervening_turns = 0
            self.state.explanation_context = "product"
        else:
            self.state.intervening_turns = min(100, self.state.intervening_turns + 1)
            if self.outcome != "generic":
                self.state.explanation_context = "uncertain"
            if self.outcome == "verified":
                self.state.commercial_at = self.state.commercial_ref = None
                self.state.verified_attributes = {}
        if self.state.pending and self.state.pending.created_at is None:
            self.state.pending.created_at = self.catalog.turn_time
            self.state.pending.candidates = new.presented[:3]
        reply.catalog_refs = new.model_dump(mode="json")
        reply.commerce_state = self.state.model_dump(mode="json")
        return reply


def run_turn(service, text, history, style, admission, catalog, semantic):
    from app.ai.catalog_orchestrator import attribute_change, reference_hint, resolve, budget_from_text
    try:
        refs, source, state, pending_authorized = load_memory(catalog)
    except Exception:
        admission.safe_record(fallback=True, failure_category="catalog_memory_unavailable")
        return local_catalog("unavailable", style)
    original_state = state.model_copy(deep=True)
    state = state.model_copy(deep=True)
    turn = Turn(catalog, refs, source, state, style)
    catalog.turn_state = state
    catalog.conversation_turn = turn
    catalog.turn_action = catalog.turn_query = catalog.turn_interpretation = None
    catalog.semantic_handled = False
    value = normalize(text)
    body = choice_body(text, refs.selection is not None)
    # Wants/preferences without a target attribute are discovery, not consent.
    if re.match(r"^(?:bghit|بغيت)\s", value) and not attributes(text) and not affirmative_selection(text):
        body = None
    changes = attribute_change(text)
    attribute_input = attribute_adapter.inspect(text, state.active_attribute)
    hard_marker = r"\b(?:only|must|uniquement|obligatoire|ghir|darori)\b"
    if re.search(hard_marker, value):
        simple = re.sub(hard_marker, "", value).strip()
        proposed = attribute_adapter.inspect(simple, state.active_attribute)
        if proposed.requested:
            attribute_input = proposed
            turn.new_hard = proposed.requested
            if proposed.followup:
                changes = proposed.requested
    if state.unresolved_attribute_value and attribute_input.followup and not attribute_input.explicit:
        unresolved = attribute_adapter.short_body(text)
        attribute_input = AttributeInput(unresolved_value=unresolved, followup=True) if len(unresolved) <= 64 else AttributeInput(followup=True, ambiguous=True)
    if PRICE.fullmatch(value) or STOCK.fullmatch(value) or explanation_topic(text) is not None:
        attribute_input = AttributeInput()
    hint = reference_hint(text)
    # A lexical name query must not consume an incompletely parsed attribute
    # phrase. Let the structured planner interpret it instead of searching for
    # an attribute value as though it were a product name.
    if body:
        lexical = query_body(body)
        if lexical and lexical.terms and (all(term in attribute_adapter.colors for term in lexical.terms)
                                         or any(term.isdecimal() for term in lexical.terms)):
            body = None
    pending = state.pending
    reply = None
    try:
        catalog.deadline = monotonic() + 45
        explicit_budget = budget_from_text(text)
        if explicit_budget is not None:
            state.constraints.max_price = str(explicit_budget)
        # A bare value only acquires a field through fresh, unambiguous evidence.
        if attribute_input.unresolved_value and refs.focus and refs.focus.variant_id:
            current = catalog.get(GetProducts(product_ids=[refs.focus.product_id], variant_ids=[refs.focus.variant_id]))
            turn.lookup = True
            if current.products:
                inferred = attribute_adapter.infer_value(attribute_input.unresolved_value,
                    attribute_adapter.verified(current.products[0].variants[0]))
                if inferred:
                    attribute_input.requested = inferred
                    attribute_input.unresolved_value = None
                    changes = inferred
        if attribute_input.requested:
            state.unresolved_attribute_value = None
            state.requested_attributes = dict(list((state.requested_attributes | attribute_input.requested).items())[-8:])
            state.changed_attributes = attribute_input.requested
            state.active_attribute = next(iter(attribute_input.requested)) if len(attribute_input.requested) == 1 else None
            if turn.new_hard:
                state.hard_attributes = state.hard_attributes | attribute_input.requested
        unsupported = set(attribute_input.requested) - set(attribute_adapter.supported)
        from app.ai.checkout import local as checkout_local
        checkout_reply = checkout_local(turn, text)
        if checkout_reply is not None:
            reply = checkout_reply
        elif state.purchase and confirmation_answer(text) is not None:
            reply = turn.confirm_purchase(text)
        elif re.search(r"€|\$|\b(?:eur|usd|euros?|dollars?)\b", value):
            turn.operation = "unsupported"
            reply = local_catalog("unsupported", style)
        elif unsupported or attribute_input.unresolved_value or attribute_input.ambiguous:
            turn.operation = "clarify"
            state.unresolved_attribute_value = attribute_input.unresolved_value
            turn.pending = Pending(operation="details", missing="attribute", attribute_name=state.active_attribute)
            reply = local_catalog("attribute_unverified" if unsupported else "attribute", style)
            reply.catalog_refs = refs.model_dump(mode="json")
        elif CANCEL.fullmatch(value):
            turn.operation = "cancel"
            state.purchase = None
            reply = SalesReply({"english": "Selection cleared. No order was created.", "french": "Choix annulé. Aucune commande créée.",
                "darija_latin": "T7yed l choix dyalk. Mazal ma drna commande.", "mixed": "T7yed l choix dyalk. Mazal ma drna commande.",
                "darija_arabic": "تحيد الاختيار ديالك. ما درنا حتى طلبية."}[style])
        elif explanation_topic(text) is not None:
            turn.operation, turn.lookup = "explain", bool(refs.focus)
            target = explanation_target(text)
            if target:
                turn.explicit_explanation = True
                query = query_body(target)
                reply = turn.named(query, False) if query else turn.clarify("explain")
            elif explanation_topic(text) == "out_of_stock":
                turn.lookup = False
                turn.outcome = "generic"
                state.explanation_context = "definition"
                reply = generic_definition(style, refs)
            elif explanation_topic(text) == "state" and state.explanation_context == "definition":
                turn.lookup = False
                turn.outcome = "generic"
                reply = generic_definition(style, refs)
            elif explanation_topic(text) == "state" and turn.relevant() and not state.target_ambiguous:
                reply = turn.detail([refs.focus] if refs.focus else [])
            else:
                turn.lookup = False
                reply = turn.clarify("explain")
        elif (pending and pending.remaining_turns and not PRICE.fullmatch(value) and not STOCK.fullmatch(value)
              and (changes or hint != "none" or (query_body(value) and query_body(value).terms))
              and not body and not re.search(r"[?؟]|\b(?:merci|pas|ne|not|si|if|search|cherche|chno|wach|prix|price)\b", value)):
            turn.purchase_requested = pending.purchase_requested
            turn.purchase_quantity = pending.quantity
            turn.operation = ("select" if pending_authorized else "search") if pending.operation in ("select", "change") else pending.operation
            choose = turn.operation == "select"
            matched = turn.pending_target(value, hint, pending.candidates) if pending.candidates and pending.operation in ("price", "stock", "details", "explain", "compare", "recommend") else []
            if matched:
                reply = turn.detail(matched, select=choose)
            elif changes and refs.focus and pending.operation not in ("price", "stock"):
                requested = attribute_adapter.from_query(state.constraints)
                state.constraints = attribute_adapter.apply(state.constraints, changes)
                reply = turn.detail([refs.focus], requested | changes, choose)
            elif hint != "none":
                pending_refs = CatalogRefs(presented=pending.candidates, focus=refs.focus) if pending.candidates else refs
                reply = turn.detail(resolve(hint, pending_refs), select=choose)
            else:
                query = query_body(value)
                inherited = attribute_adapter.from_query(query)
                if (pending.operation in ("search", "select", "change") or pending.missing in ("variant", "attribute", "size", "color")
                        or (pending.operation == "details" and not refs.focus and not pending.candidates)):
                    inherited = attribute_adapter.from_query(state.constraints) | inherited
                query = attribute_adapter.apply(query, inherited)
                fields = ("max_price", "category", "brand") if pending.operation in ("search", "select", "change") else ("max_price",)
                query = query.model_copy(update={k: getattr(state.constraints, k) for k in fields if getattr(query, k) is None})
                reply = turn.named(query, choose, pending.authorization_message_id)
            if turn.pending:
                turn.pending.authorization_message_id = pending.authorization_message_id
                turn.pending.purchase_requested = pending.purchase_requested
                turn.pending.quantity = pending.quantity
        elif body and not affirmative_selection(text):
            turn.operation = "change" if refs.selection else "select"
            query = query_body(body)
            if hint != "none" or body in ("celui-là", "celui-la", "hada", "hadak", "this", "that"):
                reply = turn.detail(resolve(hint if hint != "none" else "focus", refs), select=True)
            elif query and query.terms:
                reply = turn.named(query, True, semantic_fallback=True)
            elif query and attribute_adapter.from_query(query):
                if refs.selection and refs.focus and refs.selection.product_id != refs.focus.product_id:
                    state.constraints = attribute_adapter.apply(state.constraints, attributes(body))
                    reply = turn.clarify("change", authorization=catalog.target.inbound_id)
                else:
                    reply = turn.detail([refs.focus] if refs.focus else [], attributes(body), True)
            else:
                reply = turn.clarify("select", authorization=catalog.target.inbound_id)
        elif affirmative_selection(text) and hint in ("first", "second", "third"):
            turn.operation = "change" if refs.selection else "select"
            reply = turn.detail(resolve(hint, refs), select=True)
        elif hint == "other":
            reply = turn.detail(resolve(hint, refs))
        elif hint in ("first", "second", "third") and re.fullmatch(r"(?:l |le |the )?(?:premier|deuxième|deuxieme|second|third|first|troisième|troisieme|lowel|tani)[ ?!.]*", value):
            reply = turn.detail(resolve(hint, refs))
        elif changes:
            turn.operation, turn.lookup = "variant", bool(refs.focus)
            if state.pending and state.constraints.terms and not refs.focus:
                query = attribute_adapter.apply(state.constraints, changes)
                reply = turn.named(query, False)
            elif not refs.focus:
                state.constraints = attribute_adapter.apply(state.constraints, changes)
                reply = turn.clarify("details")
                reply.catalog_refs = refs.model_dump(mode="json")
            else:
                reply = turn.detail([refs.focus], changes)
        elif (discovery := discovery_query(text)) is not None:
            turn.operation = "search"
            reply = turn.named(discovery, False)
            if reply.catalog_refs and reply.catalog_refs.get("focus") and reply.catalog_refs["focus"].get("variant_id") is None:
                turn.pending = Pending(operation="search", missing="variant")
                reply.text += "\n" + words(style)["variant"]
        elif PRICE.fullmatch(value) or STOCK.fullmatch(value):
            turn.operation = "price" if PRICE.fullmatch(value) else "stock"
            reply = turn.detail([refs.focus] if refs.focus and not state.target_ambiguous else [])
        elif re.search(r"\b(?:arkhess|ahsan)\b", value) or (state.discussed and re.search(r"\bkatnsa7ni\b", value)):
            turn.operation = "compare" if "arkhess" in value else "recommend"
            reply = turn.comparison()
        elif re.fullmatch(r"3ndi\s+\d+\s*(?:dh|mad)", value):
            turn.operation = "recommend"
            query = SearchProducts(max_price=str(budget_from_text(text)))
            reply = turn.named(query, False)
        if reply is None:
            turn.path = "semantic" if turn.operation == "details" else "deterministic"
            reply = semantic(service, text, history, style, admission, catalog)
            if not catalog.semantic_handled:
                action = getattr(catalog, "turn_action", None)
                interpretation = getattr(catalog, "turn_interpretation", None)
                if interpretation and interpretation.operation in ("search", "details", "variant", "price", "stock", "compare", "recommend", "explain", "clarify", "unsupported"):
                    turn.operation = interpretation.operation
                turn.operation = {"select": "select", "compare": "compare", "explain": "explain", "clarify": "clarify", "unsupported": "unsupported"}.get(action, turn.operation)
                if action == "clarify" or (body and reply.text in (words(style)["product"], words(style)["variant"])):
                    turn.pending = Pending(operation="select" if body else "details", missing="target",
                                           authorization_message_id=catalog.target.inbound_id if body else None)
                if getattr(catalog, "turn_query", None):
                    state.constraints = catalog.turn_query
                    state.requested_attributes = attribute_adapter.from_query(catalog.turn_query)
                    state.active_attribute = next(iter(state.requested_attributes)) if len(state.requested_attributes) == 1 else None
                    if action == "show":
                        turn.operation = "search"
                if turn.outcome == "verified":
                    state.attempted = None
                    state.target_ambiguous = False
                if action == "no_matches" and turn.outcome != "verified":
                    turn.outcome = "miss"
                if interpretation and interpretation.requested_attributes:
                    state.requested_attributes = dict(list((state.requested_attributes | interpretation.requested_attributes).items())[-8:])
                    if len(interpretation.requested_attributes) == 1:
                        state.active_attribute = next(iter(interpretation.requested_attributes))
                if reply.catalog_refs and reply.catalog_refs.get("selection"):
                    turn.selected = bool(body or affirmative_selection(text))
                    if reply.catalog_refs["selection"]["variant_id"] is None:
                        turn.pending = Pending(operation="select", missing="variant", authorization_message_id=catalog.target.inbound_id)
                if interpretation and set(interpretation.requested_attributes) - set(attribute_adapter.supported):
                    turn.operation = "clarify"
                    turn.pending = Pending(operation="details", missing="attribute", attribute_name=state.active_attribute)
        if pending and turn.pending and pending.operation == turn.pending.operation and pending.authorization_message_id == turn.pending.authorization_message_id:
            turn.pending.remaining_turns = pending.remaining_turns - 1
            if not turn.pending.remaining_turns:
                turn.pending = None
            elif pending.created_at:
                turn.pending.created_at = pending.created_at
        if (turn.selected or turn.purchase_requested) and not turn.confirming:
            reply = turn.offer_from_reply(reply)
        reply = turn.finish(reply)
        outcome = next((kind for kind in ("unavailable", "no_matches", "variant_not_found", "product_unavailable")
                        if reply.text == words(style)[kind]), "clarification" if turn.pending else "replied")
        recorded = admission.safe_record(operation=turn.operation, resolution_path=turn.path,
            reference_source="structured" if source else "none", focus_resolved=bool(reply.catalog_refs["focus"]),
            selection_intent=bool(body or affirmative_selection(text)),
            selection_changed=reply.catalog_refs["selection"] != refs.model_dump(mode="json")["selection"],
            clarification_state=turn.pending.missing if turn.pending else None,
            catalog_lookup_required=turn.lookup or turn.path == "semantic", outcome=outcome)
        if not recorded:
            reply = local_catalog("unavailable", style)
            reply.catalog_refs = refs.model_dump(mode="json")
            reply.commerce_state = original_state.model_dump(mode="json")
        else:
            from app.services.checkout_service import finalize
            reply = finalize(turn, reply)
        return reply
    except Exception as exc:
        admission.safe_record(fallback=True, failure_category="conversation_unavailable")
        reply = local_catalog("unavailable", style)
        if turn.checkout_dirty:
            import logging
            from app.ai.checkout import phrase
            order_attempted = bool(getattr(turn, "checkout_order_attempted", False))
            completed_context = bool(turn.state.cart and turn.state.cart.status == "completed")
            stage = "order_commit" if order_attempted else "completed_order" if completed_context else "prepare_cart"
            sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
            logging.getLogger(__name__).warning("checkout.failed stage=%s failure_type=%s sqlstate=%s",
                stage, type(exc).__name__, sqlstate or "none",
                extra={"failure_type": type(exc).__name__, "checkout_stage": stage, "sqlstate": sqlstate})
            from app.services.checkout_service import recover_completed
            try:
                recovered = recover_completed(turn)
                if recovered is not None:
                    return recovered
            except Exception:
                logging.getLogger(__name__).warning("checkout.recovery_unavailable")
            if order_attempted or completed_context:
                # A failed commit acknowledgement can leave the outcome uncertain.
                reply.text = phrase(style, "Je ne peux pas vérifier si votre commande a été enregistrée. Réessayez dans un moment.",
                    "I can't verify whether your order was recorded. Please try again shortly.",
                    "Ma9dertch nverifi wach commande tsjlat. 3awed jreb men b3d chwiya.", "ما قدرتش نأكد واش الطلبية تسجلات. عاود جرب من بعد شوية.")
            else:
                reply.text = phrase(style, "Je ne peux pas préparer votre commande pour le moment. Réessayez dans un moment.",
                    "I can't prepare your order right now. Please try again shortly.",
                    "Ma9dertch nwejjed commande daba. 3awed jreb men b3d chwiya.", "ما قدرتش نوجد الطلبية دابا. عاود جرب من بعد شوية.")
        reply.catalog_refs = refs.model_dump(mode="json")
        reply.commerce_state = original_state.model_dump(mode="json")
        return reply
