"""Hybrid scoring helpers for symbol retrieval."""

from __future__ import annotations

import math
import os
import re


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def hybrid_search_enabled() -> bool:
    raw = os.getenv("BOMBE_HYBRID_SEARCH", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def semantic_vector_enabled() -> bool:
    raw = os.getenv("BOMBE_HYBRID_VECTOR", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _tokens(value: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_RE.finditer(value)}


def lexical_score(query: str, name: str, qualified_name: str) -> float:
    q = query.strip().lower()
    if not q:
        return 0.0
    n = name.lower()
    qn = qualified_name.lower()
    if q == n or q == qn:
        return 1.0
    if q in n:
        return 0.9
    if q in qn:
        return 0.8
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    target_tokens = _tokens(f"{name} {qualified_name}")
    if not target_tokens:
        return 0.0
    overlap = len(query_tokens & target_tokens)
    return overlap / max(1, len(query_tokens))


def structural_score(pagerank: float, callers: int, callees: int) -> float:
    pagerank_component = max(0.0, float(pagerank))
    traffic_component = math.log1p(max(0, int(callers)) + max(0, int(callees)))
    return pagerank_component + (traffic_component * 0.1)


def semantic_score(query: str, signature: str | None, docstring: str | None) -> float:
    if not semantic_vector_enabled():
        return 0.0
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    corpus_tokens = _tokens(f"{signature or ''} {docstring or ''}")
    if not corpus_tokens:
        return 0.0
    overlap = len(query_tokens & corpus_tokens)
    return overlap / max(1, len(query_tokens))


def rank_symbol(
    *,
    query: str,
    name: str,
    qualified_name: str,
    signature: str | None,
    docstring: str | None,
    pagerank: float,
    callers: int,
    callees: int,
) -> float:
    lexical = lexical_score(query, name, qualified_name)
    structural = structural_score(pagerank, callers, callees)
    semantic = semantic_score(query, signature, docstring)
    if not hybrid_search_enabled():
        return structural
    return (lexical * 0.55) + (structural * 0.35) + (semantic * 0.1)
