"""Application-owned extension point for verified policy/configuration sources.

Deployment configuration supplies verified delivery facts. Model proposals never
populate this source; other policies remain unknown without a verified source.
"""
from dataclasses import dataclass
from typing import Protocol

from app.ai.catalog_schemas import BusinessTopic


@dataclass(frozen=True)
class VerifiedBusinessAnswer:
    text: str
    source: str


class BusinessKnowledge(Protocol):
    def lookup(self, topic: BusinessTopic, *, quantity: int | None, language: str) -> VerifiedBusinessAnswer | None: ...


class UnconfiguredBusinessKnowledge:
    def lookup(self, topic, *, quantity=None, language=None):
        return None


BUSINESS_KNOWLEDGE: BusinessKnowledge = UnconfiguredBusinessKnowledge()


def delivery_summary(settings, style):
    """Shared by policy answers and successfully committed order receipts."""
    from app.ai.checkout import phrase
    policy = settings.delivery_policy
    if not policy.enabled:
        return ""
    parts = []
    if policy.eta_min_days is not None and policy.eta_max_days is not None:
        days = str(policy.eta_min_days) if policy.eta_min_days == policy.eta_max_days else f"{policy.eta_min_days}–{policy.eta_max_days}"
        parts.append(phrase(style, f"La livraison prend généralement {days} jour(s).",
            f"Delivery generally takes {days} day(s).", f"Ghaliban livraison katwsel f {days} jours.",
            f"غالباً التوصيل كياخد {days} أيام."))
    if policy.contact_before_arrival is True:
        parts.append(phrase(style, "Vous serez contacté avant l'arrivée de la livraison.",
            "You will be contacted before delivery arrives.", "Ghadi ytwaslo m3ak 9bel ma twsel commande.",
            "غادي يتم التواصل معاك قبل ما توصل الطلبية."))
    return "\n".join(parts)


class ConfiguredBusinessKnowledge:
    def __init__(self, settings):
        self.settings = settings

    def lookup(self, topic, *, quantity=None, language="french"):
        from app.ai.checkout import phrase
        if topic == "delivery_time":
            text = delivery_summary(self.settings, language)
            if text:
                # Contact policy alone is not an ETA; keep that distinction explicit.
                if self.settings.delivery_policy.eta_min_days is None:
                    text = phrase(language, "Le délai de livraison n'est pas confirmé.",
                        "The delivery time is not confirmed.", "Ma 3ndich délai livraison confirmé daba.",
                        "ما عنديش مدة توصيل مؤكدة دابا.") + "\n" + text
                return VerifiedBusinessAnswer(text, "deployment.delivery_policy")
        if topic == "delivery_price" and self.settings.checkout_shipping_cost is not None:
            amount = f"{self.settings.checkout_shipping_cost:.2f} {self.settings.catalog_currency}"
            return VerifiedBusinessAnswer(phrase(language, f"Les frais de livraison sont de {amount}.",
                f"Delivery costs {amount}.", f"Frais livraison homa {amount}.", f"مصاريف التوصيل هي {amount}."),
                "deployment.checkout_shipping_cost")
        return None


def answer_business_question(topic, quantity, style, settings=None):
    answer = ConfiguredBusinessKnowledge(settings).lookup(topic, quantity=quantity, language=style) if settings else None
    if answer is None:
        answer = BUSINESS_KNOWLEDGE.lookup(topic or "other", quantity=quantity, language=style)
    if answer is not None and answer.source and 0 < len(answer.text) <= 2000:
        return answer.text
    return {
        "english": "I don't have verified information about that yet. The team would need to confirm it.",
        "french": "Je n’ai pas encore d’information confirmée à ce sujet. Il faut vérifier avec l’équipe.",
        "darija_latin": "Ma 3ndich had lma3louma m2ekkda daba. Khass ta2kid men l’équipe.",
        "darija_arabic": "ما عنديش هاد المعلومة مؤكدة دابا. خاص التأكيد من الفريق.",
    }["darija_latin" if style == "mixed" else style]
