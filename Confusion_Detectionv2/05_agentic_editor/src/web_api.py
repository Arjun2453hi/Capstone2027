"""Local React workspace API. Model output is data, never executable tools.

The API accepts proposal IDs, not arbitrary SlideEdit objects or file paths.
All durable state lives locally; only explicit generation sends text to Groq.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from common.llm_monitoring import sanitize_text
from . import _upstream, applier, checker, evaluation, layout_check, preview, repair, revise, storage
from .harness import HarnessPolicy, HarnessStopped, RequestRuntime
from .planner import EditPlanner, GroqEditGenerator, skip_reason, with_max_tokens
from .schema import CheckedEdit, SlideEdit
from .verifier import EditVerifier
from .workspace_store import EvidenceCache, WorkspaceStore

PROJECT = Path(__file__).resolve().parents[2]
EDITOR = PROJECT / "05_agentic_editor"
SOURCE = PROJECT / "data" / "cc-unit2-slides.pptx"
PDF = PROJECT / "data" / "cc-unit2-slides.pdf"


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Decision(StrictBody):
    decision: Literal["approved", "rejected", "pending"]


class StartRun(StrictBody):
    kind: Literal["gap", "repair"] = "gap"
    target_id: int = Field(ge=0, le=10000)


class ApplyRequest(StrictBody):
    proposal_ids: list[str] = Field(min_length=1, max_length=64)


def _checked_payload(c):
    return {**asdict(c.edit), "accepted": c.accepted, "problems": c.problems, "revisions": c.revisions}


def _edit(row):
    fields = SlideEdit.__dataclass_fields__
    return SlideEdit(**{k: v for k, v in row.items() if k in fields})


def create_app(state_dir=None, generator_factory=None):
    load_dotenv(PROJECT / ".env")
    policy = HarnessPolicy.from_env()
    store = WorkspaceStore(state_dir or PROJECT / ".cache" / "editor-workspace")
    source_digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    room = applier.RoomChecker(SOURCE)
    identity_path = store.directory / "source.sha256"
    if identity_path.exists() and identity_path.read_text().strip() != source_digest:
        raise RuntimeError("Source deck changed. Use a fresh workspace state directory before reviewing new edits.")
    identity_path.write_text(source_digest, encoding="utf-8")
    deck = _upstream.load_deck_json(PROJECT / "01_deck_parsing" / "parsed_deck.json")
    inputs = _upstream.load_gap_verification_input_json(PROJECT / "03_question_mapping" / "gap_verification_input.json")
    dossiers = {d.topic_id: d for d in inputs.topics}
    reports = {r.topic_id: r for r in (_upstream.GapReport(**json.loads(p.read_text(encoding="utf-8")))
               for p in sorted((PROJECT / "04_gap_reporting_agent" / "output").glob("topic_*_report.json")))}
    reviewed = EDITOR / "output" / "reviewed_suggestions.json"
    if reviewed.exists():
        raw = json.loads(reviewed.read_text(encoding="utf-8"))
        for group in ("topics", "repairs"):
            for items in raw.get(group, {}).values():
                store.add_proposals(items, "saved")
    cache = EvidenceCache()
    render_lock = threading.Lock()
    mutation_lock = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="editor-run")
    cancellations = {}
    token = secrets.token_urlsafe(32)
    factory = generator_factory or GroqEditGenerator
    questions = list(dict.fromkeys((PROJECT / "data" / "u2_v2_questions.txt").read_text(encoding="utf-8-sig").splitlines()))
    questions = [q.strip() for q in questions if q.strip()]

    @asynccontextmanager
    async def lifespan(app):
        yield
        for cancel in list(cancellations.values()):
            cancel.set()
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Margin · Lecture workspace", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.csrf_token = token
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            # Production is same-origin. Vite development is explicitly allowed.
            allowed = {f"http://{request.headers.get('host')}", "http://127.0.0.1:5173", "http://localhost:5173"}
            if origin and origin not in allowed:
                return JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
            if not secrets.compare_digest(request.headers.get("x-workspace-token", ""), token):
                return JSONResponse({"detail": "Refresh the workspace before making changes"}, status_code=403)
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1_000_000:
                    return JSONResponse({"detail": "Request exceeds 1 MB"}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        return response

    def find_proposal(key):
        row = next((r for r in store.proposals() if r["id"] == key), None)
        if row is None:
            raise HTTPException(404, "Proposal not found")
        return row

    def current_snapshot():
        return {"edits": [{k: v for k, v in row.items() if k not in ("id", "source", "decision", "review_note")}
                          for row in sorted(store.proposals(), key=lambda r: r["id"])]}

    def safe_run_error(exc):
        return sanitize_text(str(exc), secrets=(os.getenv("GROQ_API_KEY", ""),))[:350]

    def execute(run, cancel):
        key, kind, target = run["id"], run["kind"], run["target_id"]
        runtime = RequestRuntime(policy=policy, cancel=cancel, managed=True, emit=lambda event, **fields: store.event(key, event, **fields))
        def progress(message):
            store.update_run(key, message=message)
            store.event(key, "stage", message=message)
        try:
            runtime.check()
            store.update_run(key, status="running", message="Preparing source evidence")
            generator = factory()
            generator.runtime = runtime
            runtime.check()
            if kind == "gap":
                earlier = [_edit(row) for row in store.proposals() if row["decision"] == "approved"]
                checked = revise.suggest_for_topic(reports[target], dossiers[target], deck, dossiers,
                    EditPlanner(generator), EditVerifier(with_max_tokens(generator, 1500)), earlier=earlier,
                    progress=progress, room=room, trace=lambda event, **fields: store.event(key, event, **fields))
            else:
                edits = repair.plan_repairs(repair.RepairPlanner(generator), SOURCE, [target], deck)
                checked = checker.check_edits(edits, dossiers, deck)
            if cancel.is_set():
                raise HarnessStopped("Run cancelled; no new proposals committed")
            if not checked:
                raise RuntimeError(runtime.last_error or "No usable proposals returned; no edit was applied")
            ids = store.add_proposals([_checked_payload(c) for c in checked], key)
            store.update_run(key, status="completed", proposal_ids=ids, telemetry=runtime.summary(),
                             message=f"{sum(c.accepted for c in checked)} of {len(checked)} proposals passed review")
            store.event(key, "checkpoint_saved", proposal_count=len(ids))
        except Exception as exc:
            store.update_run(key, status="cancelled" if cancel.is_set() else "failed",
                             message=safe_run_error(exc), telemetry=runtime.summary())
        finally:
            with mutation_lock:
                cancellations.pop(key, None)

    @app.get("/api/workspace")
    def workspace():
        topics = []
        for topic_id, report in sorted(reports.items()):
            dossier = dossiers.get(topic_id)
            slides = dossier.slide_ids if dossier else []
            title = next((deck.get(s).title for s in slides if deck.get(s) and deck.get(s).title), "Unmatched questions")
            topics.append({"id": topic_id, "title": title, "gap_type": report.gap_type,
                           "confidence": report.confidence, "slide_ids": slides,
                           "question_count": len(dossier.matched_questions) if dossier else len(inputs.unmatched_questions),
                           "eligible": skip_reason(report, dossier) is None})
        return {"token": token, "title": "Cloud Computing", "filename": SOURCE.name,
                "slide_count": len(deck.slides), "question_count": len(questions), "topics": topics,
                "live_available": bool(os.getenv("GROQ_API_KEY")) or generator_factory is not None,
                "model": os.getenv("GROQ_EDITOR_MODEL_NAME", "openai/gpt-oss-120b"),
                "policy": asdict(policy)}

    @app.get("/api/topics/{topic_id}")
    def topic(topic_id: int):
        if topic_id not in reports:
            raise HTTPException(404, "Topic not found")
        dossier = dossiers.get(topic_id)
        return {"report": reports[topic_id].model_dump(),
                "questions": [m.question for m in dossier.matched_questions] if dossier else [q.question for q in inputs.unmatched_questions],
                "slides": [{"id": s, "title": deck.get(s).title, "text": deck.get(s).raw_text}
                           for s in (dossier.slide_ids if dossier else []) if deck.get(s)]}

    @app.get("/api/proposals")
    def proposals():
        return store.proposals()

    @app.post("/api/proposals/{key}/decision")
    def decision(key: str, body: Decision):
        with mutation_lock:
            row = find_proposal(key)
            if body.decision == "approved":
                if not row["accepted"] or row.get("problems"):
                    raise HTTPException(409, "Blocked proposals must be corrected and checked before approval")
                fresh = checker.check_edit(_edit(row), dossiers.get(row["topic_id"]), deck, room=room)
                if not fresh.accepted:
                    raise HTTPException(409, "This proposal no longer passes validation: " + "; ".join(fresh.problems))
            store.decide(key, body.decision)
            store.event("workspace", "proposal_decision", proposal_id=key, decision=body.decision)
        return {"id": key, "decision": body.decision}

    @app.get("/api/runs")
    def runs():
        return store.runs()

    @app.get("/api/runs/{key}")
    def run_details(key: str):
        run = next((r for r in store.runs() if r["id"] == key), None)
        if not run:
            raise HTTPException(404, "Run not found")
        return {**run, "events": store.events(key)}

    @app.post("/api/runs", status_code=202)
    def start_run(body: StartRun):
        if generator_factory is None and not os.getenv("GROQ_API_KEY"):
            raise HTTPException(503, "Set GROQ_API_KEY in the server .env to generate live suggestions")
        if body.kind == "gap":
            if body.target_id not in reports or skip_reason(reports[body.target_id], dossiers.get(body.target_id)):
                raise HTTPException(422, "This topic is not eligible for gap editing")
        elif deck.get(body.target_id) is None:
            raise HTTPException(422, "Slide does not exist")
        with mutation_lock:
            if len(cancellations) >= 4:
                raise HTTPException(429, "The local run queue is full")
            if any(r["status"] in ("queued", "running") and r.get("kind") == body.kind and r.get("target_id") == body.target_id for r in store.runs()):
                raise HTTPException(409, "This task is already queued or running")
            run = store.create_run(body.kind, target_id=body.target_id, message="Queued")
            cancel = threading.Event()
            cancellations[run["id"]] = cancel
            executor.submit(execute, run, cancel)
        return run

    @app.post("/api/runs/{key}/cancel")
    def cancel_run(key: str):
        with mutation_lock:
            cancel = cancellations.get(key)
            if cancel is None:
                raise HTTPException(409, "This run is no longer active")
            cancel.set()
            store.event(key, "cancel_requested")
        return {"message": "Cancellation requested; an in-flight model request may finish before stopping"}

    @app.post("/api/apply")
    def apply(body: ApplyRequest):
        with mutation_lock:
            if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != source_digest:
                raise HTTPException(409, "The source deck changed after this workspace was opened")
            if len(set(body.proposal_ids)) != len(body.proposal_ids):
                raise HTTPException(422, "Duplicate proposal IDs")
            rows = [find_proposal(key) for key in body.proposal_ids]
            if any(r["decision"] != "approved" or not r["accepted"] or r.get("problems") for r in rows):
                raise HTTPException(409, "Every selected proposal must pass checks and be explicitly approved")
            checked = checker.check_edits([_edit(r) for r in rows], dossiers, deck, room=room)
            if any(not c.accepted for c in checked):
                raise HTTPException(409, "Selected edits conflict or fail validation; review the selection")
            run = store.create_run("export", status="running", message="Writing approved edits")
            folder = store.directory / "exports" / run["id"]
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / "lecture.edited.pptx"
            try:
                # Applier writes a new file; publish only after a full successful save.
                applied = applier.apply_edits(SOURCE, output, [c.edit for c in checked], deck)
                storage.save_change_log_json(applied, SOURCE.name, output.name, folder / "changes.json")
                store.event(run["id"], "export_saved", proposal_ids=body.proposal_ids, source_sha256=source_digest)
                store.update_run(run["id"], status="completed", message=f"Exported {len(applied)} approved edits", proposal_ids=body.proposal_ids)
            except Exception as exc:
                store.update_run(run["id"], status="failed", message=safe_run_error(exc))
                raise HTTPException(500, "Deck export failed; inspect the run history") from exc
        return {"id": run["id"], "download_url": f"/api/exports/{run['id']}/deck", "changes_url": f"/api/exports/{run['id']}/changes"}

    @app.get("/api/exports/{key}/{kind}")
    def download(key: str, kind: Literal["deck", "changes"]):
        if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
            raise HTTPException(404, "Export not found")
        run = next((r for r in store.runs() if r["id"] == key and r["kind"] == "export" and r["status"] == "completed"), None)
        if not run:
            raise HTTPException(404, "Export not found")
        path = store.directory / "exports" / key / ("lecture.edited.pptx" if kind == "deck" else "changes.json")
        return FileResponse(path, filename=path.name)

    @app.get("/api/slides/{slide_id}/image")
    def slide_image(slide_id: int):
        if deck.get(slide_id) is None:
            raise HTTPException(404, "Slide not found")
        key = f"source-{slide_id}"
        content = cache.get(key)
        if content is None:
            import pypdfium2
            with render_lock:
                with pypdfium2.PdfDocument(str(PDF)) as pdf:
                    page = pdf[slide_id]
                    bitmap = page.render(scale=.9)
                    image = bitmap.to_pil()
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    content = buffer.getvalue()
                    image.close()
                    bitmap.close()
                    page.close()
                cache.put(key, content)
        return Response(content, media_type="image/png")

    @app.get("/api/proposals/{key}/image")
    def proposal_image(key: str):
        row = find_proposal(key)
        edit = _edit(row)
        if deck.get(edit.after_slide_id) is None:
            raise HTTPException(422, "Invalid anchor slide")
        content = cache.get(key)
        if content is None:
            from pptx import Presentation
            with render_lock, tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "preview.pptx"
                applied = applier.apply_edits(SOURCE, path, [edit], deck)
                prs = Presentation(str(path))
                image = preview.render_slide(prs.slides[applied[0].new_slide_positions[0]], prs.slide_width, prs.slide_height, width_px=960)
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                content = buffer.getvalue()
                image.close()
                del prs
                cache.put(key, content)
        return Response(content, media_type="image/png")

    @app.get("/api/layout-issues")
    def layout_issues():
        # Resource-heavy scan is explicit, never run on each page refresh.
        with render_lock:
            return [asdict(issue) for issue in layout_check.find_layout_issues(SOURCE, PDF, max_results=15)]

    @app.get("/api/evaluation")
    def metrics():
        return evaluation.evaluate(current_snapshot())

    @app.get("/api/evaluation/template")
    def template():
        return evaluation.review_template(current_snapshot(), questions)

    @app.post("/api/evaluation")
    def review_metrics(body: dict):
        try:
            return evaluation.evaluate(current_snapshot(), body)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    dist = EDITOR / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        @app.get("/")
        def missing_frontend():
            return JSONResponse({"detail": "Build the React frontend: cd 05_agentic_editor/frontend && npm install && npm run build"}, status_code=503)
    return app
