"""Pre-order checkpoints survive a failed WhatsApp send; no external calls."""
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

from sqlalchemy import select

from app.integrations.whatsapp.client import WhatsAppAPIError
from app.integrations.whatsapp.schemas import ReplyTarget
from app.models import Message
from app.models.enums import MessageDirection, MessageType, SenderType
from app.services.whatsapp_service import send_automatic_reply
from tests.test_cod_checkout import checkout, plan, order_count
from tests.test_commerce_conversations import dialogue
from tests.test_catalog_variant_followups import pants


def test_pending_cart_survives_transport_failure_and_question(checkout, pants, db_session):
    website = "salam bghet n commandé f site ms makhdamch"
    invitation = checkout(website, [plan(website, "website_ordering", reference="none", language="darija_latin")])
    sender = Mock()
    sender.send_text_message.side_effect = WhatsAppAPIError("WhatsApp transport failure")
    catalog = checkout.catalog

    def process(body, seconds):
        inbound = Message(conversation_id=invitation.conversation_id, direction=MessageDirection.INBOUND,
            sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content=body,
            created_at=invitation.created_at + timedelta(seconds=seconds),
            metadata_={"provider": "whatsapp", "phone_number_id": catalog.settings.whatsapp_phone_number_id})
        db_session.add(inbound)
        db_session.commit()
        target = ReplyTarget(conversation_id=inbound.conversation_id, inbound_id=inbound.id,
            phone_number="212600001111")
        send_automatic_reply(target, sender, catalog.sessions, body, checkout.service)
        db_session.expire_all()
        return inbound

    inbound = process("pantalon M bleu", 1)
    saved = inbound.metadata_["checkout_state"]
    assert saved["status"] == "awaiting_confirmation"
    assert saved["items"][0]["target"]["variant_id"] == str(pants[3].id)
    assert saved["items"][0]["unit_price"] == "229.00" and saved["items"][0]["stock"] == 3
    assert "229.00 MAD" in sender.send_text_message.call_args.args[1]
    assert not db_session.scalar(select(Message.id).where(Message.metadata_["in_reply_to"].astext == str(inbound.id)))
    # Even the incident's website interpretation for '?' must use the durable cart.
    checkout.service._client.respond.side_effect = None
    checkout.service._client.respond.return_value = plan("?", "website_ordering", reference="none", speech_act="question")
    sender.send_text_message.side_effect = None
    sender.send_text_message.return_value = uuid4().hex
    followup = process("?", 2)
    outbound = db_session.scalar(select(Message).where(Message.metadata_["in_reply_to"].astext == str(followup.id)))
    assert outbound.metadata_["commerce_state"]["cart"]["id"] == saved["id"]
    assert "229.00 MAD" in outbound.content and "Que souhaitez" not in outbound.content
    assert order_count(db_session) == 0 and pants[3].stock_quantity == 3
