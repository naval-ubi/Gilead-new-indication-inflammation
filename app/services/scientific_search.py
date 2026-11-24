"""Client for the AI Scientific Search service (AWS API Gateway) and conversion of its responses into the app's
result format.

Service endpoints (base URL from AI_SEARCH_BASE_URL):
    GET  /health                       {"status": "healthy", "service": ..., "message": ...}
    POST /scientific-search/rephrase   {"query"} -> {"status", "original_query", "suggested_query", "message"}
    POST /scientific-search/query      {"query"} -> {"status", "query", "answer" (Markdown, cites [S1]),
                                                    "retrieved_chunk_count", "source_count", "sources": [...]}
Errors come back as {"status": "error", "message": "..."} (e.g. HTTP 400 "Query is required.").

When AI_SEARCH_BASE_URL is empty, runs fail with a clear message and AI Rephrase returns 503; nothing is made up.
Sample data is created inside `offline()`: its runs skip the service and simply have no AI text.
"""
from __future__ import annotations

import contextlib
import contextvars
import re
import time

import httpx

from app.core.config import settings

SOURCE_NAMES = {"pubmed": "PubMed", "pmc": "PubMed Central (PMC)", "clinicaltrials": "ClinicalTrials.gov",
                "clinicaltrials.gov": "ClinicalTrials.gov", "europepmc": "Europe PMC", "opentargets": "Open Targets Platform"}
_offline = contextvars.ContextVar("ai_search_offline", default=False)


class ScientificSearchError(RuntimeError):
    """The service could not answer. The message is safe to show to users."""


def configured() -> bool:
    return bool(settings.ai_search_base_url)


def is_offline() -> bool:
    """Inside offline(): sample data, which has no AI text and never calls the service."""
    return _offline.get()


@contextlib.contextmanager
def offline():
    """Runs created inside this block skip the service and have no AI text (used for sample data only)."""
    token = _offline.set(True)
    try:
        yield
    finally:
        _offline.reset(token)


def _client() -> httpx.Client:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings.ai_search_api_key:  # only if the service is later protected with an API Gateway key
        headers["x-api-key"] = settings.ai_search_api_key
    return httpx.Client(base_url=settings.ai_search_base_url.rstrip("/"), headers=headers,
                        timeout=httpx.Timeout(settings.ai_search_timeout_seconds, connect=10))


def _post(path: str, payload: dict) -> dict:
    """POST with one retry for gateway hiccups (502/503/504, connection errors). Timeouts are not retried: the
    service may still be working, and a second request would double the cost."""
    last = None
    for attempt in range(2):
        try:
            with _client() as c:
                r = c.post(path, json=payload)
        except httpx.TimeoutException:
            raise ScientificSearchError(
                f"The scientific search service did not answer within {settings.ai_search_timeout_seconds:g} seconds")
        except httpx.TransportError as e:
            last = ScientificSearchError(f"Could not reach the scientific search service ({type(e).__name__})")
        else:
            if r.status_code in (502, 503, 504):
                last = ScientificSearchError(f"The scientific search service is temporarily unavailable (HTTP {r.status_code})")
            else:
                try:
                    body = r.json()
                except ValueError:
                    raise ScientificSearchError(f"The scientific search service returned an unreadable response (HTTP {r.status_code})")
                if r.status_code >= 400 or body.get("status") == "error":
                    raise ScientificSearchError(body.get("message") or f"The scientific search service returned HTTP {r.status_code}")
                return body
        if attempt == 0:
            time.sleep(1.5)
    raise last


def health() -> dict:
    t0 = time.monotonic()
    try:
        with _client() as c:
            r = c.get("/health")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        ok = r.status_code == 200 and body.get("status") == "healthy"
        return {"configured": True, "ok": ok, "status_code": r.status_code, "detail": body.get("message"),
                "latency_ms": int((time.monotonic() - t0) * 1000)}
    except httpx.HTTPError as e:
        return {"configured": True, "ok": False, "detail": f"{type(e).__name__}", "latency_ms": int((time.monotonic() - t0) * 1000)}


def rephrase(query: str) -> str:
    body = _post("/scientific-search/rephrase", {"query": query})
    out = (body.get("suggested_query") or "").strip()
    if not out:
        raise ScientificSearchError("The scientific search service returned no suggestion")
    return out


def search(query: str) -> dict:
    body = _post("/scientific-search/query", {"query": query})
    if not (body.get("answer") or "").strip():
        raise ScientificSearchError("The scientific search service returned an empty answer")
    return body


# ------------------------------------------------------------------ response -> app result
def source_name(raw: str | None) -> str:
    key = (raw or "").strip().lower()
    return SOURCE_NAMES.get(key) or (raw or "Unknown source")


def _numbers(sources: list[dict]) -> dict[str, int]:
    """S-ids in the order the service lists them -> 1, 2, 3 ... (the numbers shown in the reference list)."""
    return {s.get("source_id") or f"S{i}": i for i, s in enumerate(sources, 1)}


def renumber_citations(answer: str, numbers: dict[str, int]) -> str:
    """[S1] -> [1], [S1, S3] -> [1, 3]; unknown ids are left as they are."""
    def repl(m):
        ids = [x.strip() for x in m.group(1).split(",")]
        if not all(i in numbers for i in ids):
            return m.group(0)
        return "[" + ", ".join(str(numbers[i]) for i in ids) + "]"
    return re.sub(r"\[(S\d+(?:\s*,\s*S\d+)*)\]", repl, answer)


def _plain(text: str) -> str:
    text = re.sub(r"\[(?:S?\d+(?:\s*,\s*S?\d+)*)\]", "", text)
    return re.sub(r"[*_`#>]+", "", text).strip()


def key_findings(answer: str, k: int = 4) -> list[str]:
    """Short findings for the View Details drawer: list items first, otherwise the first sentence of each paragraph."""
    bullets = [_plain(m) for m in re.findall(r"^\s*(?:[-*]|\d+\.)\s+(.+)$", answer, re.M)]
    if not bullets:
        for para in re.split(r"\n\s*\n", answer):
            para = para.strip()
            if not para or para.startswith("#") or para.startswith("|"):
                continue
            first = re.split(r"(?<=[.!?])\s+", _plain(para))[0]
            bullets.append(first)
    return [b if len(b) <= 220 else b[:217] + "..." for b in bullets if len(b) > 20][:k]


def to_records(body: dict) -> list[dict]:
    """Service sources as evidence records (the shape the loader's emerging findings and references use)."""
    out = []
    for i, s in enumerate(body.get("sources") or [], 1):
        evidence = re.sub(r"\s+", " ", s.get("evidence") or "").strip()
        out.append({"source": source_name(s.get("source")), "kind": s.get("document_type") or "publication",
                    "title": s.get("title") or f"Source {i}", "url": s.get("source_url"),
                    "snippet": evidence[:400], "source_id": s.get("source_id") or f"S{i}",
                    "record_id": s.get("source_record_id"), "doi": s.get("doi"), "journal": s.get("journal"),
                    "publication_date": s.get("publication_date")})
    return out


def to_result_parts(body: dict) -> dict:
    """answer (citations renumbered to the reference list), references, top_sources, key_findings, counts."""
    sources = body.get("sources") or []
    numbers = _numbers(sources)
    answer = renumber_citations(body.get("answer") or "", numbers)
    records = to_records(body)
    references = [{"number": numbers[r["source_id"]], "title": r["title"], "url": r["url"], "source": r["source"],
                   "note": r["snippet"][:240], "doi": r["doi"], "journal": r["journal"],
                   "publication_date": r["publication_date"], "record_id": r["record_id"]} for r in records]
    seen, top = set(), []
    for r in records:
        if r["source"] not in seen:
            seen.add(r["source"])
            top.append({"name": r["source"], "url": r["url"]})
    return {"answer": answer, "references": references, "top_sources": top, "records": records,
            "key_findings": key_findings(body.get("answer") or ""),
            "retrieved_chunk_count": body.get("retrieved_chunk_count"), "source_count": body.get("source_count", len(sources))}
