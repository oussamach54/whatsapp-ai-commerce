"""Bounded language shortcuts and verified order prompts for the existing Turn."""
import re
import unicodedata

from app.ai.cancellation_state import PendingCancellation
from app.ai.schemas import SalesReply
from app.ai.scope import normalize
from app.ai.checkout import phrase
from app.services.checkout_service import answer
from app.services import order_cancellation_service as service
from app.services.confirmation_context import active_prompt

ORDER_NUMBER = re.compile(r"\bORD-\d{4}-\d{6,}\b", re.I)
INDEX = re.compile(r"^(?:(?:la|le|numero|number|option|choix|the)\s+)?(\d+)[.!]?$", re.I)
UUID_TEXT = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.I)
ORDINALS = {"first": 0, "first one": 0, "premier": 0, "le premier": 0,
            "second": 1, "second one": 1, "deuxieme": 1, "la deuxieme": 1,
            "third": 2, "third one": 2, "troisieme": 2, "la troisieme": 2,
            "fourth one": 3}
CHOICE_QUESTIONS = ("Quelle commande souhaitez-vous annuler ?", "Which order would you like to cancel?",
                    "Achmen commande bghiti nlghiw?", "آشمن طلبية بغيتي نلغيو؟")


def plain(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', normalize(text)) if not unicodedata.combining(c))


def selection_text(text):
    value = plain(text).strip()
    return bool(INDEX.fullmatch(value) or value in ORDINALS or ORDER_NUMBER.search(value)
                or UUID_TEXT.fullmatch(value) or re.match(r"^\d+\.\s", value))


def selection_priority(catalog, text):
    """Route only selection-shaped text backed by a trusted choosing checkpoint.

    Delivery/expiry is checked again before resolution; an undelivered list can
    receive an expiry answer but never confer selection authority.
    """
    if not selection_text(text):
        return False
    with catalog.sessions() as db:
        customer = service.customer_for(db, catalog)
        pending, _ = service.latest_cancellation(db, catalog)
        return bool(pending and pending.status == "choosing" and pending.customer_id == customer.id
                    and pending.conversation_id == catalog.target.conversation_id)


def selected_index(text, prompt):
    value = plain(text).strip()
    match = INDEX.fullmatch(value)
    if match:
        # Bound conversion of untrusted numeric text independently of input limits.
        return int(match[1]) - 1 if len(match[1]) <= 2 else -1
    if value in ORDINALS:
        return ORDINALS[value]
    # Full copies must equal an actual delivered option, including legacy rows.
    starts = list(re.finditer(r"(?m)^(\d+)\.\s", prompt.content or ""))
    for i, start in enumerate(starts):
        block = prompt.content[start.start():starts[i + 1].start() if i + 1 < len(starts) else len(prompt.content)].strip()
        for question in CHOICE_QUESTIONS:
            if block.endswith(question):
                block = block[:-len(question)].strip()
        if normalize(block) == normalize(text):
            return int(start[1]) - 1
    return None


def cancellation_request(text):
    """A shortcut recognizes complete request grammar, not loose cancel keywords."""
    value = plain(text)
    value = ORDER_NUMBER.sub('', value).strip(" #.!?")
    verb = r"(?:annul(?:e|er|ez|ons)|cancel|nlghi|n?lgh[iy])"
    order = r"(?:ma |mon |my |the |la |une |l[' ]*|had |had l[' ]*)?(?:commandes?|orders?)(?: dyali| dyalk)?"
    prefix = r"(?:(?:non|no|please|svp|safi)[, ]+)?"
    return bool(re.fullmatch(prefix + r"(?:(?:je (?:veux|souhaite|voudrais) |i (?:want|would like) to |bghit |baghi )?"
        + verb + r"\s+" + order + r"|ma b9itch (?:baghi|bghit)\s+" + order + r")", value)
        or re.fullmatch(r"(?:بغيت )?(?:نلغي|الغاء|ألغي) (?:الطلبية|طلبي|الطلب)", value))


def response(turn, result, number="", summary=None):
    turn.operation = "order_cancel"
    turn.pending = turn.state.pending
    messages = {
        "none": ("Je ne trouve aucune commande récente à annuler pour vous.", "I couldn't find a recent order to cancel for you.",
                 "Ma l9itch commande récente dyalk bach nlghiha.", "ما لقيتش طلبية حديثة ديالك باش نلغيها."),
        "human": (f"Commande {number} : l'annulation automatique n'est plus disponible. Contactez l'équipe.",
                  f"Order {number} cannot be cancelled automatically. Please contact the team.",
                  f"Commande {number} ma t9derch tlgha automatiquement daba. Chof m3a l'équipe.", "الطلبية ما تقدرش تلغى تلقائيا دابا. تواصل مع الفريق."),
        "already": (f"Commande {number} déjà annulée.", f"Order {number} is already cancelled.",
                    f"Commande {number} déjà tlghat.", "الطلبية ديالك راه تلغات من قبل."),
        "success": (f"Commande {number} annulée avec succès.", f"Order {number} cancelled successfully.",
                    f"Commande {number} tlghat b najah.", "الطلبية ديالك تلغات بنجاح."),
        "declined": ("D'accord, votre commande reste inchangée.", "Okay, your order remains unchanged.",
                     "Wakha, commande dyalk b9at kif ma hiya.", "واخا، الطلبية ديالك بقات كيف ما هي."),
        "expired": ("L'annulation n'est pas confirmée. Demandez à nouveau d'annuler la commande.",
                    "Cancellation was not confirmed. Please request order cancellation again.",
                    "Ma t2ekkdatch l'annulation. 3awed tlob annulation dyal commande.", "الإلغاء ما تأكدش. عاود طلب إلغاء الطلبية."),
        "choose": CHOICE_QUESTIONS,
        "offer": ("Confirmez-vous l'annulation de cette commande ?", "Shall I cancel this order?",
                  "Bghiti n'annuliw had commande?", "بغيتي نلغيو هاد الطلبية؟"),
    }
    text = phrase(turn.style, *messages[result])
    if summary:
        text = text + "\n\n" + summary if result == "choose" else summary + "\n" + text
    marker = turn.state.cancellation.marker() if result == "offer" else None
    return SalesReply(text, catalog_refs=turn.refs.model_dump(mode="json"), confirmation_prompt=marker)


def snapshot_label(value, limit):
    value = " ".join(''.join(c for c in (value or '') if not unicodedata.category(c).startswith('C')).split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def item_line(item):
    name = snapshot_label(item.product_name_snapshot, 90)
    variant = snapshot_label(item.variant_name_snapshot, 55)
    return f"{item.quantity}× {name}" + (f" — {variant}" if variant else "")


def total_line(order, style):
    label = phrase(style, "Total", "Total", "Total", "المجموع") if order.total is not None else phrase(
        style, "Total produits", "Products total", "Total produits", "مجموع المنتجات")
    return f"{label}: {order.total if order.total is not None else order.subtotal:.2f} {order.currency}"


def snapshot_lines(order, style, limit):
    items = sorted(order.items, key=lambda item: str(item.id))
    lines = [item_line(item) for item in items[:limit]]
    if len(items) > limit:
        remaining = len(items) - limit
        lines.append(phrase(style, f"+ {remaining} autres articles.", f"+ {remaining} more items.",
                            f"+ {remaining} produits khrin.", f"+ {remaining} منتجات أخرى."))
    return lines


def choice_summary(orders, style):
    label = phrase(style, "Commande", "Order", "Commande", "الطلبية")
    return "\n\n".join("\n".join([f"{i}. {label} {order.order_number}",
        *[f"   - {line}" for line in snapshot_lines(order, style, 3)],
        f"   {total_line(order, style)} | {order.created_at.strftime('%d/%m/%Y')}"])
        for i, order in enumerate(orders, 1))


def order_summary(order, style):
    label = phrase(style, "Commande", "Order", "Commande", "الطلبية")
    lines = [f"{label} {order.order_number}:"]
    lines.extend(snapshot_lines(order, style, 6))
    lines.append(total_line(order, style))
    return "\n".join(lines)


def request(turn, text, order_ids=None):
    with turn.catalog.sessions() as db:
        customer = service.customer_for(db, turn.catalog)
        numbers = ORDER_NUMBER.findall(text)
        if len(numbers) > 1:
            return response(turn, "choose")
        number = numbers[0].upper() if numbers else None
        eligible = service.owned_orders(db, turn.catalog, customer.id, order_ids, number, eligible_only=True)
        if not eligible:
            orders = service.owned_orders(db, turn.catalog, customer.id, order_ids, number)
            turn.state.cancellation = None
            turn.cancellation_dirty = True
            return response(turn, service.disposition(orders[0], turn.catalog.settings) if orders else "none",
                            orders[0].order_number if orders else "")
        chosen = eligible[:3]
        pending = PendingCancellation(customer_id=customer.id, conversation_id=turn.catalog.target.conversation_id,
            order_ids=[order.id for order in chosen], request_message_id=turn.catalog.target.inbound_id,
            offered_at=turn.catalog.turn_time, status="awaiting_confirmation" if len(eligible) == 1 else "choosing")
        turn.state.cancellation = pending
        turn.cancellation_dirty = True
        if pending.status == "choosing":
            return response(turn, "choose", summary=choice_summary(chosen, turn.style))
        summary = order_summary(chosen[0], turn.style)
        if len(summary) > 3400:
            pending.status = "superseded"
            return response(turn, "human", chosen[0].order_number)
        return response(turn, "offer", summary=summary)


def local(turn, text):
    pending = turn.state.cancellation
    if pending and pending.status == "choosing" and selection_text(text):
        with turn.catalog.sessions() as db:
            prompt = service.selection_prompt(db, turn.catalog, pending)
            if not prompt:
                pending.status = "superseded"
                turn.cancellation_dirty = True
                return response(turn, "expired")
            customer = service.customer_for(db, turn.catalog)
            orders = service.owned_orders(db, turn.catalog, customer.id, pending.order_ids)
            by_id = {order.id: order for order in orders}
            index = selected_index(text, prompt)
            selected = by_id.get(pending.order_ids[index]) if index is not None and 0 <= index < len(pending.order_ids) else None
            if index is None and ORDER_NUMBER.fullmatch(text.strip()):
                selected = next((order for order in orders if order.order_number == text.strip().upper()), None)
            if selected:
                # Trusted candidate identity only; copied amounts and prose are not facts.
                return request(turn, "", [selected.id])
            remaining = [by_id[oid] for oid in pending.order_ids if oid in by_id
                         and service.disposition(by_id[oid], turn.catalog.settings) == "eligible"]
            if not remaining:
                pending.status = "superseded"
                turn.cancellation_dirty = True
                return response(turn, "none")
            # Invalid input re-presents only the same owned candidate set, in its
            # original order, with a fresh delivered context needed for a retry.
            turn.state.cancellation = PendingCancellation(customer_id=customer.id,
                conversation_id=turn.catalog.target.conversation_id, order_ids=[order.id for order in remaining],
                request_message_id=turn.catalog.target.inbound_id, offered_at=turn.catalog.turn_time, status="choosing")
            turn.cancellation_dirty = True
            return response(turn, "choose", summary=choice_summary(remaining, turn.style))
    if cancellation_request(text):
        return request(turn, text)
    pending = turn.state.cancellation
    if not pending:
        return None
    if answer(text) is None:
        return None
    with turn.catalog.sessions() as db:
        prompt = active_prompt(db, turn.catalog)
        # A genuinely new checkout offer supersedes an older cancellation context.
        if prompt and prompt.metadata_.get("confirmation_prompt", {}).get("action") == "checkout":
            return None
        if pending.status == "cancelled":
            orders = service.owned_orders(db, turn.catalog, pending.customer_id, pending.order_ids)
            return response(turn, "already", orders[0].order_number if orders else "")
        if pending.status not in ("choosing", "awaiting_confirmation"):
            return None
        turn.cancellation_dirty = True
        if answer(text) is False:
            pending.status = "declined"
            return response(turn, "declined")
        if not service.authorized(db, turn.catalog, pending):
            pending.status = "superseded"
            return response(turn, "expired")
        orders = service.owned_orders(db, turn.catalog, pending.customer_id, pending.order_ids)
        turn.cancellation_order_number = orders[0].order_number if orders else ""
    turn.cancellation_confirming = True
    return response(turn, "expired")  # Only the locked commit can render success.
