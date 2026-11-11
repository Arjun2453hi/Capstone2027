"""Synthetic student questions for a deck that has no real ones.

Per topic, the model reads that topic's slides and writes questions of
three intended kinds, plus a few off-topic questions for the whole deck:

  answered  -- the slides answer it
  deeper    -- about the topic, but asks for more depth, an example or a
               "why" the slides do not give
  missing   -- a closely related concept a student would expect here
               that the slides never mention
  off_topic -- from a different subject altogether

The intended kind is saved in questions.synthetic.meta.json. It is the
generator's INTENT, not a verified label: a "deeper" question may turn
out to be answered elsewhere in the deck. It must never be fed to the
pipeline, and any score against it must be reported as synthetic.
"""
from __future__ import annotations

import json
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

SYSTEM = """You write realistic questions that university students ask after a lecture.
Students write casually and briefly (one sentence, sometimes informal), the way they would
type into a course forum. Never mention "the slides", "the lecture" or "the deck".
Return only JSON."""

TOPIC_PROMPT = """Course: {title}
Lecture section (slides {start}-{end}):
---
{text}
---
Write {n_answered} questions this section answers, {n_deeper} questions about this section's
material that ask for more depth, a concrete example, or a reason than the text gives, and
{n_missing} question about a closely related concept a student would expect here but that the
text never mentions.

JSON: {{"questions": [{{"question": "...", "kind": "answered" | "deeper" | "missing"}}]}}"""

OFF_TOPIC_PROMPT = """Course: {title}
Write {n} questions a confused student might post in this course's forum that actually belong
to a completely different subject (not {title}). Mix several unrelated subjects.

JSON: {{"questions": [{{"question": "...", "kind": "off_topic"}}]}}"""

KINDS = ("answered", "deeper", "missing", "off_topic")
MAX_TOPIC_CHARS = 3500


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip()).lower()


def _call(client, model: str, prompt: str) -> List[dict]:
    response = client.chat.completions.create(
        model=model, temperature=0.7, max_tokens=1500, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
    data = json.loads(response.choices[0].message.content or "{}")
    out = []
    for item in data.get("questions", []):
        q, kind = str(item.get("question", "")).strip(), str(item.get("kind", "")).strip()
        if q and kind in KINDS and len(q) <= 300:
            out.append({"question": q if q.endswith("?") else q + "?", "kind": kind})
    return out


def generate(deck, topics, title: str, client, model: str, *, n_answered: int = 2, n_deeper: int = 2,
             n_missing: int = 1, n_off_topic: int = 4, seed: int = 2027,
             progress: Callable[[str], None] = print) -> List[dict]:
    rows, seen = [], set()

    def keep(items, topic_id, slide_ids):
        for it in items:
            if _norm(it["question"]) not in seen:
                seen.add(_norm(it["question"]))
                rows.append({**it, "topic_id": topic_id,
                             "slide_numbers": [s + 1 for s in slide_ids] if slide_ids else []})

    for t in topics:
        slides = [deck.get(s) for s in t.slide_ids]
        text = "\n\n".join(s.raw_text.strip() for s in slides if s is not None and s.raw_text.strip())
        if len(text) < 200:  # image-only or title-only section: nothing to ask about
            progress(f"topic {t.topic_id}: skipped (too little text)")
            continue
        prompt = TOPIC_PROMPT.format(title=title, start=t.start_slide_id + 1, end=t.end_slide_id + 1,
                                     text=text[:MAX_TOPIC_CHARS], n_answered=n_answered,
                                     n_deeper=n_deeper, n_missing=n_missing)
        try:
            items = _call(client, model, prompt)
        except Exception as exc:  # one topic failing must not lose the rest
            progress(f"topic {t.topic_id}: failed ({type(exc).__name__}); continuing")
            continue
        keep(items, t.topic_id, t.slide_ids)
        progress(f"topic {t.topic_id}: {len(items)} questions")
    if n_off_topic:
        try:
            keep(_call(client, model, OFF_TOPIC_PROMPT.format(title=title, n=n_off_topic)), None, [])
        except Exception as exc:
            progress(f"off-topic questions failed ({type(exc).__name__})")
    random.Random(seed).shuffle(rows)  # file order must not reveal the topic order
    return rows


def write(rows: List[dict], questions_path: Path, meta_path: Path, model: str, title: str) -> None:
    questions_path.write_text("\n".join(r["question"] for r in rows) + "\n", encoding="utf-8")
    counts = {k: sum(r["kind"] == k for r in rows) for k in KINDS}
    meta_path.write_text(json.dumps({
        "warning": "Synthetic questions. 'kind' is the generator's intent, not a verified label.",
        "generated_at": datetime.now(timezone.utc).isoformat(), "model": model, "deck_title": title,
        "counts": counts, "questions": rows}, indent=1, ensure_ascii=False), encoding="utf-8")


def generate_for_workspace(ws, progress: Callable[[str], None] = print, **kwargs) -> int:
    import importlib

    from common.deck_workspace import set_questions
    from common.llm_client import build_groq_client, resolve_model_name

    deck = importlib.import_module("01_deck_parsing.src.storage").load_deck_json(ws.parsed_deck)
    topics = importlib.import_module("02_topic_segmentation.src.storage").load_topics_json(ws.topics)
    model = resolve_model_name(os.getenv("SYNTHETIC_QUESTION_MODEL"))
    rows = generate(deck, topics, ws.title, build_groq_client(), model, progress=progress, **kwargs)
    if not rows:
        raise RuntimeError("No synthetic questions were generated (check the API key and quota)")
    write(rows, ws.synthetic_questions, ws.synthetic_meta, model, ws.title)
    set_questions(ws, ws.synthetic_questions, source="synthetic")
    return len(rows)
