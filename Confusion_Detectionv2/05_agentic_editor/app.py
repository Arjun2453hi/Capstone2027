"""app.py — live demo of the gap-to-slide-edit loop.

    streamlit run 05_agentic_editor/app.py

from the Confusion_Detectionv2 directory. Pick a gap found by stage 4,
see its report, let the LLM suggest slides (checked and fact-checked),
approve the ones you want, and apply them to a copy of the deck.
"""
from __future__ import annotations

import html
import importlib
import json
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(_PROJECT_ROOT / ".env")

editor = importlib.import_module("05_agentic_editor.src")
applier = importlib.import_module("05_agentic_editor.src.applier")
editor_storage = importlib.import_module("05_agentic_editor.src.storage")
upstream = importlib.import_module("05_agentic_editor.src._upstream")

DECK_JSON = _PROJECT_ROOT / "01_deck_parsing" / "parsed_deck.json"
GAP_INPUT = _PROJECT_ROOT / "03_question_mapping" / "gap_verification_input.json"
REPORTS_DIR = _PROJECT_ROOT / "04_gap_reporting_agent" / "output"
SOURCE_PDF = _PROJECT_ROOT / "data" / "cc-unit2-slides.pdf"
SOURCE_PPTX = _PROJECT_ROOT / "data" / "cc-unit2-slides.pptx"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DEMO_PPTX = OUTPUT_DIR / "cc-unit2-slides.demo.pptx"
DEMO_LOG = OUTPUT_DIR / "demo_change_log.json"
layout_check = importlib.import_module("05_agentic_editor.src.layout_check")

GAP_LABELS = {
    "complete_omission": "Missing entirely",
    "shallow_coverage": "Covered too thinly",
    "fragmented_context": "Scattered across slides",
    "covered": "Covered",
}

st.set_page_config(page_title="Slide Gap Editor", layout="wide")


@st.cache_resource
def load_inputs():
    deck = upstream.load_deck_json(DECK_JSON)
    dossiers = {d.topic_id: d for d in upstream.load_gap_verification_input_json(GAP_INPUT).topics}
    reports = {}
    for path in sorted(REPORTS_DIR.glob("topic_*_report.json")):
        report = upstream.GapReport(**json.loads(path.read_text(encoding="utf-8")))
        reports[report.topic_id] = report
    index = json.loads((REPORTS_DIR / "index.json").read_text(encoding="utf-8"))
    return deck, dossiers, reports, index


@st.cache_resource
def llm():
    generator = editor.GroqEditGenerator()
    return editor.EditPlanner(generator), editor.EditVerifier(generator), generator.model


@st.cache_data
def slide_image(slide_id: int):
    import pypdfium2

    pdf = pypdfium2.PdfDocument(str(SOURCE_PDF))
    return pdf[slide_id].render(scale=0.8).to_pil()


@st.cache_data
def broken_layout_candidates():
    return layout_check.find_layout_issues(SOURCE_PPTX, SOURCE_PDF, max_results=10)


def slide_preview(edit) -> str:
    """The new slide drawn the way the applier lays it out: course
    banner, orange title, rule, bullets."""
    bullets = "".join(f"<li>{html.escape(b)}</li>" for b in edit.bullets)
    return f"""
<div style="aspect-ratio:16/9;background:#fff;border:1px solid #ccc;border-radius:4px;padding:2.2% 3.5%;
            font-family:Calibri,Arial,sans-serif;color:#222;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.15)">
  <div style="font-size:0.75em;font-weight:700;color:#C55A11;letter-spacing:.04em">CLOUD COMPUTING</div>
  <div style="font-size:1.25em;font-weight:700;color:#C55A11;margin-top:.2em">{html.escape(edit.title)}</div>
  <div style="height:3px;background:#C55A11;width:68%;margin:.5em 0 .7em"></div>
  <ul style="font-size:0.85em;line-height:1.35;margin:0;padding-left:1.2em">{bullets}</ul>
</div>"""


def edit_key(edit) -> str:
    return f"{edit.topic_id}|{edit.after_slide_id}|{edit.title}"


deck, dossiers, reports, index = load_inputs()
state = st.session_state
state.setdefault("suggestions", {})  # topic_id -> [CheckedEdit]
state.setdefault("approved", {})  # edit_key -> bool
state.setdefault("applied", None)

st.title("Slide Gap Editor")
st.caption("Student questions → gaps in the deck → LLM-written slides → checked → applied to a copy of the deck")

# ---- Step 1: pick a gap -------------------------------------------------
gaps = [row for row in index if row["gap_type"] != "covered" and row["topic_id"] in dossiers and dossiers[row["topic_id"]].slide_ids]
with st.sidebar:
    st.header("1. Pick a gap")
    st.caption(f"{len(gaps)} topics with a gap, worst first")
    labels = {
        row["topic_id"]: f"Topic {row['topic_id']} · {GAP_LABELS.get(row['gap_type'], row['gap_type'])} · {row['backed_by_questions']} questions"
        for row in gaps
    }
    topic_id = st.radio("Gap", list(labels), format_func=labels.get, label_visibility="collapsed")
    st.divider()
    n_approved = sum(state.approved.values())
    st.metric("Slides approved so far", n_approved)

report, dossier = reports[topic_id], dossiers[topic_id]
row = next(r for r in gaps if r["topic_id"] == topic_id)

# ---- Step 2: the gap report --------------------------------------------
st.header(f"2. What the gap report found — topic {topic_id}")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Gap type", GAP_LABELS.get(report.gap_type, report.gap_type))
c2.metric("Agent confidence", f"{report.confidence:.0%}")
c3.metric("Student questions", len(dossier.matched_questions))
c4.metric("Slides in topic", f"{min(dossier.slide_ids) + 1}–{max(dossier.slide_ids) + 1}")
left, right = st.columns([3, 2])
with left:
    with st.expander("Full report from the investigating agent", expanded=True):
        st.markdown(report.report_text)
with right:
    st.markdown("**Questions students asked about this topic**")
    for m in dossier.matched_questions:
        st.markdown(f"- {m.question}")

reason = editor.skip_reason(report, dossier)
if reason:
    st.warning(f"The editor does not act on this report: {reason}.")

# ---- Step 3: suggestions ------------------------------------------------
st.header("3. Changes the LLM suggests")
if st.button("Suggest slides for this gap", type="primary", disabled=reason is not None):
    planner, verifier, model = llm()
    with st.status(f"Working with {model}…", expanded=True) as status:
        st.write("Writing new slides from the report and the topic's slides…")
        edits = planner.plan_topic(report, dossier, deck)
        error = getattr(planner.generator, "last_error", None)
        if error:
            status.update(label="The LLM call failed", state="error")
            st.error(f"Could not get suggestions from {model}: {error}. Wait a minute and try again.")
            st.stop()
        st.write(f"{len(edits)} slide(s) proposed. Running the quality checks…")
        # Checked together with the slides already suggested for other
        # gaps, so a repeat of one of them (e.g. both copies of the
        # duplicated lecture asking for the same slide) is caught.
        earlier = [c.edit for t, cs in state.suggestions.items() if t != topic_id for c in cs if c.accepted]
        checked = editor.check_edits(earlier + edits, dossiers, deck)[len(earlier):]
        st.write(f"{sum(c.accepted for c in checked)} passed. Fact-checking them against the deck…")
        editor.verify_checked(checked, verifier, dossiers, deck)
        status.update(label=f"{sum(c.accepted for c in checked)} of {len(checked)} suggestion(s) passed every check", state="complete")
    state.suggestions[topic_id] = checked
    for c in checked:
        state.approved[edit_key(c.edit)] = c.accepted

suggestions = state.suggestions.get(topic_id)
if suggestions is None:
    st.info("Click the button to have the LLM suggest slides for this gap.")
elif not suggestions:
    st.warning("The LLM proposed no slides for this gap.")
for i, c in enumerate(suggestions or [], start=1):
    e = c.edit
    st.subheader(f"Suggestion {i}: insert after slide {e.after_slide_id + 1}")
    before, after = st.columns(2)
    with before:
        st.caption(f"Existing slide {e.after_slide_id + 1} (stays as it is)")
        st.image(slide_image(e.after_slide_id), width="stretch")
    with after:
        st.caption("New slide that goes right after it")
        st.markdown(slide_preview(e), unsafe_allow_html=True)
    st.markdown("**Answers:** " + "; ".join(f"“{q}”" for q in e.addresses_questions))
    if c.accepted:
        st.success("Passed the quality checks and the fact-check.")
    else:
        st.error("Flagged — not approved automatically:\n\n" + "\n".join(f"- {p}" for p in c.problems))
    key = edit_key(e)
    state.approved[key] = st.checkbox("Approve this slide", value=state.approved.get(key, False), key=f"approve-{key}")
    st.divider()

# ---- Step 4: apply -----------------------------------------------------
st.header("4. Apply the approved slides to the deck")
approved = [c.edit for checked in state.suggestions.values() for c in checked if state.approved.get(edit_key(c.edit))]
st.write(f"{len(approved)} slide(s) approved across all gaps. The original deck is never changed; a copy is written.")
if st.button("Apply approved slides", type="primary", disabled=not approved):
    applied = applier.apply_edits(SOURCE_PPTX, DEMO_PPTX, approved, deck)
    editor_storage.save_change_log_json(applied, SOURCE_PPTX, DEMO_PPTX, DEMO_LOG)
    state.applied = applied

if state.applied:
    total = sum(len(a.new_slide_positions) for a in state.applied)
    st.success(f"Done: {len(deck.slides)} slides → {len(deck.slides) + total} slides.")
    for a in state.applied:
        where = ", ".join(str(p + 1) for p in a.new_slide_positions)
        extra = " (also added to the repeated copy of this lecture)" if a.mirrored_after_slide_ids else ""
        st.markdown(f"- **{a.edit.title}** → now slide {where}{extra}")
    d1, d2 = st.columns(2)
    d1.download_button("Download the edited deck", DEMO_PPTX.read_bytes(), file_name=DEMO_PPTX.name)
    if d2.button("Open it in PowerPoint"):
        subprocess.run(["open", "-a", "Microsoft PowerPoint", str(DEMO_PPTX)], check=False)

# ---- Separate check: slides with a broken layout -------------------------
st.divider()
st.header("Separate check: slides whose layout looks broken")
st.caption(
    "Text drawn over other text, or pictures covering paragraphs, found from the slide geometry and the PDF rendering. "
    "These are candidates for a person to confirm and fix by hand; the editor does not change them."
)
if st.button("Find slides with a broken layout"):
    state.broken = broken_layout_candidates()
for issue in state.get("broken") or []:
    image, text = st.columns([1, 2])
    image.image(slide_image(issue.slide_id), width="stretch")
    text.markdown(f"**Slide {issue.slide_id + 1}** · score {issue.score:.2f}\n\n" + "\n".join(f"- {r}" for r in issue.reasons))
