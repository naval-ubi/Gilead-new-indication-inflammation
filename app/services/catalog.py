"""Static reference data the UI's dropdowns, workflow picker, help center and loaders are built from.

Everything here is served by GET endpoints so the SPA stops hard-coding it. In production most of this
moves to admin-managed tables (AdminRecord); the shapes returned by the API stay the same.
"""
from __future__ import annotations

import re

# ------------------------------------------------------------------ module display names
MODULE_NAMES = {"ai": "AI Scientific Search", "pipeline": "Drug Pipeline Explorer", "cioa": "Competitive Insights",
                "scoring": "Prioritization Scoring"}

# ------------------------------------------------------------------ roles & permissions (seed data for the tables)
PERMISSIONS = {
    "view": "Open screens and results", "create": "Create searches, assessments and workspace items",
    "edit": "Edit, override scores, approve reports", "execute": "Start research runs",
    "publish": "Publish workflows, templates and scoring models", "share": "Share items with colleagues and groups",
    "administer": "Manage users, groups, connectors and configuration",
}
ROLES = {  # name -> (description, permission codes)
    "Admin": ("Full access including administration", ["view", "create", "edit", "execute", "publish", "share", "administer"]),
    "Analyst": ("Runs research and builds assessments", ["view", "create", "edit", "execute", "share"]),
    "Reviewer": ("Reviews and comments on shared work", ["view", "edit", "share"]),
    "Viewer": ("Read-only access", ["view"]),
}
DEFAULT_ROLE = "Analyst"

# ------------------------------------------------------------------ indications / therapeutic areas / targets
CORE_INDICATIONS = ["SLE", "RA", "AD", "Hidradenitis suppurativa"]
ADDITIONAL_INDICATIONS = ["Psoriasis", "Psoriatic arthritis", "Sjögren’s disease", "Systemic sclerosis", "Dermatomyositis",
                          "Crohn’s disease", "Ulcerative colitis", "Asthma"]
# kept for backwards compatibility with contexts saved by the previous UI
LEGACY_INDICATIONS = ["Lupus Nephritis", "Psoriasis", "Hidradenitis Suppurativa", "Ulcerative Colitis"]
ALL_INDICATIONS = list(dict.fromkeys(CORE_INDICATIONS + ADDITIONAL_INDICATIONS + LEGACY_INDICATIONS))
INDICATION_FULL_NAMES = {"SLE": "Systemic Lupus Erythematosus", "RA": "Rheumatoid Arthritis", "AD": "Atopic Dermatitis"}
GEOGRAPHIES = ["United States", "EU5", "Japan", "China", "Global"]

THERAPEUTIC_AREAS = {
    "Autoimmune and inflammatory disease": CORE_INDICATIONS + ADDITIONAL_INDICATIONS,
    "Rheumatology": ["RA", "SLE", "Psoriatic arthritis", "Sjögren’s disease", "Systemic sclerosis", "Dermatomyositis"],
    "Dermatology": ["AD", "Psoriasis", "Hidradenitis suppurativa", "Dermatomyositis"],
    "Allergy/immunology": ["AD", "Asthma"],
    "Gastroenterology": ["Crohn’s disease", "Ulcerative colitis"],
    "Pulmonology": ["Asthma"],
}
DEFAULT_THERAPEUTIC_AREA = "Autoimmune and inflammatory disease"
MOLECULAR_TARGET_GROUPS = [
    {"group": "Direct IRAK4", "key": "direct",
     "options": ["IRAK4 inhibitor", "IRAK4 degrader", "IRAK1/4 dual inhibitor", "IRAK1", "IRAK2"]},
    {"group": "Related pathway", "key": "related",
     "options": ["MYD88", "TLR7/8/9", "BTK", "SYK", "JAK1", "TYK2", "NLRP3", "IRF5"]},
]

# ------------------------------------------------------------------ Drug Pipeline Explorer filters
PIPELINE_FILTER_GROUPS = {
    "target": [
        {"group": "Direct IRAK-family targets",
         "options": ["IRAK4 inhibitor", "IRAK4 degrader", "IRAK4/IRAK1 dual inhibitor", "IRAK1", "IRAK2"]},
        {"group": "Related pathway targets",
         "options": ["IL-1R pathway antibody", "MYD88", "TLR7", "TLR8", "TLR9", "BTK", "SYK", "JAK1", "TYK2", "NLRP3", "IRF5"]},
    ],
    "indication": [
        {"group": "Core indications", "options": CORE_INDICATIONS},
        {"group": "Additional indications", "options": ADDITIONAL_INDICATIONS},
    ],
    "modality": [
        {"group": "Modality", "options": ["Small molecule", "Targeted protein degrader", "Monoclonal antibody", "Fusion protein",
                                          "Peptide/protein therapeutic", "Antisense oligonucleotide", "siRNA", "Gene therapy",
                                          "Cell therapy"]},
    ],
    "stage": [
        {"group": "Development stage", "options": ["Discovery", "Lead optimization", "Preclinical", "IND-enabling", "Phase 1",
                                                   "Phase 1/2", "Phase 2", "Phase 2/3", "Phase 3", "Regulatory review",
                                                   "Approved", "Phase 4"]},
        {"group": "Development status", "options": ["On hold", "Discontinued"]},
    ],
}
OPTION_HINTS = {"Targeted protein degrader": "PROTAC or molecular glue"}

# "More filters" (ClinicalTrials.gov-style) dialog on the pipeline ask box
MORE_FILTERS = [
    {"key": "status", "title": "Study Status", "type": "radio", "default": "all",
     "options": [["all", "All studies"], ["rec", "Recruiting and not yet recruiting studies"]]},
    {"key": "sex", "title": "Sex", "group": "Eligibility Criteria", "type": "radio", "default": "all",
     "options": [["all", "All"], ["f", "Female"], ["m", "Male"]]},
    {"key": "age", "title": "Age", "group": "Eligibility Criteria", "type": "checkbox",
     "options": [["child", "Child (birth - 17)"], ["adult", "Adult (18 - 64)"], ["older", "Older adult (65+)"]]},
    {"key": "phase", "title": "Study Phase", "type": "checkbox",
     "options": [["ep1", "Early Phase 1"], ["p1", "Phase 1"], ["p2", "Phase 2"], ["p3", "Phase 3"], ["p4", "Phase 4"],
                 ["na", "Not applicable"]]},
    {"key": "type", "title": "Study Type", "type": "checkbox",
     "options": [["int", "Interventional"], ["obs", "Observational"], ["reg", "Patient registries"], ["ea", "Expanded access"],
                 ["ind", "Individual patients"], ["mid", "Intermediate-size population"], ["tind", "Treatment IND/Protocol"]]},
    {"key": "results", "title": "Study Results", "type": "checkbox",
     "options": [["with", "With results"], ["without", "Without results"]]},
    {"key": "docs", "title": "Study Documents", "type": "checkbox",
     "options": [["prot", "Study protocols"], ["sap", "Statistical analysis plans (SAPs)"], ["icf", "Informed consent forms (ICFs)"]]},
    {"key": "funder", "title": "Funder Type", "type": "checkbox",
     "options": [["nih", "NIH"], ["fed", "Other U.S. federal agency"], ["ind", "Industry"],
                 ["oth", "All others (individuals, universities, organizations)"]]},
]


def more_filters_count(mf: dict | None) -> int:
    """Number of 'More filters' groups that differ from their default (badge on the filter button)."""
    if not mf:
        return 0
    n = 0
    for f in MORE_FILTERS:
        v = mf.get(f["key"])
        n += bool(v) and v != f.get("default") if f["type"] == "radio" else bool(v)
    return n


# ------------------------------------------------------------------ default sources per module (connector names)
DEFAULT_SOURCES = {
    "ai": ["PubMed", "ClinicalTrials.gov", "Open Targets Platform", "Citeline Trialtrove"],
    "pipeline": ["Citeline Pharmaprojects", "ClinicalTrials.gov", "EMA Medicines"],
    "cioa": ["Citeline Pharmaprojects", "ClinicalTrials.gov", "EMA Medicines", "IQVIA MIDAS", "USPTO Patent Public Search"],
}

# ------------------------------------------------------------------ Competitive Insights workflows
WORKFLOWS = [
    {"key": "competitive", "name": "Competitive Intelligence & Opportunity Assessment", "short": "Competitive Opportunity",
     "icon": "wf_ci", "output": None,
     "description": "Map competitors, pipeline, IP and market dynamics, then rank threats and commercial opportunities.",
     "keywords": r"competit|opportunit|threat|pipeline|patent|\bip\b|commercial",
     "sources": ["Citeline", "ClinicalTrials.gov", "EMA", "IQVIA", "USPTO"],
     "subcategories": ["Define Market & Therapeutic Focus", "Discover Competing Assets & Therapies",
                       "Assess Clinical Pipeline & IP Landscape", "Evaluate Market Dynamics & Differentiation",
                       "Consolidate Competitive Insights", "Segment & Rank Competitive Threats",
                       "Generate Strategic Insights & Market Alerts", "Identify Commercial Opportunities & Strategic Options",
                       "Validate Evidence & Identify Intelligence Gaps", "Generate Competitive Opportunity Report"],
     "sections": ["Peer Set Definition", "Landscape Scope and Competitive Universe", "Pipeline Assets and Development Programs",
                  "Clinical Trial Activity and Development Momentum", "Patent Landscape and Intellectual Property Positioning",
                  "Product Differentiation and Competitive Positioning", "Market Dynamics, Competitive Crowding, and White Space",
                  "Monitoring Framework and Trigger Events", "Portfolio Implications and Strategic Recommendations",
                  "Evidence Confidence and Key Gaps", "Key Assumptions and Validation Priorities"]},
    {"key": "endpoint", "name": "Clinical Endpoint Intelligence", "short": "Clinical Endpoints", "icon": "wf_ep",
     "output": "Comprehensive Endpoint Assessment Report",
     "description": "Catalogue and benchmark trial endpoints, precedent and regulatory guidance to recommend an endpoint strategy.",
     "keywords": r"endpoint|sri-?4|acr ?(20|50|70)|easi|primary outcome|trial design",
     "sources": ["ClinicalTrials.gov", "FDA Prescription Drug Labels", "EMA Prescription Drug Labels", "European Clinical Trials",
                 "MEDLINE", "PubMed Central"],
     "subcategories": ["Define Clinical Setting & Study Population", "Endpoint Evidence & Evaluation",
                       "Identify & Catalog Clinical Endpoints", "Evaluate Endpoint Feasibility & Performance",
                       "Benchmark Primary Endpoint Outcomes", "Review Pivotal Trial & Approval Precedents",
                       "Assess Regulatory Expectations & Guidance", "Evaluate Secondary & Supporting Outcomes",
                       "Analyze Patient Subgroups & Response Patterns", "Endpoint Strategy & Recommendations",
                       "Develop Recommended Endpoint Strategy", "Generate Executive Insights & Summary",
                       "Final Report & Deliverables", "Compile Comprehensive Endpoint Assessment Report"],
     "sections": ["Clinical and Disease Context", "Endpoint Landscape", "Primary Endpoint Benchmarks", "Registration Precedents",
                  "Regulatory Guidance and Expectations", "Secondary and Supportive Endpoint Strategy",
                  "Subgroup and Subpopulation Considerations", "Endpoint Operational Feasibility",
                  "Recommended Endpoint Strategy"]},
    {"key": "epidemiology", "name": "Epidemiology Intelligence", "short": "Epidemiology", "icon": "wf_epi", "output": None,
     "description": "Estimate patient populations by geography and subgroup, and reconcile conflicting estimates.",
     "keywords": r"epidemiolog|prevalence|incidence|patient population|how many patients|addressable|market siz",
     "sources": ["MEDLINE", "PubMed Central", "ClinicalTrials.gov", "GWAS Catalog", "FDA Prescription Drug Labels", "Preprints"],
     "subcategories": ["Define Disease Population & Diagnostic Criteria", "Assess Target Geographies",
                       "Gather Regional Epidemiology Evidence", "Develop Geographic Disease Profiles",
                       "Consolidate Geographic Findings", "Evaluate Seven Epidemiology Dimensions",
                       "Global & Pooled Epidemiology Estimates", "Disease Burden by Patient Subgroup",
                       "Disease Progression & Clinical Burden", "Diagnostic Gaps & Underdiagnosis",
                       "Risk Factors & Comorbid Conditions", "Treatment & Clinical Trial Eligibility",
                       "Real-World Evidence Validation", "Estimate Patient Population Size",
                       "Resolve Conflicting Epidemiology Estimates", "Generate Executive Insights & Recommendations",
                       "Prepare Technical Methods & Evidence Appendix", "Generate Final Epidemiology Assessment Report"],
     "sections": ["Population Estimates at a Glance", "Target Population Definition", "Geographic Prevalence Estimates",
                  "Market Sizing Interpretation and Uncertainty", "Key Caveats and Limitations"]},
    {"key": "landscape", "name": "Therapeutic Landscape Intelligence", "short": "Therapeutic Landscape", "icon": "wf_tl",
     "output": "Treatment Landscape Intelligence & Competitive Assessment Report",
     "description": "Review approved and emerging therapies, pivotal evidence and safety to find mechanism gaps.",
     "keywords": r"landscape|standard of care|approved therap|treatment option|sequenc|mechanism cluster|white space",
     "sources": ["FDA Prescription Drug Labels", "EMA Prescription Drug Labels", "ClinicalTrials.gov", "Company websites",
                 "Conferences", "PubMed Central"],
     "subcategories": ["Define Treatment Landscape & Standard of Care", "Evaluate Four Therapeutic Landscape Dimensions",
                       "Marketed Treatments & Current Standards", "Emerging Therapies & Development Pipeline",
                       "Key Clinical Trials & Efficacy Evidence", "Safety Profiles & Tolerability Assessment",
                       "Map Mechanisms of Action & Identify Treatment Gaps",
                       "Generate Executive Insights & Strategic Recommendations", "Compile Clinical Trial Evidence Profiles",
                       "Develop Integrated Safety & Tolerability Profiles", "Validate Evidence Quality & Source Reliability",
                       "Generate Comprehensive Treatment Landscape Report"],
     "sections": ["Current Treatment Landscape", "Treatment Pathway and Sequencing", "Mechanism of Action Clusters and Unmet Need",
                  "Treatment Differentiation Gaps", "Methodology and Limitations"]},
    {"key": "unmet_need", "name": "Unmet Medical Needs Intelligence", "short": "Unmet Needs", "icon": "wf_un",
     "output": "Unmet Medical Needs & Therapeutic Opportunity Assessment",
     "description": "Assess eight dimensions of unmet need and define what clinically meaningful benefit looks like.",
     "keywords": r"unmet|burden|quality of life|underserved|meaningful benefit|gap in care",
     "sources": ["PubMed Central", "MEDLINE", "ClinicalTrials.gov", "FDA Prescription Drug Labels", "WHO", "News"],
     "subcategories": ["Evaluate Eight Dimensions of Unmet Medical Need", "Diagnostic Challenges & Care Gaps",
                       "Treatment Effectiveness & Limitations", "Historical Clinical Development Setbacks",
                       "Overall Unmet Need & Disease Impact", "Patient Burden & Quality of Life",
                       "Treatment Safety & Tolerability Challenges", "Current Treatment Standards & Limitations",
                       "Underserved & High-Need Patient Populations", "Synthesize Findings & Strategic Insights",
                       "Generate Executive Insights & Recommendations", "Define Clinically Meaningful Treatment Benefits",
                       "Generate Comprehensive Unmet Needs Report"],
     "sections": ["Current Standard of Care", "Limitations of Current Care", "Patient Burden and Impact of Inadequate Disease Control",
                  "Underserved Populations and Treatment Inequities", "Definition of Clinically Meaningful Benefit",
                  "Clinical Research and Development Context"]},
]
WORKFLOW_BY_KEY = {w["key"]: w for w in WORKFLOWS}


def workflow_out(w: dict) -> dict:
    return {k: v for k, v in w.items() if k != "keywords"} | {
        "index": WORKFLOWS.index(w), "subcategory_count": len(w["subcategories"]), "section_count": len(w["sections"])}


def resolve_workflow(key_or_index: str | int | None) -> dict:
    if key_or_index is None or key_or_index == "":
        return WORKFLOWS[0]
    if isinstance(key_or_index, int) or str(key_or_index).isdigit():
        i = int(key_or_index)
        if 0 <= i < len(WORKFLOWS):
            return WORKFLOWS[i]
    elif key_or_index in WORKFLOW_BY_KEY:
        return WORKFLOW_BY_KEY[key_or_index]
    raise KeyError(f"Unknown workflow: {key_or_index}")


def recommend_workflow(question: str) -> dict:
    """Highlights the workflow that best fits the question; the specialised workflows win over the default."""
    q = (question or "").lower()
    for w in WORKFLOWS[1:]:
        if re.search(w["keywords"], q):
            return w
    return WORKFLOWS[0]


# ------------------------------------------------------------------ starter questions (rephrase + follow-ups)
STARTER_QUESTIONS = {
    "ai": [
        "What role does IRAK4 play in the pathogenesis of SLE, RA, and AD, and which inflammatory pathways are shared or disease-specific?",
        "What genetic, molecular, preclinical, and clinical evidence supports IRAK4 as a therapeutic target in SLE, RA, and AD?",
        "How does IRAK4 inhibition affect TLR/IL-1R–MyD88 signaling, inflammatory cytokine production, and immune-cell activation in each disease?",
        "Which of the three diseases has the strongest scientific evidence supporting IRAK4 inhibition, and what are the major evidence gaps?",
        "Which biomarkers could predict patient response to IRAK4 inhibitors or demonstrate target engagement in SLE, RA, and AD?",
        "What clinical trials have evaluated IRAK4 inhibitors or degraders in SLE, RA, and AD, and what efficacy and safety findings have been reported?",
        "How does IRAK4 inhibition compare with JAK, TYK2, BTK, and cytokine-targeted therapies in biological rationale, efficacy evidence, and safety?",
        "Which patient subgroups or disease phenotypes in SLE, RA, and AD are most likely to benefit from IRAK4 inhibition, based on available evidence?",
        "What are the potential safety risks of long-term IRAK4 inhibition, particularly infection risk, immune suppression, and off-target effects?",
        "Based on published scientific and clinical evidence, which of SLE, RA, and AD offers the strongest rationale for further IRAK4 inhibitor development, and why?",
    ],
    "pipeline": [
        "Which IRAK4 inhibitors and degraders are currently in development for SLE, RA, and AD? Include drug name, sponsor, mechanism of action, and development phase.",
        "What registered clinical trials have evaluated IRAK4-targeting therapies in SLE, RA, and AD? Include NCT number, trial phase, enrollment, study status, and sponsor.",
        "Map IRAK4-targeting drug candidates across Research, Preclinical, Phase 1, Phase 2, Phase 3, Phase 4, and Marketed stages for the three diseases.",
        "What efficacy results have been reported for IRAK4 inhibitors in SLE, RA, and AD, and how do the outcomes compare across trials?",
        "Which primary and secondary endpoints have been used in IRAK4 clinical trials, and how appropriate are they for measuring treatment response in each indication?",
        "What adverse events, serious adverse events, discontinuations, and safety concerns have been reported in clinical trials of IRAK4-targeting therapies?",
        "Which companies are developing IRAK4-targeting therapies, and how do their assets compare by clinical stage, modality, trial progress, and differentiation?",
        "Which IRAK4 clinical development programs have been discontinued, terminated, or failed to meet their primary endpoints in SLE, RA, or AD, and what reasons have been reported?",
        "Which patient populations, treatment lines, or disease subtypes remain underrepresented in IRAK4 clinical trials across SLE, RA, and AD?",
        "Based on clinical trial activity, efficacy, safety, development maturity, and competitive intensity, which of SLE, RA, or AD should be prioritised?",
    ],
    "cioa": [
        "Identify all emerging IRAK4 inhibitors being developed for systemic lupus erythematosus (SLE), rheumatoid arthritis (RA), and atopic dermatitis (AD). Include company, asset name, mechanism of action, modality, development phase, target indication, and latest reported status. Distinguish direct IRAK4 inhibitors from other therapies targeting related pathways.",
        "Compare the clinical development pipelines of IRAK4 inhibitors across SLE, RA, and AD. Identify active and recently discontinued programs, clinical trial phases, key endpoints, reported efficacy and safety findings, and anticipated milestones. Highlight programs most likely to become significant competitors over the next 3–5 years.",
        "Analyze emerging therapeutic mechanisms competing with IRAK4 inhibition in SLE, RA, and AD, including relevant JAK, TYK2, BTK, and cytokine-targeting therapies. Compare biological rationale, clinical differentiation, safety considerations, and potential advantages or limitations of IRAK4 inhibition for each indication.",
    ],
}

# What each module is allowed to answer. Questions outside this scope get the "Please ask a relevant question" reply.
SCOPE = {
    "ai": "scientific questions about IRAK4 biology, validation and competitive positioning in SLE, RA and AD",
    "pipeline": "questions about the IRAK4 drug pipeline in SLE, RA and AD: assets, sponsors, clinical trials, efficacy, "
                "safety and prioritisation",
}
_DOMAIN = r"indication|unmet|therap|treatment|patient|disease|biomarker|efficacy|safety|pathway|cytokine|immun|inflamm|" \
          r"market|payer|competit|drug|asset|sponsor|endpoint|epidemiolog|prevalence"
SCOPE_PATTERN = {
    "ai": r"irak|myd88|myddosome|\bsle\b|lupus|rheumatoid|\bra\b|atopic|\bad\b|inhibit|degrader|" + _DOMAIN,
    "pipeline": r"irak|\bsle\b|lupus|rheumatoid|\bra\b|atopic|\bad\b|inhibit|degrader|pipeline|trial|program|" + _DOMAIN,
}

EVIDENCE_KINDS = [  # used to label "Emerging findings" while research runs
    ("clinical", "Clinical evidence", r"phase|trial|patients|clinical|registry|nct"),
    ("preclinical", "Preclinical evidence", r"mice|murine|model|preclinical|in vitro|cells|cell line"),
    ("genetic", "Genetic evidence", r"genetic|gwas|variant|deficien|polymorph|human genetics"),
]


def evidence_kind(text: str) -> dict:
    for key, label, pat in EVIDENCE_KINDS:
        if re.search(pat, text or "", re.I):
            return {"key": key, "label": label}
    return {"key": "mechanistic", "label": "Mechanistic evidence"}


# ------------------------------------------------------------------ help center
HELP_FAQ = [
    ("How do I start a new research question?", "Use the search box on Home or open AI Scientific Search. Type a free-form question; use AI Rephrase to expand it before you submit."),
    ("Does my research context follow me between modules?", "Yes. Target, mechanism, indications, geography and research session are kept when you switch modules."),
    ("How do I save, share or export my work?", "Use Save, Share and Export at the top of any result. Saved items live in Workspace, where you can reopen, rerun, duplicate, organise and share them."),
    ("What is the difference between Standard and Deep Research?", "Standard returns a quick, sourced answer. Deep Research cross-checks conflicting sources and takes longer."),
    ("Can I resume an analysis I left?", "Yes. Open History, then Research activity, and choose Resume on any in-progress, paused or failed item."),
    ("Why is a data source unavailable?", "Sources are enabled by administrators. Licensed sources may need access to be granted. See Data Connectors for status."),
    ("How are AI scores changed in Prioritization Scoring?", "Open \"Review and override AI-recommended scores\". A justification is required, and the override is traceable."),
    ("Can I collapse the navigation?", "Yes. Use the control at the bottom of the navigation, or press Ctrl+B."),
]
HELP_DOCS = [
    ("AI Scientific Search", "Ask free-form questions, select data sources, use Deep Research and review cited answers."),
    ("Drug Pipeline Explorer", "Explore programs in table, swimlane, graph, heatmap and radar views with shared filters."),
    ("Competitive Insights", "Choose one of five research workflows and review the report section by section."),
    ("Prioritization Scoring", "Configure criteria and weights, review AI scores, compare scenarios and export."),
    ("Workspace", "Organise saved research in folders, share with view or edit permission, and track versions."),
    ("History", "Review search history and research activity, then resume supported analyses."),
    ("Admin & Data Connectors", "Manage users, workflows, scoring models, templates, connectors and prompts."),
]
HELP_GUIDES = [
    ("Set your research context", "Choose target, mechanism, indications and geography from the header chip."),
    ("Ask a question", "Type on Home or AI Scientific Search. Use AI Rephrase if it helps."),
    ("Review the answer", "Expand or collapse sections, check evidence and references."),
    ("Save and share", "Save to Workspace, then share with colleagues using view or edit permission."),
    ("Come back later", "Resume unfinished work from Home or History."),
]
HELP_CHAT_RULES = [
    (r"context|target|indication|geograph", "Your research context (target, mechanism, indications, geography and session) is in the header chip. Select it to change it. Saved searches and results keep their own context."),
    (r"save|workspace|folder|share", "Use Save at the top of any result, then open Workspace to organise it in folders and share it with view or edit permission."),
    (r"history|resume|continue|earlier", "Open History. The Research activity tab lists steps and statuses, with Resume for supported analyses."),
    (r"score|weight|prioriti", "In Prioritization Scoring, change weights, then choose Apply & Recalculate. Override AI scores with a justification under \"Review and override AI-recommended scores\"."),
    (r"pipeline|drug|swimlane|heatmap", "Drug Pipeline Explorer shows the same programs in Table, Swimlane, Graph, Heatmap and Radar views. Filters apply to every view."),
    (r"workflow|competitive|report", "In Competitive Insights, ask a question, pick one of five workflows, then review the report. Use Change workflow to run the same question through another workflow."),
    (r"rephrase|suggest", "AI Rephrase rewrites your question in more detail. Review it, then submit."),
    (r"export|pdf|excel|powerpoint", "Choose Export at the top of a result and pick a format."),
    (r"nav|sidebar|collapse|menu", "Use the control at the bottom of the navigation, or press Ctrl+B."),
    (r"deep", "Deep Research cross-checks conflicting sources and takes longer than Standard."),
]
