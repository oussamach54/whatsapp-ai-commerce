"""Conservative shortcuts. Unknown phrasing goes to semantic classification."""
import re
import unicodedata

from app.ai.schemas import ScopeDecision


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    return " ".join("".join(c for c in text if unicodedata.category(c) != "Cf").split())


def language_style(text, history=()):
    value = normalize(text)
    if re.search(r"[\u0600-\u06ff]", value):
        return "darija_arabic"
    if re.fullmatch(r"ma\s?fh[ae]mtch[.!?]*|safi fhemtk merci[.!?]*", value):
        return "darija_latin"
    if re.search(r"\b(?:ch7al|kayn|arkhess|ahsan)\b", value):
        return "mixed" if re.search(r"\b(?:je|merci|prix)\b", value) else "darija_latin"
    if re.search(r"\b(?:how much|available|in stock)\b", value):
        return "english"
    if re.search(r"\b(?:quel prix|combien|bonne journée)\b", value):
        return "french"
    darija = re.search(r"\b(salam|chokran|labas|bslama|bgh?et|bghit|bghet|chno|wach|3ndi|nakhod|nasi7a|katnsa7ni|achmen|dyal|3la|n3ti|chre7|3ellemni|chkon|3tini|kteb|wrini)\b", value)
    french = re.search(r"\b(je|bonjour|merci|cadeau|budget|livraison|commande|produit|pour|conseilles|explique|ecris|écris|moi|elia)\b", value)
    if darija:
        return "mixed" if french else "darija_latin"
    if french or re.search(r"\b(qui est|qu'est|au revoir|devoirs|francais|français)\b", value):
        return "french"
    if re.search(r"\b(hello|hi|thanks|thank|bye|the|what|which|can|my|i|explain|write|ignore|show|reveal|your)\b", value):
        return "english"
    for message in reversed(history):
        if message.role == "user":
            return language_style(message.content)
    return "french"


SOCIAL = re.compile(r"^(?:(?:salam|bonjour|bonsoir|salut|merci|chokran|choukran|labas|bslama|au revoir|hello|hi|hey|thanks|thank you|bye|goodbye|سلام|شكرا|لاباس|بسلامة)[\s,!?.،😊🙂🙏]*)+$")
INJECTION = re.compile(
    r"(?:ignore|forget|disregard|oublie|ignorez?|تجاهل).{0,35}(?:instructions|rules|regles|règles|تعليمات)|"
    r"(?:show|reveal|print|repeat|display|donne|montre|affiche|révèle|revele|wrini|3tini|اكشف|وريني).{0,45}(?:system|developer|prompt|تعليمات)|"
    r"(?:what are|tell me|quelles sont|chno huma).{0,25}(?:your instructions|tes instructions|system|developer)|"
    r"(?:act|behave|agis|comporte).{0,30}(?:chatgpt|general.purpose|assistant general)|"
    r"(?:you are|tu es).{0,25}(?:chatgpt|unrestricted)|"
    r"(?:<\|(?:system|developer)|\[(?:system|developer)\]|(?:^|\s)(?:system|developer)\s*:)", re.I)
UNRELATED = re.compile(
    r"(?:explique(?:\s+moi)?|explain|teach me|3ellemni|chre7 lia|شرح ليا).{0,20}\b(?:python|javascript|programming|coding|politics|history)\b|"
    r"(?:qui est|who is|chkon (?:howa|huwa)|شكون هو).{0,10}(?:cristiano|ronaldo)|"
    r"(?:fais|do|write|ecris|écris).{0,15}(?:mes devoirs|my homework|my cv|moi un cv)|"
    r"(?:general (?:knowledge|politics|news)|actualites politiques|actualité politique)|"
    r"(?:explain|tell me about|summarize|résume|explique).{0,25}(?:politics|world news|world history|roman history|histoire romaine)|"
    r"(?:write|debug|fix|écris|ecris).{0,25}(?:python code|javascript code|a python (?:script|function)|du code)", re.I)
# A phrase shortcut is never a denial rule; absence of a match is unresolved.
SHOPPING = re.compile(
    r"\b(?:cadeau|gift|budget|nasi7a|katnsa7ni|nakhod|livraison|delivery|payment|paiement|cod|"
    r"retours?|returns?|exchange|echange|échange|remboursement|refund|stock|availability|disponible|"
    r"taille|size|couleur|color|variant|prix|price|produit|product|commande|order)\b|"
    r"\b(?:je cherche|je veux commander|i want to order|bghit|bghet)\b|"
    r"chno kat propose|achmen wa7d ahsan|wach hadchi mzyan|tu me conseilles|chno t9der t3awni|"
    r"je cherche quelque chose pour|what (?:do|would) you recommend|which one (?:is better|do you recommend)|"
    r"بغيت|هدية|تنصحني|الثمن|التوصيل|المقاس|الطلبية", re.I)
CONFLICT = re.compile(r"\b(?:python|javascript|coding|code|politics|politique|history|histoire|news|"
                      r"homework|devoirs|cv|ronaldo|story|poem|poème|joke)\b", re.I)


def commerce_intent(value):
    if re.search(r"\b(?:return|retour|exchange|échange|refund|remboursement|status|suivi|policy|policies)\b", value):
        return "support"
    if re.search(r"\b(?:delivery|livraison|payment|paiement|cod|price|prix|stock|availability|disponible|commande|order)\b", value):
        return "purchase"
    if re.search(r"\b(?:size|taille|color|couleur|variant|compare|comparaison)\b", value):
        return "product"
    return "discovery"


def local_route(text, history=()):
    value = normalize(text)
    style = language_style(value, history)
    def decision(scope, intent, security="none"):
        return ScopeDecision(scope=scope, intent=intent, language_style=style, security_action=security)
    # Explicit unrelated tasks override superficial shopping words and old context.
    if UNRELATED.search(value):
        return decision("unrelated", "unrelated", "redirect" if INJECTION.search(value) else "none"), text
    if INJECTION.search(value):
        # Only salvage independent, clearly commercial clauses; never arbitrary substrings.
        clauses = re.split(r"[,.!?;\n]+|\b(?:and|et|but|mais)\b", text, flags=re.I)
        safe = [c.strip() for c in clauses if c.strip() and not INJECTION.search(normalize(c))
                and not UNRELATED.search(normalize(c)) and not CONFLICT.search(normalize(c))
                and SHOPPING.search(normalize(c))]
        if safe:
            return decision("commerce", "discovery", "ignore_injection"), ". ".join(safe)
        return decision("unrelated", "unrelated", "redirect"), text
    if SOCIAL.fullmatch(value) or re.fullmatch(r"(?:ah ok(?:ay)?|ok(?:ay)?|safi fhemtk(?: merci)?|bonne journée)[.!?]*", value):
        return decision("social", "social"), text
    if CONFLICT.search(value):
        # A product word is insufficient when the task/domain is ambiguous.
        return decision("unresolved", "unknown"), text
    if explanation_topic(value) is not None:
        return decision("commerce", "product"), text
    if SHOPPING.search(value):
        return decision("commerce", commerce_intent(value)), text
    return decision("unresolved", "unknown"), text


def context_window(history, count=6, chars=3000):
    selected = []
    for message in reversed(history[-count:]):
        if INJECTION.search(normalize(message.content)):
            continue
        if len(message.content) > chars:
            break
        selected.append(message)
        chars -= len(message.content)
    return list(reversed(selected))




def explanation_topic(text):
    """Small intent families; unknown targets need bounded semantic interpretation."""
    value = normalize(text).strip(" .!?")
    if explanation_target(text):
        return "out_of_stock"
    if re.fullmatch(r"ma\s?fh[ae]mtch|je n'ai pas compris|j'ai pas compris|i (?:didn't|did not|don't) understand|ما فهمتش", value):
        return "state"
    match = re.fullmatch(r"(?:chno kat3ni|ça veut dire quoi|qu'est-ce que ça veut dire|what does (?:that|this) mean)\s*(.*)", value)
    if match:
        target = match[1].strip(" .!?'")
        if not target:
            return "state"
        if target in ("salat l quantité", "salat l quantite", "rupture de stock", "out of stock"):
            return "out_of_stock"
        return "unknown"
    return None


def explanation_target(text):
    """Explicit named target, independently of any remembered focus."""
    value = normalize(text).strip(" .!?")
    match = re.fullmatch(r"(?:chno kat3ni|ça veut dire quoi|what does)\s+"
                         r"(?:salat (?:l )?quantit[eé]|rupture de stock|out of stock)\s+"
                         r"(?:dial|dyal|pour|for)\s+(.+)", value)
    return match[1] if match else None


def affirmative_selection(text):
    """Only a complete affirmative choice utterance can authorize selection.

    Reject questions, quotations, negation and compound/example utterances.
    Unknown formulations clarify; a planner cannot expand this authorization.
    """
    value = normalize(text).rstrip(" .!")
    if re.search(r"[?\"'«»;:,\n]|\b(?:pas|ne|non|not|don't|if|si|mais|but|ou|or|et|and|example|exemple)\b", value):
        return False
    referenced = bool(re.fullmatch(
        r"(?:nakhod|khod lia|bghit|je prends|je choisis|i choose|i take) "
        r"(?:hada|hadak|celui-là|celui-la|this|that|(?:le )?(?:premier|deuxième|deuxieme|second|tani|first|third))", value))
    if referenced:
        return True
    match = re.fullmatch(r"(?:nakhod|khod lia|bghit|je prends|je choisis|i choose|i take)\s+(.+)", value)
    if not match:
        return False
    from app.ai.attribute_adapter import CATALOG_ATTRIBUTES
    request = CATALOG_ATTRIBUTES.inspect(match[1])
    return bool(request.followup and request.requested and not set(request.requested) - set(CATALOG_ATTRIBUTES.supported))
