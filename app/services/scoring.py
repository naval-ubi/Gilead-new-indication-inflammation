"""Prioritization scoring engine. Pure functions: no DB, no FastAPI, so it is trivially unit-testable.

Mirrors the prototype's scCalc / scRank / sensHtml:
  weighted = sum(score_c * weight_c / 100)            (scores 1-5, weights in percent)
  norm     = weighted / 5 * 100                        (0-100 priority score)
A result is only produced when weights total 100 and no score is missing.
"""
from __future__ import annotations

from dataclasses import dataclass

SCALE_LABELS = {1: "Very low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very high"}
MAX_SCORE = 5


@dataclass
class IndicationResult:
    indication: str
    scores: dict[str, int | None]
    raw: int
    weighted: float
    norm: float | None
    missing: int
    rank: int | None = None
    priority: str | None = None


def priority_band(norm: float | None) -> str | None:
    if norm is None:
        return None
    return "High" if norm >= 80 else "Medium" if norm >= 60 else "Low"


def effective_scores(ai_scores: dict, overrides: dict, indication: str, keys: list[str]) -> dict[str, int | None]:
    """Human override (with justification) wins over the AI-recommended score."""
    ai = ai_scores.get(indication, {})
    ov = overrides.get(indication, {})
    out: dict[str, int | None] = {}
    for k in keys:
        out[k] = ov[k]["score"] if k in ov else ai.get(k)
    return out


def validate_weights(weights: dict[str, float], keys: list[str]) -> list[str]:
    errs = []
    total = sum(weights.get(k, 0) for k in keys)
    if abs(total - 100) > 0.01:
        errs.append(f"Weights must total 100% (currently {total:g}%)")
    if any(weights.get(k, 0) < 0 for k in keys):
        errs.append("Weights cannot be negative")
    return errs


def calculate(indications: list[str], keys: list[str], weights: dict[str, float],
              ai_scores: dict, overrides: dict | None = None) -> list[IndicationResult]:
    overrides = overrides or {}
    total_w = sum(weights.get(k, 0) for k in keys)
    results: list[IndicationResult] = []
    for ind in indications:
        sc = effective_scores(ai_scores, overrides, ind, keys)
        missing = sum(1 for v in sc.values() if v is None)
        weighted = sum(v * weights.get(k, 0) / 100 for k, v in sc.items() if v is not None)
        raw = sum(v for v in sc.values() if v is not None)
        norm = weighted / MAX_SCORE * 100 if abs(total_w - 100) < 0.01 and missing == 0 else None
        results.append(IndicationResult(ind, sc, raw, round(weighted, 4), None if norm is None else round(norm, 1), missing))
    ranked = sorted([r for r in results if r.norm is not None], key=lambda r: -r.norm)
    for pos, r in enumerate(ranked, 1):
        r.rank = pos
    for r in results:
        r.priority = priority_band(r.norm)
    return results


def ranking(results: list[IndicationResult]) -> dict[str, int]:
    return {r.indication: r.rank for r in results if r.rank is not None}


def sensitivity(indications: list[str], keys: list[str], weights: dict[str, float],
                ai_scores: dict, overrides: dict | None = None) -> dict:
    """Rank of each indication if ONE criterion's weight is doubled and all weights are rescaled to 100."""
    base = ranking(calculate(indications, keys, weights, ai_scores, overrides))
    rows = []
    for k in keys:
        w = {kk: (v * 2 if kk == k else v) for kk, v in weights.items() if kk in keys}
        tot = sum(w.values()) or 1
        w2 = {kk: v / tot * 100 for kk, v in w.items()}
        r = ranking(calculate(indications, keys, w2, ai_scores, overrides))
        rows.append({
            "criterion": k,
            "ranks": {i: {"rank": r.get(i), "delta": (base[i] - r[i]) if i in r and i in base else None}
                      for i in indications},  # delta > 0 means the indication moved UP
        })
    return {"base": base, "scenarios": rows}
