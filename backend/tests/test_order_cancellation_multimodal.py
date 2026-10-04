"""Images and links supersede a cancellation prompt but cannot supply consent."""
import pytest
from sqlalchemy import select

from app.models import Order
from app.models.enums import OrderStatus
from tests.test_multimodal_commerce import multimodal, observation, URL
from tests.test_cod_checkout import checkout, cart
from tests.test_commerce_conversations import dialogue
from tests.test_catalog_variant_followups import pants
from tests.test_order_cancellation import completed, restored


@pytest.mark.parametrize("source", ["image", "link", "failed_link"])
def test_multimodal_input_cannot_confirm_cancellation(multimodal, db_session, source):
    completed(multimodal.checkout, db_session)
    order = db_session.scalar(select(Order))
    multimodal.checkout("cancel my order")
    if source == "image":
        multimodal("oui", observation("oui", intent="purchase", speech_act="affirmative"))
    else:
        multimodal("oui " + (URL if source == "link" else URL + "-missing"), image=False)
    multimodal.checkout("oui")
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED and not restored(db_session, order)
