"""Integration seams for external data sources.

AI-generated content (answers, rephrasing, summaries, report text) comes from the AI Scientific Search service,
see app/services/scientific_search.py. This module holds the data-source registry: one BaseConnector subclass per
source (PubMed, Citeline, IQVIA ...), registered under the name stored in `connectors.name`. A source without a
registered client is reported as "not connected"; nothing is ever made up for it.
"""
from __future__ import annotations

import re

CATEGORIES = ["Scientific & Biological", "Clinical", "Regulatory & Safety",
              "Commercial, Epidemiology & RWD", "IP & Patents"]


def words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def in_scope(question: str, module: str, context: dict | None = None) -> bool:
    """Guardrail before a billed AI call: does this module answer this question? Keyword check including the user's
    research context (target, indications). Modules without a scope definition accept everything."""
    from app.services.catalog import SCOPE_PATTERN
    pat = SCOPE_PATTERN.get(module)
    if not pat:
        return True
    ctx = context or {}
    extra = [ctx.get("target", "")] + list(ctx.get("indications") or [])
    extra_pat = "|".join(re.escape(x.lower()) for x in extra if x)
    q = (question or "").lower()
    return bool(re.search(pat, q) or (extra_pat and re.search(extra_pat, q)))


# ---------------------------------------------------------------- data-source clients
class BaseConnector:
    name = "base"

    def search(self, question: str, context: dict) -> list[dict]:
        """Return evidence records: {source, title, url, snippet, kind}"""
        raise NotImplementedError

    def health(self) -> tuple[bool, str]:
        return True, "ok"


_REGISTRY: dict[str, BaseConnector] = {}


def register(connector: BaseConnector) -> None:
    _REGISTRY[connector.name] = connector


def get_connector(name: str) -> BaseConnector | None:
    """The registered client for a data source, or None when no client is connected yet."""
    return _REGISTRY.get(name)
