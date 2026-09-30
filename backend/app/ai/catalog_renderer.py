"""Render facts from DTOs only. Model prose and descriptions are never rendered."""
from decimal import Decimal
from app.ai.catalog_schemas import CatalogRefs, ProductRef, Selection
from app.ai.schemas import SalesReply
from app.services.catalog_service import CatalogError
from app.ai.attribute_adapter import CATALOG_ATTRIBUTES as attribute_adapter

TEXT = {
    "english": {"show": "Here are the verified options:", "compare": "Here is the comparison:",
        "select": "Your selection (not an order):", "no_matches": "I couldn't find a matching option in the catalog.",
        "budget": "What is your budget?", "preference": "What kind of product would you prefer?",
        "product": "Which product do you mean?", "variant": "Which variant would you like?",
        "unsupported": "I can't verify that information from the catalog.", "unavailable": "I can't check the catalog right now. Please try again.",
        "in_stock": "in stock", "out_of_stock": "out of stock", "unknown": "not specified", "size": "size", "color": "color",
        "no_variant": "No active variant found.", "more": "Other variants exist; which one do you mean?"},
    "french": {"show": "Voici les options vérifiées :", "compare": "Voici la comparaison :",
        "select": "Votre sélection (ce n'est pas une commande) :", "no_matches": "Je n'ai pas trouvé d'option correspondante dans le catalogue.",
        "budget": "Quel est votre budget ?", "preference": "Quel type de produit préférez-vous ?",
        "product": "De quel produit parlez-vous ?", "variant": "Quelle variante souhaitez-vous ?",
        "unsupported": "Je ne peux pas vérifier cette information dans le catalogue.", "unavailable": "Je ne peux pas consulter le catalogue maintenant. Réessayez plus tard.",
        "in_stock": "en stock", "out_of_stock": "épuisé", "unknown": "non précisée", "size": "taille", "color": "couleur",
        "no_variant": "Aucune variante active trouvée.", "more": "D'autres variantes existent ; laquelle souhaitez-vous ?"},
    "darija_latin": {"show": "Hado les choix li kaynin f catalogue:", "compare": "Hadi comparaison binathom:",
        "select": "Hada l choix dyalk (mazal machi commande):", "no_matches": "Ma l9itch chi produit kaynaseb had talab f catalogue.",
        "budget": "Ch7al l budget dyalk?", "preference": "Achmen no3 dyal produit bghiti?",
        "product": "Achmen produit kat9sed?", "variant": "Achmen variante bghiti?",
        "unsupported": "Ma n9derch n2ekked had l ma3louma men catalogue.", "unavailable": "Ma 9dertch nchouf catalogue daba. 3awed men be3d.",
        "in_stock": "kayn f stock", "out_of_stock": "salat l quantite", "unknown": "ma m7eddadach", "size": "taille", "color": "couleur",
        "no_variant": "Ma l9itch variante active.", "more": "Kaynin variantes okhrin; achmen wa7da kat9sed?"},
    "darija_arabic": {"show": "هادو الاختيارات اللي كاينين فالكتالوگ:", "compare": "ها المقارنة بيناتهم:",
        "select": "ها الاختيار ديالك (مازال ماشي طلبية):", "no_matches": "ما لقيتش منتوج مناسب لهاد الطلب فالكتالوگ.",
        "budget": "شحال الميزانية ديالك؟", "preference": "شنو نوع المنتوج اللي بغيتي؟",
        "product": "آشمن منتوج كتقصد؟", "variant": "آشمن نسخة بغيتي؟",
        "unsupported": "ما نقدرش نأكد هاد المعلومة من الكتالوگ.", "unavailable": "ما قدرتش نشوف الكتالوگ دابا. عاود من بعد.",
        "in_stock": "كاين فالمخزون", "out_of_stock": "سالا المخزون", "unknown": "ما محددش", "size": "المقاس", "color": "اللون",
        "no_variant": "ما لقيتش نسخة مفعلة.", "more": "كاينين نسخ أخرى؛ آشمن وحدة كتقصد؟"},
}

# Existence failures are separate from discovery's generic no-matches response.
for _style, _product, _variant in (
    ("english", "This product is unavailable.", "I couldn't find that variant for this product."),
    ("french", "Ce produit n'est pas disponible.", "Je n'ai pas trouvé cette variante pour ce produit."),
    ("darija_latin", "Had produit ma b9ach disponible.", "Ma l9itch had variante f had produit."),
    ("darija_arabic", "هاد المنتوج ما بقاش متوفر.", "ما لقيتش هاد النسخة فهاد المنتوج."),
):
    TEXT[_style].update(product_unavailable=_product, variant_not_found=_variant)

# Default questions describe variants, not an assumed set of product attributes.
for _style, _question, _more, _missing, _attribute, _unverified in (
    ("english", "Which variant would you like?", "Other variants exist; which one do you mean?",
     "I couldn't find that variant for this product.", "Which product attribute does that value refer to?",
     "I can't verify that attribute from the catalog. Which listed variant do you mean?"),
    ("french", "Quelle variante souhaitez-vous ?", "D'autres variantes existent ; laquelle souhaitez-vous ?",
     "Je n'ai pas trouvé cette variante pour ce produit.", "À quelle caractéristique du produit correspond cette valeur ?",
     "Je ne peux pas vérifier cette caractéristique dans le catalogue. Quelle variante présentée souhaitez-vous ?"),
    ("darija_latin", "Achmen variante bghiti?", "Kaynin variantes okhrin; achmen wa7da kat9sed?",
     "Ma l9itch had variante f had produit.", "Had l9ima dyal achmen caractéristique f produit?",
     "Ma n9derch n2ekked had caractéristique men catalogue. Achmen variante men li writek kat9sed?"),
    ("darija_arabic", "آشمن نسخة بغيتي؟", "كاينين نسخ أخرى؛ آشمن وحدة كتقصد؟",
     "ما لقيتش هاد النسخة فهاد المنتوج.", "هاد القيمة ديال آشمن خاصية فالمنتوج؟",
     "ما نقدرش نأكد هاد الخاصية من الكتالوگ. آشمن نسخة من اللي وريتك كتقصد؟"),
):
    TEXT[_style]["attribute_variant_not_found"] = _missing
    TEXT[_style].update(variant=_question, more=_more, variant_not_found=_missing,
                        attribute=_attribute, attribute_unverified=_unverified)


def words(style):
    return TEXT["darija_latin" if style == "mixed" else style]


def local_catalog(kind, style):
    # Empty candidate references; the turn owns commit/rollback of trusted state.
    return SalesReply(words(style)[kind], catalog_refs=CatalogRefs().model_dump(mode="json"))


def generic_definition(style, refs):
    meanings = {
        "english": "‘Out of stock’ means no units are available.",
        "french": "« Rupture de stock » signifie qu’il n’y a aucune pièce disponible.",
        "darija_latin": "‘Salat l quantité’ kat3ni ma kaynach quantité disponible.",
        "darija_arabic": "«سالات الكمية» كتعني ما كايناش قطع متوفرة.",
    }
    return SalesReply(meanings["darija_latin" if style == "mixed" else style],
                      catalog_refs=refs.model_dump(mode="json"))


def target_question(style):
    return {"english": "Which variant do you mean?", "french": "Quelle variante voulez-vous dire ?",
            "darija_latin": "Achmen variante kat9sed?", "darija_arabic": "آشمن نسخة كتقصد؟"}[
                "darija_latin" if style == "mixed" else style]


def attribute_difference(variant, requested, style):
    differences = attribute_adapter.differences(variant, requested)
    if not differences:
        return ""
    lang = "darija_latin" if style == "mixed" else style
    templates = {"english": "{attribute} {after} instead of {before}", "french": "{attribute} {after} au lieu de {before}",
                 "darija_latin": "{attribute} {after}, machi {before}", "darija_arabic": "{attribute} {after}، ماشي {before}"}
    return " (" + "; ".join(templates[lang].format(attribute=words(style).get(key, ""), before=before,
        after=after or words(style)["unknown"]) for key, (before, after) in differences.items()) + ")"


def render_alternatives(product, variants, requested, refs, style, currency, truncated=False):
    labels = {"english": "I couldn't find that option", "french": "Je n’ai pas trouvé cette option",
              "darija_latin": "Ma l9itch had l’option", "darija_arabic": "ما لقيتش هاد الاختيار"}
    lang = "darija_latin" if style == "mixed" else style
    lines = [labels[lang] + " (" + ", ".join(f"{words(style).get(k, '')} {v}".strip() for k, v in requested.items()) + ")."]
    for index, variant in enumerate(variants, 1):
        lines.append(f"{index}. {product.name} — {variant.name}: {variant.price} {currency}; "
                     f"{words(style)[variant.availability]}" + attribute_difference(variant, requested, style) + ".")
    if truncated:
        lines.append(words(style)["more"])
    body = "\n".join(lines)
    if len(body) > 4096:
        raise CatalogError("reply_too_large")
    return SalesReply(body, catalog_refs=refs.model_dump(mode="json"))


def purchase_reply(product, variant, style, quantity, status):
    lang = "darija_latin" if style == "mixed" else style
    summary = f"{product.name} — {variant.name}: {variant.price} MAD; {words(style)[variant.availability]}."
    if quantity > 1:
        summary += f" {quantity} × {variant.price} MAD = {Decimal(variant.price) * quantity:.2f} MAD."
    messages = {
        "english": {"offer": "Would you like to confirm this choice?", "confirmed": "Your choice is confirmed. No order has been created.",
                    "price_changed": "The price has changed. Would you like to confirm at this price?", "unavailable": f"Sorry, only {variant.stock_quantity} units are available. Would you like another option?"},
        "french": {"offer": "Vous confirmez ce choix ?", "confirmed": "Votre choix est confirmé. Aucune commande n’a été créée.",
                   "price_changed": "Le prix a changé. Vous confirmez à ce prix ?", "unavailable": f"Désolé, il reste {variant.stock_quantity} pièce(s). Vous souhaitez une autre option ?"},
        "darija_latin": {"offer": "Nconfirmiw had choix?", "confirmed": "T2ekked l choix dyalk. Mazal ma tsjlat ta commande.",
                         "price_changed": "Taman tbeddel. Nconfirmiw had choix b had taman?", "unavailable": f"Sme7 lia, ba9i ghir {variant.stock_quantity} pièce. Bghiti nchoufo choix akhor?"},
        "darija_arabic": {"offer": "نأكدو هاد الاختيار؟", "confirmed": "تأكد الاختيار ديالك. ما تسجلات حتى طلبية.",
                          "price_changed": "الثمن تبدل. نأكدو بهاد الثمن؟", "unavailable": f"سمح ليا، باقي غير {variant.stock_quantity} قطعة. نشوفو اختيار آخر؟"},
    }
    return summary + "\n" + messages[lang][status]


def price_difference(first, second, style):
    if Decimal(first.price) == Decimal(second.price):
        return ""
    cheap, dear = sorted((first, second), key=lambda v: Decimal(v.price))
    difference = Decimal(dear.price) - Decimal(cheap.price)
    template = {"english": "{cheap} costs {difference:.2f} MAD less than {dear}.",
                "french": "{cheap} coûte {difference:.2f} MAD de moins que {dear}.",
                "darija_latin": "{cheap} arkhess men {dear} b {difference:.2f} MAD.",
                "darija_arabic": "{cheap} أرخص من {dear} بـ {difference:.2f} MAD."}[
                    "darija_latin" if style == "mixed" else style]
    return template.format(cheap=cheap.name, dear=dear.name, difference=difference)


def variant_question(product, style):
    w = words(style)
    keys = [key for key in attribute_adapter.supported if any(key in attribute_adapter.verified(v) for v in product.variants)]
    if not keys:
        return w["variant"]
    lang = "darija_latin" if style == "mixed" else style
    separator, template = {
        "english": (" or ", "Which {attributes} would you like?"),
        "french": (" ou ", "Quelle {attributes} souhaitez-vous ?"),
        "darija_latin": (" wla ", "Achmen {attributes} bghiti?"),
        "darija_arabic": (" ولا ", "آشمن {attributes} بغيتي؟"),
    }[lang]
    return template.format(attributes=separator.join(w.get(key, key) for key in keys))


def render(plan, products, style, currency, source_message_id=None, max_price=None):
    w = words(style)
    if plan.action in ("clarify", "unsupported", "no_matches"):
        if plan.items:
            raise CatalogError("invalid_plan")
        return local_catalog((plan.question or "product") if plan.action == "clarify" else plan.action, style)
    if not plan.items or (plan.action == "compare" and len(plan.items) < 2) or (plan.action == "select" and len(plan.items) != 1):
        raise CatalogError("invalid_plan")
    if len({r.product_id for r in plan.items}) != len(plan.items):
        raise CatalogError("duplicate_plan_items")
    if plan.action == "select" and plan.items[0].variant_id:
        target = plan.items[0]
        product = products.get(target.product_id)
        if product and any(v.id == target.variant_id and v.stock_quantity <= 0 for v in product.variants):
            # Identity may remain focus; unavailable inventory cannot become a
            # newly verified variant choice. A product-level preference is distinct.
            plan = plan.model_copy(update={"action": "show"})
    lines, shown = [w[plan.action]], []
    unresolved = False
    for index, ref in enumerate(plan.items, 1):
        product = products.get(ref.product_id)
        if product is None:
            raise CatalogError("unverified_product")
        variants = [v for v in product.variants if ref.variant_id is None or v.id == ref.variant_id]
        if ref.variant_id and not variants:
            raise CatalogError("unverified_variant")
        lines.append(f"{index}. {product.name}")
        for variant in variants:
            if variant.product_id != product.id or (max_price is not None and Decimal(variant.price) > max_price):
                raise CatalogError("constraint_mismatch")
            values = attribute_adapter.verified(variant)
            attributes = "".join(f"{w.get(key, key)}: {value}; " for key, value in values.items())
            lines.append(f"  {variant.name} — {variant.price} {currency}; {attributes}{w[variant.availability]}")
        if not variants and not product.has_more_variants:
            lines.append(w["no_variant"])
        if product.has_more_variants:
            lines.append(w["more"])
        shown.append(ProductRef(product_id=product.id, variant_id=ref.variant_id))
        unresolved |= ref.variant_id is None
    selection = None
    if plan.action == "select":
        ref = shown[0]
        selection = Selection(**ref.model_dump(), source_message_id=source_message_id,
                              resolution="variant" if ref.variant_id else "product")
        if unresolved:
            lines.append(variant_question(products[ref.product_id], style))
    refs = CatalogRefs(presented=shown, focus=shown[0] if len(shown) == 1 else None, selection=selection)
    text = "\n".join(lines)
    if len(text) > 4096:
        raise CatalogError("reply_too_large")
    return SalesReply(text, catalog_refs=refs.model_dump(mode="json"))


def render_explanation(product, variant, refs, topic, style, currency):
    """Only freshly verified DTO fields enter factual conversational templates."""
    lang = "darija_latin" if style == "mixed" else style
    templates = {
        "english": ("{name} ({variant}) costs {price} {currency}. Right now, {stock}.",
                    "it is available", "there are no units available",
                    "‘Out of stock’ means no units are available. "),
        "french": ("{name} ({variant}) coûte {price} {currency}. Actuellement, {stock}.",
                   "il est disponible", "aucune pièce n’est disponible",
                   "« Rupture de stock » signifie qu’il n’y a aucune pièce disponible. "),
        "darija_latin": ("Ya3ni {name} ({variant}) b {price} {currency}. Daba {stock}.",
                         "kayn f stock", "ma b9at 7ta pièce disponible",
                         "‘Salat l quantité’ kat3ni ma kaynach quantité disponible. "),
        "darija_arabic": ("يعني {name} ({variant}) بـ {price} {currency}. دابا {stock}.",
                          "كاين فالمخزون", "ما بقات حتى قطعة متوفرة",
                          "«سالات الكمية» كتعني ما كايناش قطع متوفرة. "),
    }
    template, available, empty, meaning = templates[lang]
    body = template.format(name=product.name, variant=variant.name, price=variant.price,
        currency=currency, stock=available if variant.stock_quantity > 0 else empty)
    if topic == "out_of_stock":
        body = meaning + body
    if len(body) > 4096:
        raise CatalogError("reply_too_large")
    return SalesReply(body, catalog_refs=refs.model_dump(mode="json"))
