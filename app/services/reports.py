"""'Export Full Report (PDF)' for every research module: a render-ready report document built from a completed
run's saved result. The frontend renders the PDF from it, with one renderer for all modules.

Every report has the same shape:
  module, report_id, title, question, generated_at, meta [[label, value]], notice, at_a_glance [{value, label, icon}],
  key_takeaways [str], contents [str], sections [{index, title, text, bullets, references, table, turn}], appendices
  [{title, items}], footer, filename, turns
section.text is Markdown with [n] citations matching section.references[].number (numbering is per section);
section.table is {headers, rows} or null.

A conversation report (?thread=true) covers every answered turn of the conversation, oldest first: turns lists
them [{number, run_id, question, generated_at}] and each section's turn says which turn it belongs to. In a
single-run report turns and section.turn are null.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.db import models as m
from app.services import catalog
from app.services.scientific_search import key_findings

FOOTER = "Gilead Confidential · For internal use only"
NO_AI_TEXT = "This report has no AI-written text (sample data)."
FILTER_LABELS = [("target", "Target"), ("indication", "Indication"), ("modality", "Modality"), ("stage", "Development stage")]


class NotExportable(Exception):
    pass


def build_report(run: m.Run) -> dict:
    if run.status != "Completed":
        raise NotExportable(f"Run is {run.status}; export it once it has completed")
    res = {k: v for k, v in (run.result or {}).items() if not k.startswith("_")}
    if res.get("relevant") is False:
        raise NotExportable("This question was outside the module's scope, so there is no answer to export")
    builder = {"ai": ai_report, "pipeline": pipeline_report, "cioa": cioa_report}[run.module]
    report = {"module": run.module, **builder(run, res), "turns": None}
    report["sections"] = [{**sec, "turn": None} for sec in report["sections"]]
    return report


def _answered(run: m.Run) -> bool:
    return run.status == "Completed" and (run.result or {}).get("relevant") is not False


def build_thread_report(db, run: m.Run) -> dict:
    """The whole conversation that `run` belongs to as one report: every answered turn, oldest first. Header,
    meta and at-a-glance figures come from the latest answered turn; key takeaways from all turns."""
    turns = db.scalars(select(m.Run).where(m.Run.thread_id == (run.thread_id or run.id), m.Run.user_id == run.user_id,
                                           m.Run.deleted.is_(False)).order_by(m.Run.id)).all()
    answered = [t for t in turns if _answered(t)]
    if not answered:
        raise NotExportable("This conversation has no completed answer to export yet")
    parts = [(n, t, build_report(t)) for n, t in enumerate(answered, 1)]
    _, last_run, last = parts[-1]

    sections, takeaways, follow_ups = [], [], None
    for n, t, rep in parts:
        turn = {"number": n, "run_id": t.id, "question": t.question}
        for sec in rep["sections"]:
            if sec["title"] == "Suggested follow-up questions":
                follow_ups = sec if t is last_run else follow_ups     # only the latest turn's suggestions
                continue
            title = (f"{n}. {t.question or rep['title']}" if sec["title"] == "Answer"
                     else f"{n}. {sec['title']}")
            sections.append({**sec, "title": title, "turn": turn})
        takeaways += [k for k in rep["key_takeaways"] if k not in takeaways]
    if follow_ups:
        sections.append({**follow_ups, "turn": None})
    for i, sec in enumerate(sections):
        sec["index"] = i

    skipped = len(turns) - len(answered)
    appendices = [dict(a) for a in last["appendices"]]
    if skipped:
        appendices.append({"title": "Appendix · Turns not included",
                           "items": [f"{skipped} turn(s) of this conversation have no answer (still running, failed, "
                                     "cancelled or outside the module's scope) and are not included."]})
    prefix = last["report_id"].split("-")[0]
    thread_id = last_run.thread_id or last_run.id
    stem, ext = last["filename"].rsplit(".", 1)
    return {
        **last,
        "report_id": f"{prefix}-T{thread_id:06d}",
        "title": f"{last['title']} · conversation",
        "question": answered[0].question,
        "meta": [row for row in last["meta"] if row[0] != "Report ID"]
                + [["Conversation turns", str(len(answered))], ["Report ID", f"{prefix}-T{thread_id:06d}"]],
        "notice": next((r["notice"] for _, _, r in parts if r["notice"]), None),
        "key_takeaways": takeaways[:10],
        "contents": [sec["title"] for sec in sections],
        "sections": sections,
        "appendices": appendices,
        "filename": f"{stem}_conversation.{ext}",
        "turns": [{"number": n, "run_id": t.id, "question": t.question, "generated_at": t.updated_at} for n, t, _ in parts],
    }


def _section(i: int, title: str, *, text: str | None = None, bullets: list | None = None,
             references: list | None = None, table: dict | None = None) -> dict:
    return {"index": i, "title": title, "text": text, "bullets": bullets or [], "references": references or [],
            "table": table}


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")[:limit].strip("-") or "Report"


def _sources(res: dict) -> str:
    rs = res.get("research_summary", {})
    return ", ".join(rs.get("sources_searched") or res.get("sources_used") or [])


def _method(res: dict, extra: list[str]) -> dict:
    return {"title": "Appendix · Method and limitations", "items": [
        f"Sources: {_sources(res) or 'none recorded'}.",
        *extra,
        "AI-written text comes from the AI Scientific Search service and cites the sources it used. "
        "Check the cited sources before making decisions.",
    ]}


# ------------------------------------------------------------------ AI Scientific Search
def ai_report(run: m.Run, res: dict) -> dict:
    ctx = (run.params or {}).get("context", {})
    rs = res.get("research_summary", {})
    refs = res.get("references", [])
    rid, generated = f"AI-{run.id:06d}", run.updated_at
    sections = [_section(0, "Answer", text=res.get("answer"), references=refs)]
    if res.get("follow_ups"):
        sections.append(_section(1, "Suggested follow-up questions", bullets=res["follow_ups"]))
    return {
        "report_id": rid,
        "title": "Scientific Search report",
        "question": run.question,
        "generated_at": generated,
        "meta": [["Molecular target", ctx.get("target", "")], ["Indications", ", ".join(ctx.get("indications") or [])],
                 ["Research mode", run.mode or "Standard"], ["Sources searched", _sources(res)],
                 ["Generated", "AI Assistant"], ["Report ID", rid]],
        "notice": None if res.get("provider") else NO_AI_TEXT,
        "at_a_glance": [
            {"value": len(refs), "label": "References cited", "icon": "book"},
            {"value": len({r.get("source") for r in refs if r.get("source")}), "label": "Source databases", "icon": "db"},
            {"value": res.get("retrieved_passages") or 0, "label": "Passages reviewed", "icon": "doc"},
        ],
        "key_takeaways": res.get("key_findings", []),
        "contents": [s["title"] for s in sections],
        "sections": sections,
        "appendices": [_method(res, [f"Research stages: {', '.join(rs.get('stages') or [])}."])],
        "footer": FOOTER,
        "filename": f"Scientific-Search_{_slug(run.title)}_{generated:%Y-%m-%d}.pdf",
    }


# ------------------------------------------------------------------ Drug Pipeline Explorer
def pipeline_report(run: m.Run, res: dict) -> dict:
    f = res.get("filters") or {}
    assets = res.get("assets", [])
    rid, generated = f"DP-{run.id:06d}", run.updated_at
    summary = res.get("summary")
    by_stage = [[row["stage"], *row["counts"].values(), sum(row["counts"].values())]
                for row in res.get("stage_counts", []) if sum(row["counts"].values())]
    inds = list((res.get("stage_counts") or [{}])[0].get("counts", {}).keys())
    sections = [
        _section(0, "AI summary", text=summary, references=res.get("references", [])),
        _section(1, "Programs by development stage",
                 table={"headers": ["Stage", *inds, "Total"], "rows": by_stage} if by_stage else None),
        _section(2, "Pipeline programs", table={
            "headers": ["Asset", "Sponsor", "Target", "Modality", "Indication", "Stage", "Status", "Geography"],
            "rows": [[a.get("name"), a.get("sponsor"), a.get("target"), a.get("modality"), a.get("indication"),
                      a.get("stage"), a.get("activity"), a.get("geography")] for a in assets]} if assets else None),
    ]
    mf = res.get("more_filters_count") or 0
    return {
        "report_id": rid,
        "title": "Drug Pipeline report",
        "question": run.question,
        "generated_at": generated,
        "meta": [*[[label, f.get(key) or "All"] for key, label in FILTER_LABELS],
                 ["More filters", f"{mf} applied" if mf else "None"], ["Programs", str(len(assets))],
                 ["Generated", "AI Assistant"], ["Report ID", rid]],
        "notice": None if res.get("provider") else NO_AI_TEXT,
        "at_a_glance": [{"value": t["value"], "label": t["label"], "icon": t["icon"]} for t in res.get("kpi_tiles", [])],
        "key_takeaways": key_findings(summary) if summary else [],
        "contents": [s["title"] for s in sections],
        "sections": sections,
        "appendices": [_method(res, ["Program data come from the pipeline database at the time of the run; "
                                     "statuses change frequently, re-run before decisions."])],
        "footer": FOOTER,
        "filename": f"Drug-Pipeline_{_slug(f.get('indication') or 'All-indications')}_{generated:%Y-%m-%d}.pdf",
    }


# ------------------------------------------------------------------ Competitive Insights
def cioa_report(run: m.Run, res: dict) -> dict:
    wf = catalog.resolve_workflow(run.workflow)
    rs = res.get("research_summary", {})
    ctx = res.get("context", {})
    mf = catalog.more_filters_count((run.params or {}).get("more_filters"))
    generated = run.updated_at
    return {
        "report_id": f"CI-{run.id:06d}",
        "title": f"{wf['name']} report",
        "question": run.question,
        "generated_at": generated,
        "meta": [["Molecular target", ctx.get("molecular_target", "")], ["Indications", ", ".join(ctx.get("indications") or [])],
                 ["Therapeutic area", ctx.get("therapeutic_area", "")], ["Workflow", wf["name"]],
                 ["More filters", f"{mf} applied" if mf else "None"],
                 ["Sources searched", _sources(res)],
                 ["Generated", "AI Assistant"], ["Report ID", f"CI-{run.id:06d}"]],
        "notice": None if res.get("provider") else NO_AI_TEXT,
        "at_a_glance": res.get("stats", []),
        "key_takeaways": res.get("key_takeaways", []),
        "contents": [s["title"] for s in res.get("sections", [])],
        "sections": res.get("sections", []),
        "appendices": [
            {"title": "Appendix A · Workflow subcategories", "items": wf["subcategories"]},
            {"title": "Appendix B · Method and limitations",
             "items": [f"Sources: {', '.join(rs.get('sources_searched') or [])}.",
                       "Content is assembled by the AI Assistant from enabled data connectors and the selected workflow template.",
                       "Program statuses change frequently; re-run the workflow before decisions."]},
        ],
        "footer": FOOTER,
        "filename": f"Competitive-Insights_{wf['short'].replace(' ', '-')}_{generated:%Y-%m-%d}.pdf",
    }
