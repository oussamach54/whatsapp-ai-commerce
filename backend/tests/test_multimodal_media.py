"""No network: Meta metadata/download transport and Responses are mocked."""
import json
import logging
from unittest.mock import Mock

import httpx
import pytest

from app.core.config import Settings
from app.integrations.whatsapp.client import WhatsAppClient
from app.integrations.whatsapp.media import MediaError
from app.integrations.whatsapp.parser import parse_messages
from app.integrations.whatsapp.schemas import IncomingImage
from tests.test_whatsapp_webhook import payload

SIGNED = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=123&ext=secret-signed"
DATA = {"image/jpeg": b"\xff\xd8\xff" + b"jpeg-test", "image/png": b"\x89PNG\r\n\x1a\n" + b"png-test",
        "image/webp": b"RIFF1234WEBP" + b"webp-test"}


def settings(**kw):
    return Settings(_env_file=None, database_host="unused", database_name="unused", database_user="unused",
        database_password="test", whatsapp_phone_number_id="123456789", whatsapp_api_version="v23.0",
        whatsapp_access_token="secret-test-token", **kw)


def image_payload(caption=None, mime="image/jpeg"):
    data = payload()
    message = data["entry"][0]["changes"][0]["value"]["messages"][0]
    message.pop("text")
    message.update(type="image", image={"id": "123", "mime_type": mime})
    if caption is not None:
        message["image"]["caption"] = caption
    return data


@pytest.mark.parametrize("caption", [None, "wach 3ndkom b7al hada?", "avez-vous ceci?", "do you have this?"])
def test_image_webhook_parser(caption):
    incoming = parse_messages(image_payload(caption), "123456789")
    assert len(incoming) == 1 and incoming[0].image.id == "123"
    assert incoming[0].text == (caption or "[image]")


@pytest.mark.parametrize("mime", list(DATA))
def test_authenticated_bounded_meta_flow(mime, caplog):
    caplog.set_level(logging.DEBUG)
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer secret-test-token"
        assert request.extensions["timeout"]["read"] <= 10
        if request.url.host == "graph.facebook.com":
            assert request.url.path == "/v23.0/123"
            return httpx.Response(200, json={"url": SIGNED, "mime_type": mime, "file_size": len(DATA[mime])})
        return httpx.Response(200, content=DATA[mime], headers={"Content-Type": mime})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert WhatsAppClient(settings(), http).download_image(IncomingImage(id="123", mime_type=mime)) == (DATA[mime], mime)
    assert len(calls) == 2
    assert SIGNED not in caplog.text and "secret-test-token" not in caplog.text
    assert "jpeg-test" not in caplog.text and "Authorization" not in caplog.text


@pytest.mark.parametrize("failure", ["unsupported", "oversized_metadata", "oversized_stream", "metadata_error",
    "download_error", "timeout", "metadata_timeout", "bad_signature", "mismatched_mime", "redirect",
    "compressed", "invalid_json", "metadata_too_large", "negative_length"])
def test_media_rejected_safely(failure, caplog):
    caplog.set_level(logging.DEBUG)
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.host == "graph.facebook.com":
            if failure == "metadata_error":
                return httpx.Response(403, text=SIGNED + "secret-test-token")
            if failure == "metadata_timeout":
                raise httpx.ReadTimeout(SIGNED)
            if failure == "invalid_json":
                return httpx.Response(200, text="invalid")
            if failure == "metadata_too_large":
                return httpx.Response(200, content=b"x" * 17000)
            return httpx.Response(200, json={"url": SIGNED, "mime_type": "image/jpeg",
                "file_size": 9000 if failure == "oversized_metadata" else 20})
        if failure == "timeout":
            raise httpx.ReadTimeout(SIGNED + "secret-test-token")
        if failure == "download_error":
            return httpx.Response(500, text=SIGNED)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/"})
        headers = {"Content-Type": "image/png" if failure == "mismatched_mime" else "image/jpeg"}
        if failure == "compressed":
            headers["Content-Encoding"] = "gzip"
        if failure == "negative_length":
            headers["Content-Length"] = "-1"
        if failure == "oversized_stream":
            return httpx.Response(200, stream=httpx.ByteStream(b"\xff\xd8\xff" + b"x" * 2000), headers=headers)
        return httpx.Response(200, content=b"bad" if failure == "bad_signature" else DATA["image/jpeg"], headers=headers)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError) as error:
            WhatsAppClient(settings(whatsapp_image_max_bytes=1024), http).download_image(
                IncomingImage(id="123", mime_type="image/svg+xml" if failure == "unsupported" else "image/jpeg"))
    assert SIGNED not in str(error.value) and SIGNED not in caplog.text
    assert "secret-test-token" not in caplog.text
    assert len(calls) <= 2


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data",
    "https://attacker.example/a", "file:///etc/passwd", "https://lookaside.fbsbx.com.attacker.example/",
    "https://lookaside.fbsbx.com@attacker.example/", "https://lookaside.fbsbx.com:444/whatsapp_business/attachments/",
    "https://lookaside.fbsbx.com/not-media/", "https://lookaside.fbsbx.com/whatsapp_business/attachments/#fragment"])
def test_media_url_allowlist_never_downloads_untrusted_origin(url):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"url": url, "mime_type": "image/jpeg", "file_size": 20})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError):
            WhatsAppClient(settings(), http).download_image(IncomingImage(id="123"))
    assert len(calls) == 1


def test_media_deadline_enforced_between_chunks(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.integrations.whatsapp.media.monotonic", lambda: clock[0])
    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"\xff\xd8\xff"
            clock[0] = 11.0
            yield b"next"
            pytest.fail("read past deadline")
    def handler(request):
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json={"url": SIGNED, "mime_type": "image/jpeg", "file_size": 20})
        return httpx.Response(200, stream=SlowStream(), headers={"Content-Type": "image/jpeg"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError, match="media_timeout"):
            WhatsAppClient(settings(), http).download_image(IncomingImage(id="123"))
