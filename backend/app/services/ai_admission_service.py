"""Short, committed PostgreSQL transactions. Never call providers under locks."""
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm.attributes import flag_modified

from app.models import AIBudget, Conversation, Customer, Message
from app.models.enums import MessageDirection, SenderType
from app.ai.scope import normalize
from app.services.common import transaction

logger = logging.getLogger(__name__)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
VERSION = "5.3.1"


class AIAdmission:
    def __init__(self, sessions, settings, target):
        self.sessions, self.settings, self.target = sessions, settings, target

    @staticmethod
    def _now(db):
        return db.scalar(select(func.clock_timestamp())).astimezone(timezone.utc)

    def _anchor(self, db):
        message = db.scalars(select(Message).where(
            Message.id == self.target.inbound_id, Message.conversation_id == self.target.conversation_id,
            Message.direction == MessageDirection.INBOUND, Message.sender_type == SenderType.CUSTOMER,
        ).with_for_update()).one()
        customer = db.scalars(select(Customer).join(Conversation).where(
            Conversation.id == message.conversation_id)).one()
        business = (message.metadata_ or {}).get("phone_number_id")
        if not isinstance(business, str) or not business:
            raise ValueError("Missing business identity")
        namespace = hashlib.sha256(business.encode()).hexdigest()[:32]
        identity = f"{namespace}:{customer.id}"
        # Serializes one customer's checks across conversations and workers.
        lock = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], signed=True)
        db.execute(select(func.pg_advisory_xact_lock(lock)))
        now = self._now(db)
        return message, customer, namespace, identity, now

    @staticmethod
    def _counter(db, key, start, expires):
        db.execute(insert(AIBudget).values(key=key, window_start=start, expires_at=expires, count=0)
                   .on_conflict_do_nothing())
        return db.scalars(select(AIBudget).where(AIBudget.key == key,
            AIBudget.window_start == start).with_for_update()).one()

    def _notice(self, db, identity, now):
        row = self._counter(db, f"notice:{identity}", EPOCH,
                            now + timedelta(seconds=self.settings.ai_notice_cooldown_seconds))
        previous = (row.state or {}).get("last", 0)
        if now.timestamp() - previous < self.settings.ai_notice_cooldown_seconds:
            return False
        row.state = {"last": now.timestamp()}
        row.expires_at = now + timedelta(seconds=self.settings.ai_notice_cooldown_seconds)
        return True

    def begin(self):
        """Claim once even when work fails later; duplicate jobs never restart paid work."""
        with self.sessions() as db, transaction(db):
            message, customer, business, identity, now = self._anchor(db)
            metadata = dict(message.metadata_ or {})
            if "ai_guard" in metadata:
                return "duplicate"
            guard = {"version": VERSION, "started_at": now.isoformat(), "attempts": {},
                     "category": "processing", "path": "local", "fallback": False}
            metadata["ai_guard"] = guard
            message.metadata_ = metadata
            if customer.is_blocked:
                guard.update(category="blocked", exclude_history=True)
                flag_modified(message, "metadata_")
                return "blocked"
            # Prune at most 100 expired rows, skipping other workers' locked rows.
            expired = select(AIBudget.key, AIBudget.window_start).where(AIBudget.expires_at < now).order_by(
                AIBudget.expires_at).limit(100).with_for_update(skip_locked=True)
            db.execute(delete(AIBudget).where(tuple_(AIBudget.key, AIBudget.window_start).in_(expired)),
                       execution_options={"synchronize_session": False})
            minute = now.replace(second=0, microsecond=0)
            count = self._counter(db, f"inbound:{identity}", minute, minute + timedelta(minutes=1))
            count.count += 1
            if count.count > self.settings.ai_inbound_per_minute:
                guard.update(category="rate_limited", exclude_history=True)
                flag_modified(message, "metadata_")
                return "notice" if self._notice(db, identity, now) else "suppressed"
            digest = hashlib.sha256(normalize(message.content or "").encode()).hexdigest()
            spam = self._counter(db, f"spam:{identity}:{digest}", EPOCH,
                                 now + timedelta(seconds=self.settings.ai_spam_window_seconds))
            cutoff = now.timestamp() - self.settings.ai_spam_window_seconds
            times = [t for t in (spam.state or {}).get("times", []) if t > cutoff]
            times.append(now.timestamp())
            spam.state = {"times": times[-self.settings.ai_spam_threshold:]}
            spam.expires_at = now + timedelta(seconds=self.settings.ai_spam_window_seconds)
            if len(times) >= self.settings.ai_spam_threshold:
                guard.update(category="spam", exclude_history=True)
                flag_modified(message, "metadata_")
                return "notice" if self._notice(db, identity, now) else "suppressed"
            return "allowed"

    def reserve(self, stage, model):
        if stage not in ("classifier", "generation", "generation_2", "generation_3"):
            raise ValueError("Unknown stage")
        with self.sessions() as db, transaction(db):
            message, customer, business, identity, now = self._anchor(db)
            metadata = dict(message.metadata_ or {})
            guard = dict(metadata.get("ai_guard") or {})
            attempts = dict(guard.get("attempts") or {})
            previous = {"generation_2": "generation", "generation_3": "generation_2"}.get(stage)
            if previous and attempts.get(previous, {}).get("status") != "completed":
                return "suppressed"
            if customer.is_blocked or not guard or stage in attempts or guard.get("category") in (
                "blocked", "spam", "rate_limited", "too_long", "invalid_input", "unrelated", "social"):
                return "suppressed"
            hour, day = now.replace(minute=0, second=0, microsecond=0), now.replace(hour=0, minute=0, second=0, microsecond=0)
            specs = [
                (f"paid:business:{business}", day, 86400, self.settings.ai_business_attempts_per_day),
                (f"paid:customer:day:{identity}", day, 86400, self.settings.ai_customer_attempts_per_day),
                (f"paid:customer:hour:{identity}", hour, 3600, self.settings.ai_customer_attempts_per_hour),
            ]
            rows = [(self._counter(db, key, start, start + timedelta(seconds=duration)), limit)
                    for key, start, duration, limit in sorted(specs)]
            if any(row.count >= limit for row, limit in rows):
                guard.update(category="rate_limited", exclude_history=True)
                metadata["ai_guard"] = guard
                message.metadata_ = metadata
                return "notice" if self._notice(db, identity, now) else "suppressed"
            for row, limit in rows:
                row.count += 1
            attempts[stage] = {"status": "reserved", "model": model, "started_at": now.isoformat(),
                               "input_tokens": None, "output_tokens": None, "cached_input_tokens": None}
            guard["attempts"] = attempts
            metadata["ai_guard"] = guard
            message.metadata_ = metadata
            return "allowed"

    def record(self, *, stage=None, values=None, **decision):
        """Inputs are application-owned structured values, never provider bodies."""
        with self.sessions() as db, transaction(db):
            message = db.scalars(select(Message).where(Message.id == self.target.inbound_id).with_for_update()).one()
            metadata = dict(message.metadata_ or {})
            guard = dict(metadata.get("ai_guard") or {})
            now = db.scalar(select(func.clock_timestamp())).isoformat()
            if stage:
                attempts = dict(guard.get("attempts") or {})
                attempts[stage] = dict(attempts.get(stage) or {}) | (values or {}) | {"finished_at": now}
                guard["attempts"] = attempts
            guard.update(decision)
            guard["updated_at"] = now
            metadata["ai_guard"] = guard
            message.metadata_ = metadata

    def safe_record(self, **kwargs):
        try:
            self.record(**kwargs)
            return True
        except Exception:
            logger.warning("ai.telemetry_unavailable")
            return False
