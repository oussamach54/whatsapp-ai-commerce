"""Suppress provider debug payloads only during sensitive I/O in this context."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging

_private = ContextVar("private_provider_io", default=False)


class _PrivateFilter(logging.Filter):
    def filter(self, record):
        return not _private.get()


for _name in ("httpx", "httpx2", "httpcore.connection", "httpcore.http11", "httpcore.http2",
              "httpcore.proxy", "httpcore.socks", "httpcore2.connection", "httpcore2.http11",
              "httpcore2.http2", "openai._base_client", "openai._utils._logs"):
    logging.getLogger(_name).addFilter(_PrivateFilter())


@contextmanager
def private_provider_io():
    token = _private.set(True)
    try:
        yield
    finally:
        _private.reset(token)
