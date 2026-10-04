"""Application-owned storefront URLs. This module never makes network requests."""
import ipaddress
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

URL_PATTERN = re.compile(r"(?:[a-zA-Z][a-zA-Z0-9+.-]*://|(?:https?|file|javascript|data|ftp|mailto):|www\.)[^\s<>]+", re.IGNORECASE)


def origin(value):
    if not value or len(value) > 2048 or any(ord(c) <= 32 or c == "\\" for c in value):
        raise ValueError("Invalid storefront URL")
    parsed = urlsplit(value)
    host = parsed.hostname
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("Storefront requires an HTTPS origin")
    if host == "localhost" or "." not in host or host.endswith((".localhost", ".local", ".internal", ".")):
        raise ValueError("Storefront requires a public hostname")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Storefront IP literals are unsupported")
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ValueError("Invalid storefront host")
    return parsed, (parsed.scheme, host, parsed.port or 443)


def validate_configuration(base, template):
    if base:
        parsed, _ = origin(base)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Storefront base must be an origin")
    if not re.fullmatch(r"/(?:[a-zA-Z0-9_-]+/)*\{slug\}", template):
        raise ValueError("Product path must end in one {slug} segment")


def safe_slug(slug):
    return bool(isinstance(slug, str) and 0 < len(slug) <= 255 and slug not in (".", "..")
                and not any(c in slug for c in "/\\%?#")
                and not any(ord(c) < 32 or ord(c) == 127 for c in slug))


def product_url(settings, slug):
    if not settings.storefront_base_url or not safe_slug(slug):
        return None
    parsed, _ = origin(settings.storefront_base_url)
    path = settings.storefront_product_path_template.replace("{slug}", quote(slug, safe=""))
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def extract_link(text, settings):
    """None: ordinary text. Otherwise (verified-shape slug or None, caption)."""
    matches = list(URL_PATTERN.finditer(text))
    if not matches:
        return None
    caption = URL_PATTERN.sub("", text).strip()
    if len(matches) != 1 or not settings.storefront_base_url:
        return None, caption
    value = matches[0].group().rstrip(".,!;)")
    try:
        parsed, candidate_origin = origin(value)
        _, trusted_origin = origin(settings.storefront_base_url)
        prefix = settings.storefront_product_path_template.removesuffix("{slug}")
        if candidate_origin != trusted_origin or not parsed.path.startswith(prefix):
            return None, caption
        slug = unquote(parsed.path[len(prefix):], errors="strict")
        if not safe_slug(slug) or quote(slug, safe="") != parsed.path[len(prefix):]:
            return None, caption
        return slug, caption
    except (ValueError, UnicodeError):
        return None, caption
