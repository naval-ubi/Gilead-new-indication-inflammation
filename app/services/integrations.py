"""Integration seams. Everything external (LLM, PubMed, Citeline, IQVIA ...) sits behind these two
interfaces. The stubs below make the API run end-to-end offline; replace them per connector.

  * LLMClient       -> Anthropic / Azure OpenAI / internal gateway
  * BaseConnector   -> one subclass per data source, registered by the name stored in `connectors.name`
"""
from __future__ import annotations

import re
from typing import Protocol

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
    """'AI Suggestion' shown after >= 2 words. Replace with an LLM call using a prompt from the Prompts Library."""
    q = q.strip()
    if len(q) > 70 and re.search(r"for\s+\w+|\bin\b", q):
        return q
    return re.sub(r"[?.]*$", "", q) + " - compare SLE, RA and AD in the US over the next 5 years, and cite evidence quality for each finding."


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


def fan_out(question: str, context: dict, source_names: list[str]) -> tuple[list[dict], list[dict]]:
    """Query enabled sources; one failing source must not fail the run. Returns (records, errors)."""
    records, errors = [], []
    for n in source_names:
        try:
            records += get_connector(n).search(question, context)
        except Exception as e:  # noqa: BLE001 - isolate connector failures
            errors.append({"source": n, "error": str(e)})
    return records, errors
