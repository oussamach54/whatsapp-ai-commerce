import logging

from app.ai.prompts import FALLBACK_REPLY
from app.ai.replies import local_reply
from app.ai.schemas import AIHistoryMessage, SalesReply
from app.ai.scope import INJECTION, context_window, language_style, local_route, normalize

logger = logging.getLogger(__name__)


class SalesRouter:
    def __init__(self, service, classifier, admission, settings, catalog=None):
        self.service, self.classifier, self.admission, self.settings = service, classifier, admission, settings
        self.catalog = catalog

    def _reserve(self, stage, model):
        try:
            return self.admission.reserve(stage, model)
        except Exception:
            logger.warning("ai.admission_unavailable")
            return "suppressed"

    def reply(self, text, conversation_id, load_history):
        style = language_style(text)
        try:
            status = self.admission.begin()
        except Exception:
            logger.warning("ai.admission_unavailable")
            return SalesReply(None, True)
        if status != "allowed":
            return SalesReply(local_reply("notice", style) if status == "notice" else None, True)
        if len(text) > self.settings.ai_max_input_chars:
            self.admission.safe_record(category="too_long", path="local", exclude_history=True, fallback=False)
            return SalesReply(local_reply("shorten", style), True)
        try:
            AIHistoryMessage(role="user", content=text)
        except ValueError:
            self.admission.safe_record(category="invalid_input", path="local", exclude_history=True, fallback=False)
            return SalesReply(local_reply("clarify", style), True)

        decision, safe_text = local_route(text)
        # Social/obvious unrelated shortcuts need no history query.
        history = []
        if decision.scope not in ("social", "unrelated"):
            try:
                history = load_history()
            except Exception:
                logger.warning("ai.history_unavailable")
        # Drop obvious legacy injection, even if no application label exists yet.
        history = [m for m in history if not INJECTION.search(normalize(m.content))]
        decision, safe_text = local_route(text, history)
        if self.catalog is not None:
            from app.services.checkout_service import answer, CANCELLATIONS
            # Acknowledgements are commercial only when an application cart exists.
            if answer(safe_text) is not None or normalize(safe_text).strip(" .!") in CANCELLATIONS:
                from app.services.checkout_service import load_cart
                try:
                    cart, _ = load_cart(self.catalog)
                    if cart:
                        decision = decision.model_copy(update={"scope": "commerce", "intent": "purchase"})
                except Exception:
                    return SalesReply(None, True)
        if self.catalog is not None and decision.scope == "unresolved":
            from app.ai.conversation_engine import contextual_shortcut
            if contextual_shortcut(safe_text):
                decision = decision.model_copy(update={"scope": "commerce", "intent": "product"})
        if self.catalog is not None and decision.scope not in ("social", "unrelated"):
            try:
                with self.catalog.sessions() as db:
                    self.catalog.authorize(db)
            except Exception:
                self.admission.safe_record(fallback=True, failure_category="catalog_scope_unavailable")
                return SalesReply(None, True)
        if self.catalog is not None and decision.scope == "unresolved":
            from app.ai.conversation_engine import load_memory
            from app.ai.scope import CONFLICT
            try:
                refs, _, memory, _ = load_memory(self.catalog)
                recent = (memory.commercial_at is not None and
                          0 <= (self.catalog.turn_time - memory.commercial_at).total_seconds() <= 900)
                if (memory.pending or memory.purchase or memory.cart or (refs.focus and recent)) and not CONFLICT.search(normalize(safe_text)):
                    decision = decision.model_copy(update={"scope": "commerce", "intent": "product"})
            except Exception:
                return SalesReply(None, True)
        path = "local"
        if decision.scope == "unresolved":
            # No configured classifier means clarify locally, not an expensive fallback.
            if not self.settings.ai_classifier_model or not self.settings.openai_api_key:
                self.admission.safe_record(category="unresolved", path="local", fallback=False)
                return SalesReply(local_reply("clarify", decision.language_style))
            status = self._reserve("classifier", self.settings.ai_classifier_model)
            if status != "allowed":
                return SalesReply(local_reply("notice", decision.language_style) if status == "notice" else None, True)
            telemetry = {}
            classified = self.classifier.classify(text, context_window(history), telemetry)
            recorded = self.admission.safe_record(stage="classifier", values=telemetry)
            path = "classifier"
            if classified is None or not recorded:
                self.admission.safe_record(category="unresolved", path=path, fallback=False)
                return SalesReply(local_reply("clarify", decision.language_style))
            decision = classified
        exclude = decision.scope == "unrelated" or decision.security_action != "none"
        recorded = self.admission.safe_record(category=decision.scope, intent=decision.intent, path=path,
                                   language_style=decision.language_style,
                                   security_action=decision.security_action, exclude_history=exclude, fallback=False)
        if not recorded:
            return SalesReply(None, True)
        if decision.security_action == "redirect" or decision.scope == "unrelated":
            return SalesReply(local_reply("redirect", decision.language_style), True)
        if decision.scope == "social":
            return SalesReply(local_reply("social", decision.language_style, text),
                              preserve_commerce=self.catalog is not None)
        if decision.scope != "commerce":
            return SalesReply(local_reply("clarify", decision.language_style))
        # The classifier cannot safely rewrite a mixed malicious request. Local
        # extraction may salvage independent clauses; otherwise ask a clarification.
        if decision.security_action == "ignore_injection" and safe_text == text:
            return SalesReply(local_reply("clarify", decision.language_style), True)
        if not self.settings.openai_model or not self.settings.openai_api_key:
            self.admission.safe_record(fallback=True, failure_category="not_configured")
            return SalesReply(FALLBACK_REPLY)
        if self.catalog is not None:
            from app.ai.catalog_orchestrator import run_catalog
            result = run_catalog(self.service, safe_text, history, decision.language_style, self.admission, self.catalog)
            result.exclude_history = exclude
            return result
        if self.service.catalog_enabled:
            return SalesReply(FALLBACK_REPLY)
        status = self._reserve("generation", self.settings.openai_model)
        if status != "allowed":
            return SalesReply(local_reply("notice", decision.language_style) if status == "notice" else None, True)
        telemetry = {}
        reply = self.service.generate_reply(safe_text, conversation_id, history, telemetry=telemetry)
        self.admission.safe_record(stage="generation", values=telemetry, path="generation",
                                   fallback=telemetry.get("fallback", False))
        return SalesReply(reply, exclude)
