"""Token estimation helpers with optional model-aware tokenization."""

from __future__ import annotations

from functools import lru_cache


def _fallback_estimate(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 3.5))


@lru_cache(maxsize=16)
def _encoding_for_model(model: str):
    try:
        import tiktoken  # type: ignore
    except Exception:
        return None
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None


def estimate_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    if not text:
        return 0
    encoding = _encoding_for_model(model)
    if encoding is None:
        return _fallback_estimate(text)
    try:
        return max(1, len(encoding.encode(text)))
    except Exception:
        return _fallback_estimate(text)
