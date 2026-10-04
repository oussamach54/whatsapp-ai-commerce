import pytest
from app.services.product_links import extract_link, product_url
from tests.test_multimodal_media import settings

BASE = "https://shop.example.com"


@pytest.mark.parametrize("suffix", ["", "?price=1&stock=999", "#discount"])
def test_trusted_link_exact_origin_and_slug(suffix):
    config = settings(storefront_base_url=BASE)
    assert extract_link(BASE + "/products/pantalon-classic" + suffix + " wach kayn M?", config) == ("pantalon-classic", "wach kayn M?")


@pytest.mark.parametrize("url", ["https://shop.example.com.attacker.org/products/pants", "https://attacker.org/products/pants",
    "http://shop.example.com/products/pants", "https://shop.example.com:444/products/pants",
    "https://shop.example.com@attacker.org/products/pants", "file:///etc/passwd", "ftp://shop.example.com/products/pants",
    "http://localhost/", "http://127.0.0.1/", "http://10.0.0.1/", "http://169.254.169.254/",
    "https://shop.example.com/products/%2F%2Fevil", "https://shop.example.com/products/..",
    "https://shop.example.com/products/%252e%252e", "https://shop.example.com/products/a/b",
    "https://shop.example.com/products/", "https://shop.example.com:bad/products/pants", "www.attacker.org/pants",
    "javascript:alert(1)", "data:text/html,secret", "https:/shop.example.com/products/pants"])
def test_untrusted_or_malformed_link_no_resolution(url):
    assert extract_link(url, settings(storefront_base_url=BASE))[0] is None


@pytest.mark.parametrize("slug", ["pantalon-classic", "café bleu", "قميص", "cotton&linen"])
def test_application_owned_url_roundtrip(slug):
    config = settings(storefront_base_url=BASE, storefront_product_path_template="/shop/item/{slug}")
    url = product_url(config, slug)
    assert url.startswith(BASE + "/shop/item/")
    assert extract_link(url, config)[0] == slug


@pytest.mark.parametrize("slug", ["../evil", "//attacker.org", "https://attacker.org", "%2fetc", "..", "a\\b", "x?price=1", "x#stock", "a\nurl"])
def test_slug_cannot_escape_storefront(slug):
    assert product_url(settings(storefront_base_url=BASE), slug) is None


@pytest.mark.parametrize("base", ["http://shop.example.com", "https://localhost", "https://127.0.0.1",
    "https://shop.example.com/path", "https://shop.example.com?x=y", "https://user:pw@shop.example.com"])
def test_invalid_storefront_configuration(base):
    with pytest.raises(ValueError):
        settings(storefront_base_url=base)


def test_disabled_storefront_and_multiple_links():
    assert product_url(settings(), "pants") is None
    assert extract_link("bonjour", settings()) is None
    assert extract_link(BASE + "/products/pants", settings())[0] is None
    assert extract_link(BASE + "/products/pants " + BASE + "/products/shirt", settings(storefront_base_url=BASE))[0] is None
