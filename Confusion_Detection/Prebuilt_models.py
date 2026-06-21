"""
Gap Detection: Find what your slide deck fails to answer.

Usage:
    Just run:  python gap_detection.py
    Paths are hardcoded below in the INPUT PATHS section.

Output:
    gap_report.txt  — ranked list of slides and what they're missing

How it works (high level):
    1. Extract text spans from each slide in the PDF.
    2. For each question, use a Cross-Encoder to find the most relevant slide span.
    3. Run a QA model on that span — if it can't extract an answer, the question "fails".
    4. Cluster failed questions that point to the same slide.
    5. Use FLAN-T5 to turn each cluster of failed questions into one readable gap sentence.
    6. Write a report: slide-by-slide, sorted worst-first.
"""

import os
import json
import textwrap
import warnings
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Dict

import numpy as np
import torch
import pdfplumber
from transformers import pipeline, AutoTokenizer, AutoModelForSeq2SeqLM
from sentence_transformers import SentenceTransformer, CrossEncoder
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from tqdm import tqdm

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────
# CONFIG  (change these to tune behaviour)
# ─────────────────────────────────────────────
MAX_QUESTIONS         = None   # how many questions to process (None = all)
MAX_SPANS             = None    # max slide spans to consider (None = all)
ANSWERABILITY_THRESH  = 0.40   # p_ans below this → question is unanswered
TOP_K_SPANS           = 5      # cross-encoder candidates per question
MAX_GAP_CLUSTERS      = 20     # cap on slide groups to summarise
SPAN_MIN_LEN          = 30     # ignore spans shorter than this (chars)
SPAN_MAX_LEN          = 512    # split spans longer than this


# ─────────────────────────────────────────────
# DATA CLASS
# ─────────────────────────────────────────────
@dataclass
class SlideSpan:
    text:     str
    slide_id: int   # 0-indexed page number


# ─────────────────────────────────────────────
# STEP 1 — Extract text spans from the PDF
# ─────────────────────────────────────────────
def extract_spans(pdf_path: str) -> List[SlideSpan]:
    """
    Open the PDF and pull text out page by page.
    Each page may produce multiple spans if it's long — we chunk at SPAN_MAX_LEN
    so the models (which have a 512-token limit) see manageable pieces.
    """
    spans = []
    with pdfplumber.open(pdf_path) as pdf:
        for slide_id, page in enumerate(pdf.pages):
            raw = page.extract_text()
            if not raw:
                continue

            lines = [l.strip() for l in raw.split("\n") if l.strip()]
            chunk, chunk_len = [], 0

            for line in lines:
                if chunk_len + len(line) > SPAN_MAX_LEN and chunk:
                    text = " ".join(chunk)
                    if len(text) >= SPAN_MIN_LEN:
                        spans.append(SlideSpan(text=text, slide_id=slide_id))
                    chunk, chunk_len = [line], len(line)
                else:
                    chunk.append(line)
                    chunk_len += len(line)

            if chunk:
                text = " ".join(chunk)
                if len(text) >= SPAN_MIN_LEN:
                    spans.append(SlideSpan(text=text, slide_id=slide_id))

    return spans


# ─────────────────────────────────────────────
# STEP 2 — Load questions from a .txt file
# ─────────────────────────────────────────────
def load_questions(path: str) -> List[str]:
    """One question per line. Empty lines are skipped."""
    with open(path, "r", encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


# ─────────────────────────────────────────────
# STEP 3 — Load all four models
# ─────────────────────────────────────────────
def load_models(device: str):
    """
    Returns (cross_encoder, qa_pipeline, sbert, flan_tokenizer, flan_model).

    - cross_encoder : ranks how relevant each span is for a question
    - qa_pipeline   : extracts an answer from a span (or says "no answer")
    - sbert         : embeds questions for clustering
    - flan_*        : summarises a cluster of failed questions into one gap sentence
    """
    print("[1/4] Loading Cross-Encoder...")
    ce = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", device=device)

    print("[2/4] Loading QA model (RoBERTa)...")
    qa = pipeline(
        "question-answering",
        model="deepset/roberta-base-squad2",
        device=0 if device == "cuda" else -1,
        handle_impossible_answer=True,
    )

    print("[3/4] Loading Sentence-BERT...")
    sb = SentenceTransformer("all-MiniLM-L6-v2", device=device)

    print("[4/4] Loading FLAN-T5...")
    tok = AutoTokenizer.from_pretrained("google/flan-t5-base")
    mdl = AutoModelForSeq2SeqLM.from_pretrained("google/flan-t5-base").to(device)

    print("All models ready.\n")
    return ce, qa, sb, tok, mdl


# ─────────────────────────────────────────────
# STAGE 1 — Score every question against the slides
# ─────────────────────────────────────────────
def run_stage1(
    questions: List[str],
    spans:     List[SlideSpan],
    ce,        # cross-encoder model
    qa,        # qa pipeline
    device:    str,
) -> List[Dict]:
    """
    For each question:
      R(q, s)  = cross-encoder relevance score (how related is this span?)
      p_ans    = probability the QA model found a real answer in the best span
      p_null   = 1 - p_ans  (probability of NO answer → gap signal)
      M(q, s)  = p_null × R(q, s) × (1 - p_ans)  — missingness score

    A question is marked as a GAP when p_ans < ANSWERABILITY_THRESH.
    The span with the highest M score is the 'location' — the slide most
    responsible for failing this question.
    """
    results = []

    for question in tqdm(questions, desc="Stage 1 — scoring questions"):

        # --- Relevance: score all spans with the cross-encoder ---
        pairs = [(question, s.text[:512]) for s in spans]
        raw_scores = ce.predict(pairs, show_progress_bar=False)
        # Normalise to [0,1] with sigmoid
        R_scores = 1.0 / (1.0 + np.exp(-raw_scores))

        top_k_idx = np.argsort(R_scores)[::-1][:TOP_K_SPANS]

        # --- Answerability: run QA on the single best span ---
        best_span = spans[top_k_idx[0]]
        qa_result = qa(
            question=question,
            context=best_span.text[:512],
            handle_impossible_answer=True,
        )
        p_ans = qa_result["score"]
        if qa_result.get("answer", "").strip() == "":
            p_ans = 0.0
        p_null = 1.0 - p_ans

        # --- Missingness scores for top-k spans ---
        M_scores = np.zeros(len(spans))
        for i in top_k_idx:
            M_scores[i] = p_null * R_scores[i] * (1.0 - p_ans)

        # --- Localisation: which span is most responsible? ---
        loc_idx   = int(np.argmax(M_scores))
        loc_span  = spans[loc_idx]

        results.append({
            "question":       question,
            "is_gap":         p_ans < ANSWERABILITY_THRESH,
            "p_ans":          float(p_ans),
            "p_null":         float(p_null),
            "answer_found":   qa_result.get("answer", ""),
            "slide_id":       loc_span.slide_id,        # 0-indexed
            "slide_number":   loc_span.slide_id + 1,    # 1-indexed (human-readable)
            "span_excerpt":   loc_span.text[:120],
            "missing_score":  float(M_scores[loc_idx]),
        })

    return results


# ─────────────────────────────────────────────
# STAGE 2 — Cluster + summarise gaps per slide
# ─────────────────────────────────────────────
def cluster_questions(questions: List[str], sbert, max_k: int = 4) -> List[List[str]]:
    """
    Embed questions with Sentence-BERT, then find the best number of
    K-Means clusters (k=2..max_k) using silhouette score.
    Returns a list of clusters, each cluster being a list of question strings.
    Small groups (≤2) are returned as-is without clustering.
    """
    if len(questions) <= 2:
        return [questions]

    embeddings = sbert.encode(questions, convert_to_numpy=True, show_progress_bar=False)
    best_k, best_score = 2, -1

    for k in range(2, min(max_k + 1, len(questions))):
        km = KMeans(n_clusters=k, random_state=42, n_init=5)
        labels = km.fit_predict(embeddings)
        if len(set(labels)) > 1:
            score = silhouette_score(embeddings, labels)
            if score > best_score:
                best_score, best_k = score, k

    km_final = KMeans(n_clusters=best_k, random_state=42, n_init=5)
    labels = km_final.fit_predict(embeddings)

    clusters: Dict[int, List[str]] = defaultdict(list)
    for q, lbl in zip(questions, labels):
        clusters[lbl].append(q)

    return list(clusters.values())


def summarise_gap(cluster_qs: List[str], span_ctx: str, tok, mdl, device: str) -> str:
    """
    Feed FLAN-T5 a cluster of failed questions + the slide context it failed on.
    Ask it to produce ONE concise sentence describing what concept is missing.
    """
    qs_str = " | ".join(cluster_qs[:5])
    prompt = (
        f"The following questions could not be answered by the slide content.\n"
        f"Slide context: {span_ctx[:200]}\n"
        f"Unanswered questions: {qs_str}\n"
        f"Summarize the missing concept in one concise sentence:"
    )
    inputs = tok(prompt, return_tensors="pt", truncation=True, max_length=512).to(device)
    with torch.no_grad():
        out = mdl.generate(**inputs, max_new_tokens=80, num_beams=4)
    return tok.decode(out[0], skip_special_tokens=True)


def run_stage2(stage1_results: List[Dict], sbert, tok, mdl, device: str) -> List[Dict]:
    """
    Takes all questions flagged as gaps, groups them by the slide they point to,
    clusters within each group semantically, then summarises each cluster.

    Returns a list of gap records sorted by severity (worst slides first).
    """
    # Filter to only the gaps
    gaps = [r for r in stage1_results if r["is_gap"]]
    if not gaps:
        return []

    # Group failed questions by the slide they were localised to
    by_slide: Dict[int, List[Dict]] = defaultdict(list)
    for r in gaps:
        by_slide[r["slide_id"]].append(r)

    gap_records = []

    # Process the worst slides first (most failed questions), cap at MAX_GAP_CLUSTERS
    sorted_slides = sorted(by_slide.items(), key=lambda x: -len(x[1]))[:MAX_GAP_CLUSTERS]

    for slide_id, group in tqdm(sorted_slides, desc="Stage 2 — summarising gaps"):
        qs_in_group  = [r["question"] for r in group]
        span_ctx     = group[0]["span_excerpt"]
        slide_number = group[0]["slide_number"]

        clusters = cluster_questions(qs_in_group, sbert)

        for cluster_qs in clusters:
            if not cluster_qs:
                continue

            gap_text   = summarise_gap(cluster_qs, span_ctx, tok, mdl, device)
            avg_score  = float(np.mean([
                r["missing_score"] for r in group if r["question"] in cluster_qs
            ]))

            gap_records.append({
                "slide_number":            slide_number,
                "span_excerpt":            span_ctx,
                "gap_summary":             gap_text,
                "num_questions":           len(cluster_qs),
                "avg_missing_score":       avg_score,
                "representative_questions": cluster_qs[:3],
            })

    # Sort: worst slide (highest score) first
    gap_records.sort(key=lambda x: -x["avg_missing_score"])
    return gap_records


# ─────────────────────────────────────────────
# WRITE THE REPORT
# ─────────────────────────────────────────────
def write_report(gap_records: List[Dict], stage1_results: List[Dict], out_path: str):
    """
    Writes a plain-text report structured as:

      SUMMARY
      -------
      … totals …

      GAP #1  |  Slide 12  |  Severity: 0.712  |  Backed by 5 questions
      ─────────────────────────────────────────────────────────────────
      MISSING:  The slides do not explain how dropout is applied during training.

      SLIDE CONTEXT:
        "… excerpt of the slide text …"

      QUESTIONS THAT COULDN'T BE ANSWERED:
        • What is dropout and when is it used?
        • How does dropout prevent overfitting?
        • …

      (repeat for each gap)
    """
    n_total    = len(stage1_results)
    n_gaps     = sum(1 for r in stage1_results if r["is_gap"])
    n_answered = n_total - n_gaps
    coverage   = 100.0 * n_answered / n_total if n_total else 0

    LINE  = "─" * 70
    DLINE = "═" * 70

    with open(out_path, "w", encoding="utf-8") as f:

        f.write(DLINE + "\n")
        f.write("  GAP DETECTION REPORT\n")
        f.write(DLINE + "\n\n")

        f.write(f"  Questions analysed   : {n_total}\n")
        f.write(f"  Answered by slides   : {n_answered}  ({coverage:.1f}%)\n")
        f.write(f"  Unanswered (gaps)    : {n_gaps}\n")
        f.write(f"  Gap clusters found   : {len(gap_records)}\n")
        f.write("\n" + DLINE + "\n\n")

        if not gap_records:
            f.write("  No gaps found — all questions are answered by the slides.\n")
            return

        for i, gap in enumerate(gap_records, 1):
            f.write(f"\n  GAP #{i}  |  Slide {gap['slide_number']}  "
                    f"|  Severity: {gap['avg_missing_score']:.3f}  "
                    f"|  {gap['num_questions']} question(s)\n")
            f.write(LINE + "\n")

            # What is missing
            f.write(f"\n  MISSING:\n")
            wrapped = textwrap.fill(
                gap["gap_summary"],
                width=66,
                initial_indent="    ",
                subsequent_indent="    ",
            )
            f.write(wrapped + "\n")

            # Slide context excerpt
            f.write(f"\n  SLIDE CONTEXT:\n")
            ctx = textwrap.fill(
                gap["span_excerpt"],
                width=66,
                initial_indent='    "',
                subsequent_indent='     ',
            )
            f.write(ctx + '"\n')

            # Representative questions
            f.write(f"\n  QUESTIONS THAT COULDN'T BE ANSWERED:\n")
            for q in gap["representative_questions"]:
                wrapped_q = textwrap.fill(
                    q,
                    width=64,
                    initial_indent="    • ",
                    subsequent_indent="      ",
                )
                f.write(wrapped_q + "\n")

            f.write("\n")

        f.write(DLINE + "\n")
        f.write("  END OF REPORT\n")
        f.write(DLINE + "\n")


# ─────────────────────────────────────────────
# INPUT PATHS  ← change these to your files
# ─────────────────────────────────────────────
PDF_PATH       = r"C:\Users\NAGARJUN N H\OneDrive\Desktop\Capstone\Capstone2027\Data\se-u2-slides.pdf"
QUESTIONS_PATH = r"C:\Users\NAGARJUN N H\OneDrive\Desktop\Capstone\Capstone2027\Data\u2_questions.txt"
OUTPUT_PATH    = r"C:\Users\NAGARJUN N H\OneDrive\Desktop\Capstone\Capstone2027\Data\gap_report.txt"


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    assert os.path.exists(PDF_PATH),       f"PDF not found: {PDF_PATH}"
    assert os.path.exists(QUESTIONS_PATH), f"Questions file not found: {QUESTIONS_PATH}"

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}\n")

    # ── Load inputs ──────────────────────────────────────────────
    print("Extracting slide spans from PDF...")
    spans = extract_spans(PDF_PATH)
    print(f"  → {len(spans)} spans from {len(set(s.slide_id for s in spans))} slides\n")

    print("Loading questions...")
    questions = load_questions(QUESTIONS_PATH)
    print(f"  → {len(questions)} questions loaded\n")

    # Apply caps
    if MAX_QUESTIONS:
        questions = questions[:MAX_QUESTIONS]
    if MAX_SPANS:
        spans = spans[:MAX_SPANS]

    # ── Load models ───────────────────────────────────────────────
    ce, qa, sbert, flan_tok, flan_mdl = load_models(device)

    # ── Stage 1: detect gaps ──────────────────────────────────────
    print("Running Stage 1 — gap detection...\n")
    stage1_results = run_stage1(questions, spans, ce, qa, device)

    n_gaps = sum(1 for r in stage1_results if r["is_gap"])
    print(f"\nStage 1 done.  Gaps found: {n_gaps} / {len(stage1_results)}\n")

    # ── Stage 2: summarise gaps ───────────────────────────────────
    print("Running Stage 2 — gap summarisation...\n")
    gap_records = run_stage2(stage1_results, sbert, flan_tok, flan_mdl, device)

    # ── Write report ──────────────────────────────────────────────
    write_report(gap_records, stage1_results, OUTPUT_PATH)
    print(f"\nDone.  Report written to: {OUTPUT_PATH}")

    # Also save raw JSON alongside the report
    json_out = OUTPUT_PATH.replace(".txt", ".json")
    with open(json_out, "w") as f:
        json.dump({"stage1": stage1_results, "gaps": gap_records}, f, indent=2)
    print(f"Raw data saved to:        {json_out}")


if __name__ == "__main__":
    main()