"""Integration seams. Everything external (LLM, PubMed, Citeline, IQVIA ...) sits behind these two
interfaces. The stubs below make the API run end-to-end offline; replace them per connector.

  * LLMClient       -> Anthropic / Azure OpenAI / internal gateway
  * BaseConnector   -> one subclass per data source, registered by the name stored in `connectors.name`
"""
from __future__ import annotations

import re
from typing import Callable, Protocol

CATEGORIES = ["Scientific & Biological", "Clinical", "Regulatory & Safety",
              "Commercial, Epidemiology & RWD", "IP & Patents"]


# ---------------------------------------------------------------- LLM
class LLMClient(Protocol):
    def complete(self, system: str, prompt: str, max_tokens: int = 1000) -> str: ...


class StubLLM:
    def complete(self, system: str, prompt: str, max_tokens: int = 1000) -> str:
        return f"[stub-llm] {prompt[:200]}"


_llm: LLMClient = StubLLM()


def get_llm() -> LLMClient:
    return _llm


def set_llm(client: LLMClient) -> None:  # call from main.py once a real client is configured
    global _llm
    _llm = client


def words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def improve_prompt(q: str) -> str:
    """Legacy 'AI Suggestion' (previous UI). Kept for API compatibility; the new UI uses rephrase_prompt()."""
    q = q.strip()
    if len(q) > 70 and re.search(r"for\s+\w+|\bin\b", q):
        return q
    return re.sub(r"[?.]*$", "", q) + " - compare SLE, RA and AD in the US over the next 5 years, and cite evidence quality for each finding."


_STOP = set("a an and are as at based be been by can could do does each for from has have how in into is it its most of on or "
            "that the their them these this those to was what when where which who why will with would about across any all i "
            "me my we our you your please tell give show explain there than then also just like likely available published "
            "particularly major".split())
_GENERIC = {"irak4", "sle", "ra", "ad", "inhibition", "inhibitor", "inhibit", "disease", "three", "lupu", "arthriti",
            "dermatiti", "rheumatoid", "atopic", "systemic", "erythematosu", "degrader", "therapy", "clinical", "trial"}


def tokens(text: str) -> set[str]:
    """Content words, crudely stemmed, without words every question in this domain shares."""
    out = set()
    for w in re.split(r"[\s/]+", re.sub(r"[^a-z0-9\s/-]", " ", (text or "").lower())):
        w = re.sub(r"ies$", "y", w)
        w = re.sub(r"s$", "", w)
        if len(w) > 1 and w not in _STOP and w not in _GENERIC:
            out.add(w)
    return out


def nearest_question(text: str, candidates: list[str], exclude: list[str] | None = None) -> tuple[str | None, float]:
    """Best catalogue match by token overlap. Returns (question, score) where score = overlap + overlap ratio."""
    t = tokens(text)
    excl = {e.strip().lower() for e in exclude or []}
    best, best_score = None, 0.0
    for c in candidates:
        if c.strip().lower() in excl:
            continue
        n = len(t & tokens(c))
        score = n + (n / len(t) if t else 0)
        if n and score > best_score:
            best, best_score = c, score
    return best, best_score


def rephrase_prompt(text: str, module: str = "ai") -> str:
    """'AI Rephrase' button: rewrite the user's question in more detail.
    Stub: snap to the closest curated question for the module, otherwise append an evidence-structuring clause.
    Replace with an LLM call driven by a Prompts Library template."""
    from app.services.catalog import STARTER_QUESTIONS  # local import avoids a cycle at module load
    q = text.strip()
    best, _ = nearest_question(q, STARTER_QUESTIONS.get(module, STARTER_QUESTIONS["ai"]))
    if best and best.strip().lower() != q.lower():
        return best
    return (re.sub(r"[?.]*$", "", q) + "? Summarise the strongest supporting evidence for each indication, separate "
            "established findings from emerging or preclinical data, and note key gaps or uncertainties.")


def in_scope(question: str, module: str, context: dict | None = None) -> bool:
    """Guardrail: does this module answer this question? Stub = keyword check incl. the user's research context.
    Replace with an LLM classifier. Modules without a scope definition accept everything."""
    from app.services.catalog import SCOPE_PATTERN
    pat = SCOPE_PATTERN.get(module)
    if not pat:
        return True
    ctx = context or {}
    extra = [ctx.get("target", "")] + list(ctx.get("indications") or [])
    extra_pat = "|".join(re.escape(x.lower()) for x in extra if x)
    q = (question or "").lower()
    return bool(re.search(pat, q) or (extra_pat and re.search(extra_pat, q)))


def suggest_follow_ups(question: str, module: str, asked: list[str] | None = None, n: int = 3) -> list[str]:
    """'Follow-up questions' under each answer: related curated questions the user has not asked yet in this thread."""
    from app.services.catalog import STARTER_QUESTIONS
    pool = STARTER_QUESTIONS.get(module, [])
    asked_l = {a.strip().lower() for a in (asked or [])} | {question.strip().lower()}
    t = tokens(question)
    ranked = sorted((c for c in pool if c.strip().lower() not in asked_l), key=lambda c: -len(t & tokens(c)))
    return ranked[:n]


# ---------------------------------------------------------------- Connectors
class BaseConnector:
    name = "base"

    def search(self, question: str, context: dict) -> list[dict]:
        """Return evidence records: {source,title,url,snippet,retrieved_at,kind}"""
        raise NotImplementedError

    def health(self) -> tuple[bool, str]:
        return True, "ok"


class StubConnector(BaseConnector):
    def __init__(self, name: str):
        self.name = name

    def search(self, question: str, context: dict) -> list[dict]:
        t = context.get("target", "the target")
        return [{"source": self.name, "kind": "evidence", "title": f"{t}: evidence record from {self.name}",
                 "url": None, "snippet": "Illustrative record - replace StubConnector with a real client."}]


_REGISTRY: dict[str, BaseConnector] = {}


def register(connector: BaseConnector) -> None:
    _REGISTRY[connector.name] = connector


def get_connector(name: str) -> BaseConnector:
    return _REGISTRY.get(name) or StubConnector(name)


def fan_out(question: str, context: dict, source_names: list[str],
            on_progress: Callable[[str, str], None] | None = None) -> tuple[list[dict], list[dict]]:
    """Query enabled sources; one failing source must not fail the run. Returns (records, errors).
    on_progress(source, status) is called with 'active' | 'done' | 'failed' so the loader can show
    Searching / Searched per source."""
    records, errors = [], []
    for n in source_names:
        if on_progress:
            on_progress(n, "active")
        try:
            records += get_connector(n).search(question, context)
            if on_progress:
                on_progress(n, "done")
        except Exception as e:  # noqa: BLE001 - isolate connector failures
            errors.append({"source": n, "error": str(e)})
            if on_progress:
                on_progress(n, "failed")
    return records, errors
