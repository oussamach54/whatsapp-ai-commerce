"""Bounded, authenticated Meta image retrieval; no disk storage or URL logging."""
import json
import re
from time import monotonic
from urllib.parse import urlsplit

import httpx

from app.core.sensitive_io import private_provider_io

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


class MediaError(Exception):
    def __init__(self, category="media_unavailable"):
        super().__init__(category)
        self.category = category


def image_signature(data, mime):
    return ((mime == "image/jpeg" and data.startswith(b"\xff\xd8\xff")) or
            (mime == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or
            (mime == "image/webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP"))


def download_image(client, media):
    settings = client._settings
    if media.mime_type is not None and media.mime_type not in IMAGE_TYPES:
        raise MediaError("unsupported_image")
    if not re.fullmatch(r"[0-9]{1,255}", media.id):
        raise MediaError()
    deadline = monotonic() + settings.whatsapp_media_timeout_seconds
    headers = {"Authorization": f"Bearer {client._token}", "Accept-Encoding": "identity"}

    def read(url, limit, expected=None):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise MediaError("media_timeout")
        with client._http.stream("GET", url, headers=headers, timeout=httpx.Timeout(remaining),
                                 follow_redirects=False) as response:
            if response.status_code != 200:
                raise MediaError()
            if response.headers.get("content-encoding", "identity") != "identity":
                raise MediaError()
            if expected and response.headers.get("content-type", "").split(";")[0].strip().lower() != expected:
                raise MediaError("unsupported_image")
            length = response.headers.get("content-length")
            if length is not None and (not length.isdecimal() or int(length) > limit):
                raise MediaError("image_too_large")
            data = bytearray()
            # Inspect each transport chunk rather than waiting for a large
            # decoded buffer; this also enforces the deadline on slow streams.
            chunks = [response.content] if response.is_stream_consumed else response.iter_raw()
            for chunk in chunks:
                if monotonic() > deadline:
                    raise MediaError("media_timeout")
                if len(data) + len(chunk) > limit:
                    raise MediaError("image_too_large")
                data.extend(chunk)
            return bytes(data)

    try:
        with private_provider_io():
            metadata = json.loads(read(f"https://graph.facebook.com/{settings.whatsapp_api_version}/{media.id}", 16384))
            mime = metadata.get("mime_type")
            if mime not in IMAGE_TYPES or (media.mime_type is not None and media.mime_type != mime):
                raise MediaError("unsupported_image")
            size = metadata.get("file_size")
            if type(size) is not int or size <= 0 or size > settings.whatsapp_image_max_bytes:
                raise MediaError("image_too_large")
            url = metadata.get("url")
            if not isinstance(url, str) or len(url) > 4096 or any(ord(c) <= 32 or c == "\\" for c in url):
                raise MediaError()
            parsed = urlsplit(url)
            # Fixed Meta media origin, no wildcards, redirects, credentials or IPs.
            if (parsed.scheme != "https" or parsed.hostname != "lookaside.fbsbx.com" or
                    parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment or
                    parsed.path != "/whatsapp_business/attachments/"):
                raise MediaError()
            data = read(url, settings.whatsapp_image_max_bytes, mime)
            if not image_signature(data, mime):
                raise MediaError("unsupported_image")
            return data, mime
    except MediaError:
        raise
    except httpx.TimeoutException:
        raise MediaError("media_timeout") from None
    except Exception:
        raise MediaError() from None
