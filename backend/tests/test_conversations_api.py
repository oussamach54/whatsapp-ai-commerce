from datetime import datetime, timedelta, timezone
from uuid import UUID
from app.models import Message

def test_conversation_messages(client, customer, db_session):
    response = client.post("/api/conversations", json={"customer_id": customer["id"]})
    assert response.status_code == 201
    path = f"/api/conversations/{response.json()['id']}"
    assert client.get(path).json()["customer"]["id"] == customer["id"]
    ids = []
    for i in range(3):
        response = client.post(path + "/messages", json={"direction": "inbound", "sender_type": "customer", "content": str(i), "metadata": {"index": i}})
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
        # PostgreSQL now() is transaction-stable; set distinct times inside the test's outer transaction.
        db_session.get(Message, UUID(ids[-1])).created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i)
        db_session.commit()
    messages = client.get(path + "/messages").json()
    assert [m["id"] for m in messages] == ids
    assert messages[0]["metadata"] == {"index": 0}
    assert client.get(path + "/messages?limit=1&offset=1").json()[0]["id"] == ids[1]
    assert client.post(path + "/messages", json={"direction": "bad", "sender_type": "customer"}).status_code == 422
