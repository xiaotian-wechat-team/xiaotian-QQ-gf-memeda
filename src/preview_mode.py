"""Request-local preview flag for indirect reply side effects."""
from contextlib import contextmanager
from contextvars import ContextVar

_PREVIEW = ContextVar("xiaotian_preview", default=False)


def is_preview():
    return _PREVIEW.get()


@contextmanager
def preview(enabled=True):
    token = _PREVIEW.set(is_preview() or enabled)
    try:
        yield
    finally:
        _PREVIEW.reset(token)
