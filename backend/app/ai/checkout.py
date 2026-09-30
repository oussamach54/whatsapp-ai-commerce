"""Checkout operations for the existing Turn; no separate router or model loop."""
from decimal import Decimal
from app.ai.attribute_adapter import CATALOG_ATTRIBUTES as attributes
from app.ai.catalog_schemas import ProductRef, GetProducts, SearchProducts, RequestedItem
from app.ai.checkout_state import Cart, CartLine, CustomerValue
from app.ai.schemas import SalesReply
from app.ai.scope import normalize
from app.services.checkout_service import answer, CANCELLATIONS, clean_field, known_fields, missing_fields, totals


class ItemResolutionError(ValueError):
    pass


def phrase(style, french, english, darija, arabic):
    return {"french": french, "english": english, "darija_latin": darija, "mixed": darija, "darija_arabic": arabic}[style]


def render_cart(turn, result="offer", problem=None):
    cart, style = turn.state.cart, turn.style
    if result == "missing":
        labels = {
            "customer_name": ("nom", "name", "smiya", "السمية"),
            "phone": ("numéro de téléphone", "phone number", "numéro téléphone", "رقم الهاتف"),
            "city": ("ville", "city", "ville", "المدينة"),
            "address": ("adresse de livraison", "delivery address", "adresse de livraison", "عنوان التوصيل"),
            "delivery_note": ("instructions de livraison", "delivery instructions", "instructions livraison", "تعليمات التوصيل"),
            "postal_code": ("code postal", "postal code", "code postal", "الرمز البريدي"),
        }
        needed = ", ".join(phrase(style, *labels[field]) for field in missing_fields(turn.catalog.settings, cart))
        return phrase(style, f"Parfait 😊 Donnez-moi uniquement : {needed}, pour terminer votre commande.",
            f"Great 😊 Please send your {needed} to complete your order.",
            f"Parfait 😊 3tini ghir {needed} bach nkemlo commande.", f"مزيان 😊 عطيني غير {needed} باش نكملو الطلبية.")
    if result == "cancelled":
        return phrase(style, "Pas de souci, on laisse cette commande de côté 😊", "No problem, we'll leave this purchase for now 😊",
            "Pas de souci, nkhalliw had commande daba 😊", "ما كاين مشكل، نخليو هاد الطلبية دابا 😊")
    if result == "existing":
        return phrase(style, "Votre commande est déjà enregistrée. Pour la modifier ou l'annuler, il faut contacter l'équipe.",
            "Your order is already recorded. Please contact the team to change or cancel it.",
            "Commande dyalk déjà tsjlat. Bach tbeddelha wla tlghiha, khass tchof m3a l'équipe.",
            "الطلبية ديالك تسجلات. باش تبدلها ولا تلغيها، خاص تشوف مع الفريق.")
    if result == "ambiguous":
        return phrase(style, "Quel article souhaitez-vous modifier ?", "Which item would you like to change?",
            "Achmen produit bghiti tbeddel?", "آشمن منتوج بغيتي تبدل؟")
    if result == "stock":
        return phrase(style, f"Désolé, {problem.variant_name} : seulement {problem.stock} disponible(s). Quelle quantité souhaitez-vous ?",
            f"Sorry, only {problem.stock} of {problem.variant_name} are available. What quantity would you like?",
            f"Sme7 lia, {problem.variant_name} kayn menno ghir {problem.stock} daba. Ch7al bghiti?",
            f"سمح ليا، {problem.variant_name} كاين منو غير {problem.stock} دابا. شحال بغيتي؟")
    if result == "unavailable":
        return phrase(style, "Cet article n'est plus disponible. Voulez-vous voir un autre choix ?",
            "That item is no longer available. Would you like another option?",
            "Had produit ma b9ach disponible. Bghiti choix akhor?", "هاد المنتوج ما بقاش متوفر. بغيتي اختيار آخر؟")
    lines = [f"{line.quantity}× {line.product_name} — {line.variant_name}: {Decimal(line.unit_price) * line.quantity:.2f} MAD"
             for line in cart.items]
    lines.append(phrase(style, "Total produits", "Products total", "Total produits", "مجموع المنتوجات") + f": {cart.subtotal} MAD.")
    if cart.shipping_cost is not None:
        lines.append(phrase(style, "Livraison", "Delivery", "Livraison", "التوصيل") + f": {cart.shipping_cost} MAD. Total: {cart.total} MAD.")
    if result == "completed":
        lines.insert(0, phrase(style, "Votre commande est confirmée ✅", "Your order is confirmed ✅",
            "Voilà, commande dyalk t2ekkdat ✅", "الطلبية ديالك تأكدات ✅"))
        from app.ai.business_knowledge import delivery_summary
        delivery = delivery_summary(turn.catalog.settings, style)
        if delivery:
            lines.append(delivery)
        lines.append(phrase(style, "Merci pour votre commande 😊", "Thank you for your order 😊", "Merci pour ta commande, marhba bik 😊", "شكراً على الطلبية، مرحبا بيك 😊"))
    else:
        if result == "price":
            lines.insert(0, phrase(style, "Le prix a changé :", "The price has changed:", "Taman tbeddel:", "الثمن تبدل:"))
        lines.append(phrase(style, "Confirmez-vous la commande ?", "Shall I confirm your order?", "Nconfirmiw commande?", "نأكدو الطلبية؟"))
    return "\n".join(lines)


def reply(turn, result="offer", problem=None):
    refs = turn.refs.model_dump(mode="json")
    cart = turn.state.cart
    if cart and result in ("offer", "price", "completed") and cart.items:
        refs["presented"] = [line.target.model_dump(mode="json") for line in cart.items[:3]]
        refs["focus"] = refs["presented"][0] if len(cart.items) == 1 else None
    return SalesReply(render_cart(turn, result, problem), catalog_refs=refs)


def hydrate(turn, cart):
    with turn.catalog.sessions() as db:
        turn.catalog.authorize(db)
        known_fields(db, turn.catalog, cart)


def receive_fields(turn, values, text):
    valid = {}
    for field in values:
        # The model extracts literal customer data; it cannot infer contact data.
        if normalize(field.value) not in normalize(text):
            continue
        try:
            value = clean_field(field.name, field.value)
        except ValueError:
            continue
        valid[field.name] = CustomerValue(value=value, source="customer", message_id=turn.catalog.target.inbound_id)
    if values:
        turn.checkout_private = True
        turn.state.customer_intent = None
    turn.checkout_values = valid
    if turn.state.cart and turn.state.cart.status not in ("completed", "cancelled"):
        turn.state.cart.fields.update(valid)
        turn.checkout_dirty = True


def sync_purchase(turn):
    """Project legacy single-choice metadata; cart is the only checkout authority."""
    from app.ai.commerce_state import PurchaseIntent
    cart = turn.state.cart
    if not cart or len(cart.items) != 1 or cart.status in ("blocked", "cancelled"):
        turn.state.purchase = None
        return
    line = cart.items[0]
    turn.state.purchase = PurchaseIntent(target=line.target, quantity=line.quantity, quoted_unit_price=line.unit_price,
        request_message_id=turn.catalog.target.inbound_id, offered_at=cart.offered_at,
        status="confirmed" if cart.status in ("confirmed", "completed") else "awaiting_confirmation",
        confirmation_message_id=cart.confirmation_message_id)


def offer_single(turn, product, variant, quantity):
    old = turn.state.cart
    fields = dict(old.fields) if old else {}
    fields.update(getattr(turn, "checkout_values", {}))
    cart = Cart(offered_at=turn.catalog.turn_time, fields=fields,
        items=[CartLine(target=ProductRef(product_id=product.id, variant_id=variant.id), quantity=quantity,
                        unit_price=variant.price, stock=variant.stock_quantity, product_name=product.name, variant_name=variant.name)])
    turn.state.cart = cart
    hydrate(turn, cart)
    totals(cart, turn.catalog.settings)
    turn.checkout_dirty = True
    sync_purchase(turn)
    return render_cart(turn)


def confirm(turn, text):
    cart = turn.state.cart
    turn.confirming = True
    turn.checkout_dirty = True
    if cart.status == "completed":
        return reply(turn, "completed")
    if answer(text) is False or normalize(text).strip(" .!") in CANCELLATIONS:
        cart.status = "cancelled"
        turn.state.purchase = None
        return reply(turn, "cancelled")
    if cart.status in ("cancelled", "blocked"):
        return reply(turn, "unavailable")
    if cart.status != "confirmed" and (answer(text) is None
            or not 0 <= (turn.catalog.turn_time - cart.offered_at).total_seconds() <= 900):
        # Re-presenting a cart is a new verified offer, not consent. Its window
        # starts here, rather than at an older (possibly undelivered) proposal.
        for line in cart.items:
            current = turn.catalog.get(GetProducts(product_ids=[line.target.product_id], variant_ids=[line.target.variant_id]))
            if not current.products:
                cart.status = "blocked"
                return reply(turn, "unavailable")
            variant = current.products[0].variants[0]
            line.unit_price, line.stock = variant.price, variant.stock_quantity
            if line.stock < line.quantity:
                cart.status = "blocked"
                return reply(turn, "stock", line)
        cart.version += 1
        cart.offered_at = turn.catalog.turn_time
        cart.status = "awaiting_confirmation"
        cart.confirmed_version = cart.confirmation_message_id = None
        totals(cart, turn.catalog.settings)
        sync_purchase(turn)
        return reply(turn)
    if answer(text) is True:
        cart.status, cart.confirmed_version = "confirmed", cart.version
        cart.confirmation_message_id = turn.catalog.target.inbound_id
        return reply(turn, "missing")
    return reply(turn)


def local(turn, text):
    cart = turn.state.cart
    if not cart:
        return None
    value = normalize(text).strip(" .!")
    if value in CANCELLATIONS:
        return reply(turn, "existing") if cart.status == "completed" else confirm(turn, text)
    if answer(text) is not None:
        return confirm(turn, text)
    # A single missing free-text field can be collected without sending PII to AI.
    missing = missing_fields(turn.catalog.settings, cart)
    if cart.status == "confirmed" and len(missing) == 1:
        from app.ai.scope import local_route
        decision, _ = local_route(text)
        if decision.scope == "unresolved" and not any(mark in text for mark in ("?", "؟")):
            from app.ai.catalog_schemas import CheckoutValue
            receive_fields(turn, [CheckoutValue(name=missing[0], value=text)], text)
            return reply(turn, "missing")
    return None


def resolve_item(turn, request, context=None):
    from app.ai.catalog_orchestrator import resolve
    requested = attributes.normalize_values({a.name: a.value for a in request.attributes})
    if len(requested) != len(request.attributes) or set(requested) - set(attributes.supported):
        return None
    if request.terms:
        result = turn.catalog.search(SearchProducts(terms=request.terms, in_stock_only=False))
        if len(result.products) != 1 or result.has_more:
            return None
        targets = [ProductRef(product_id=result.products[0].id)]
    else:
        # A unique recommendation takes precedence over an older browsing focus.
        targets = ([context.target] if context and request.reference == "none" else
                   turn.refs.presented if len(turn.refs.presented) == 1 and request.reference in ("none", "focus") else
                   resolve(request.reference, turn.refs))
        if not targets and turn.refs.focus:
            targets = [turn.refs.focus]
        if not targets and turn.state.cart:
            for line in turn.state.cart.items:
                current = turn.catalog.get(GetProducts(product_ids=[line.target.product_id], variant_ids=[line.target.variant_id]))
                if current.products and attributes.matches(current.products[0].variants[0], requested):
                    targets.append(line.target)
    if len(targets) != 1:
        return None
    target = targets[0]
    if target.variant_id and requested:
        previous = turn.catalog.get(GetProducts(product_ids=[target.product_id], variant_ids=[target.variant_id]))
        if not previous.products:
            return None
        preserved, _ = attributes.transition(previous.products[0].variants[0], requested)
        requested = preserved | requested
    result = turn.catalog.get(GetProducts(product_ids=[target.product_id],
        variant_ids=[target.variant_id] if target.variant_id and not requested else [], **attributes.filters(requested)))
    if result.products and not result.products[0].variants and not result.products[0].has_more_variants:
        raise ItemResolutionError("variant_not_found")
    if not result.products:
        raise ItemResolutionError("product_unavailable")
    if result.products[0].has_more_variants or len(result.products[0].variants) != 1:
        return None
    product, variant = result.products[0], result.products[0].variants[0]
    return CartLine(target=ProductRef(product_id=product.id, variant_id=variant.id), quantity=request.quantity,
        unit_price=variant.price, stock=variant.stock_quantity, product_name=product.name, variant_name=variant.name)


def propose(turn, intent, text):
    """Resolve semantic items and edits into a bounded application-owned cart."""
    receive_fields(turn, intent.checkout, text)
    old = turn.state.cart
    if intent.intent == "checkout":
        if old and old.status == "confirmed":
            turn.checkout_dirty = True
            return reply(turn, "missing")
        return reply(turn) if old and old.items else turn.clarify("details")
    if intent.speech_act in ("negative", "quoted") or not intent.evidence or normalize(intent.evidence) != normalize(text):
        return turn.clarify("details")
    requests = intent.items or [RequestedItem(reference=intent.reference if intent.reference != "pair" else "none",
        terms=intent.terms, attributes=intent.attributes, quantity=intent.quantity or 1)]
    editing = intent.intent == "cart_edit"
    if editing and (not old or old.status in ("completed", "cancelled")):
        return reply(turn, "existing") if old else turn.clarify("details")
    if editing:
        old.status, old.confirmed_version, old.confirmation_message_id = "blocked", None, None
        turn.checkout_dirty = True
    cart = old.model_copy(deep=True) if editing else Cart(offered_at=turn.catalog.turn_time, fields=dict(old.fields) if old else {})
    cart.fields.update(getattr(turn, "checkout_values", {}))
    if editing:
        cart.version += 1
    if not editing or intent.cart_action == "replace":
        cart.items = []
    for request in requests:
        if editing and intent.cart_action in ("remove", "set_quantity", "increase", "decrease"):
            values = attributes.normalize_values({a.name: a.value for a in request.attributes})
            matches = []
            for line in cart.items:
                result = turn.catalog.get(GetProducts(product_ids=[line.target.product_id], variant_ids=[line.target.variant_id]))
                if not result.products:
                    continue
                p, v = result.products[0], result.products[0].variants[0]
                if attributes.matches(v, values) and all(normalize(term) in normalize(p.name) for term in request.terms):
                    matches.append(line)
            if len(matches) != 1:
                return reply(turn, "ambiguous")
            line = matches[0]
            if intent.cart_action == "remove":
                cart.items.remove(line)
                continue
            quantity = request.quantity if intent.cart_action == "set_quantity" else line.quantity + request.quantity * (1 if intent.cart_action == "increase" else -1)
            if not 1 <= quantity <= 99:
                return reply(turn, "ambiguous")
            line.quantity = quantity
        else:
            try:
                line = resolve_item(turn, request, cart.items[-1] if cart.items else None)
            except ItemResolutionError as exc:
                from app.ai.catalog_renderer import local_catalog
                return local_catalog(str(exc), turn.style)
            if line is None:
                return turn.clarify("details")
            same = next((existing for existing in cart.items if existing.target == line.target), None)
            if same:
                if same.quantity + line.quantity > 99:
                    return turn.clarify("details")
                same.quantity += line.quantity
            else:
                cart.items.append(line)
    if len(cart.items) > 6:
        return turn.clarify("details")
    cart.confirmed_version = cart.confirmation_message_id = None
    cart.status = "awaiting_confirmation" if cart.items else "cancelled"
    cart.offered_at = turn.catalog.turn_time
    # Re-read all lines, including unchanged lines after an edit.
    problem = None
    for line in cart.items:
        result = turn.catalog.get(GetProducts(product_ids=[line.target.product_id], variant_ids=[line.target.variant_id]))
        if not result.products:
            return reply(turn, "unavailable")
        variant = result.products[0].variants[0]
        line.unit_price, line.stock = variant.price, variant.stock_quantity
        if line.stock < line.quantity:
            problem = line
    turn.state.cart = cart
    hydrate(turn, cart)
    totals(cart, turn.catalog.settings)
    turn.checkout_dirty = True
    if problem:
        cart.status = "blocked"
    turn.operation = "details"
    turn.replace_focus = True
    turn.outcome = "verified"
    sync_purchase(turn)
    return reply(turn, "stock" if problem else "offer" if cart.items else "cancelled", problem)
