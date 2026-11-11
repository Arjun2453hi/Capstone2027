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

from common import deck_workspace
from common.llm_client import has_credentials
from common.llm_monitoring import sanitize_text
from . import _upstream, applier, checker, display, evaluation, layout_check, pdf_applier, preview, repair, revise, storage
from .harness import HarnessPolicy, HarnessStopped, RequestRuntime
from .planner import EditPlanner, GroqEditGenerator, skip_reason, with_max_tokens
from .schema import CheckedEdit, SlideEdit
from .verifier import EditVerifier
from .workspace_store import EvidenceCache, WorkspaceStore

PROJECT = Path(__file__).resolve().parents[2]
EDITOR = PROJECT / "05_agentic_editor"


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


def _render_pdf_page(pdf, index: int, scale: float = .9) -> bytes:
    """PNG of one page; pdf is a path or PDF bytes."""
    import pypdfium2
    with pypdfium2.PdfDocument(pdf if isinstance(pdf, bytes) else str(pdf)) as doc:
        page = doc[index]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image.close()
        bitmap.close()
        page.close()
    return buffer.getvalue()


def create_app(state_dir=None, generator_factory=None, deck_id=None, include_saved=None):
    """The workspace for ONE deck (deck_id, else $DECK_ID, else the only
    deck). Every path, the title and the question set come from that
    deck's workspace; nothing here names a specific deck."""
    load_dotenv(PROJECT / ".env")
    ws = deck_workspace.resolve(deck_id)
    if not ws.parsed_deck.exists():
        raise RuntimeError(f"Deck '{ws.deck_id}' has not been processed yet: python run_pipeline.py run {ws.deck_id}")
    SOURCE, PDF = ws.edit_source, ws.pdf
    is_pdf = ws.editable_format == "pdf"
    capabilities = ws.capabilities
    policy = HarnessPolicy.from_env()
    store = WorkspaceStore(state_dir or ws.editor_state)
    source_digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    # PDF decks can only gain new pages; a .pptx deck's real slide space is measured.
    room = pdf_applier.pdf_room_check if is_pdf else applier.RoomChecker(SOURCE)
    banner = pdf_applier.find_banner(PDF) if is_pdf else None
    identity_path = store.directory / "source.sha256"
    if identity_path.exists() and identity_path.read_text().strip() != source_digest:
        raise RuntimeError("Source deck changed. Use a fresh workspace state directory before reviewing new edits.")
    identity_path.write_text(source_digest, encoding="utf-8")
    deck = ws.editor_deck()  # the .pptx text when there is one; same slide ids
    if ws.gap_input.exists():
        inputs = _upstream.load_gap_verification_input_json(ws.gap_input)
        dossiers, unmatched = {d.topic_id: d for d in inputs.topics}, list(inputs.unmatched_questions)
    else:  # questions not mapped yet: the deck opens with no topics to work on
        dossiers, unmatched = {}, []
    reports = {r.topic_id: r for r in (_upstream.GapReport(**json.loads(p.read_text(encoding="utf-8")))
               for p in sorted(ws.reports_dir.glob("topic_*_report.json")))} if ws.reports_dir.exists() else {}
    # Pre-generated suggestions are opt-in (include_saved, or EDITOR_INCLUDE_SAVED=1):
    # by default every suggestion on screen comes from a live run in this workspace.
    if include_saved is None:
        include_saved = os.getenv("EDITOR_INCLUDE_SAVED", "").strip().lower() in ("1", "true", "yes")
    reviewed = ws.editor_dir / "reviewed_suggestions.json"
    if not include_saved:
        store.remove_unapproved("saved")
    if include_saved and reviewed.exists():
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
    questions = list(dict.fromkeys(ws.questions.read_text(encoding="utf-8-sig").splitlines())) if ws.questions.exists() else []
    questions = [q.strip() for q in questions if q.strip()]

    @asynccontextmanager
    async def lifespan(app):
        yield
        for cancel in list(cancellations.values()):
            cancel.set()
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Margin · Lecture workspace", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.deck_id = ws.deck_id
    app.state.busy = lambda: bool(cancellations)  # a queued or running job
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
                    EditPlanner(generator, insert_only=is_pdf), EditVerifier(with_max_tokens(generator, 1500)), earlier=earlier,
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
            title = display.topic_title(deck, slides, fallback="Unmatched questions")
            topics.append({"id": topic_id, "title": title, "gap_type": report.gap_type,
                           "confidence": report.confidence, "slide_ids": slides,
                           "question_count": len(dossier.matched_questions) if dossier else len(unmatched),
                           "eligible": skip_reason(report, dossier) is None})
        return {"token": token, "title": ws.title, "filename": SOURCE.name, "deck_id": ws.deck_id,
                "format": ws.editable_format, "capabilities": capabilities,
                "question_source": ws.question_source, "pipeline": ws.status(),
                "decks": [{**d.summary(), "ready": d.parsed_deck.exists()} for d in deck_workspace.list_decks()],
                "slide_count": len(deck.slides), "question_count": len(questions), "topics": topics,
                "live_available": has_credentials() or generator_factory is not None,
                "model": os.getenv("GROQ_EDITOR_MODEL_NAME", "openai/gpt-oss-120b"),
                "policy": asdict(policy)}

    @app.get("/api/topics/{topic_id}")
    def topic(topic_id: int):
        if topic_id not in reports:
            raise HTTPException(404, "Topic not found")
        dossier = dossiers.get(topic_id)
        report = reports[topic_id].model_dump()
        report["report_text"] = display.report_text_for_display(report["report_text"], report.get("slide_refs"))
        return {"report": report,
                "questions": [m.question for m in dossier.matched_questions] if dossier else [q.question for q in unmatched],
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
        if generator_factory is None and not has_credentials():
            raise HTTPException(503, "Set GROQ_API_KEY (or GROQ_API_KEY_1, _2, ...) in the server .env to generate live suggestions")
        if body.kind == "repair" and not capabilities["layout_repair"]:
            raise HTTPException(422, "Layout repair needs the deck's .pptx and .pdf; this deck has only its " + ws.editable_format)
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
            output = folder / f"lecture.edited{SOURCE.suffix}"
            try:
                # Applier writes a new file; publish only after a full successful save.
                if is_pdf:
                    applied = pdf_applier.apply_edits_pdf(SOURCE, output, [c.edit for c in checked], deck, banner=banner)
                else:
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
        folder = store.directory / "exports" / key
        path = next(iter(sorted(folder.glob("lecture.edited.*"))), folder / "missing") if kind == "deck" else folder / "changes.json"
        if not path.exists():
            raise HTTPException(404, "Export not found")
        return FileResponse(path, filename=path.name)

    @app.get("/api/slides/{slide_id}/image")
    def slide_image(slide_id: int):
        if deck.get(slide_id) is None:
            raise HTTPException(404, "Slide not found")
        key = f"source-{slide_id}"
        content = cache.get(key)
        if content is None:
            with render_lock:
                if PDF is not None and ws.parse_source == PDF:
                    content = _render_pdf_page(PDF, slide_id)
                else:  # no PDF of this deck: draw the .pptx slide from its shapes
                    from importlib import import_module
                    from pptx import Presentation
                    index = import_module("01_deck_parsing.src.pptx_parser").slide_index_map(SOURCE)[slide_id]
                    prs = Presentation(str(SOURCE))
                    image = preview.render_slide(prs.slides[index], prs.slide_width, prs.slide_height, width_px=960)
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    content = buffer.getvalue()
                    image.close()
                cache.put(key, content)
        return Response(content, media_type="image/png")

    @app.get("/api/proposals/{key}/image")
    def proposal_image(key: str):
        row = find_proposal(key)
        edit = _edit(row)
        if deck.get(edit.after_slide_id) is None:
            raise HTTPException(422, "Invalid anchor slide")
        content = cache.get(key)
        if content is None and is_pdf:
            with render_lock:
                content = _render_pdf_page(pdf_applier.render_edit_page(SOURCE, edit, banner), 0)
                cache.put(key, content)
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
        if not capabilities["layout_repair"]:
            return []  # needs both the .pptx and its PDF
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


class DeckDispatcher:
    """One server, many decks. Each request goes to the app of the deck
    named by the `deck` cookie (or a `?deck=` query), else the default
    deck. A deck's app is built on first use and kept, so its run queue,
    approvals and caches are its own. The UI switches decks by setting
    the cookie and reloading."""

    def __init__(self, default_deck=None, state_root=None, generator_factory=None, include_saved=None):
        self.default_deck = default_deck
        self.state_root = Path(state_root) if state_root else None
        self.generator_factory = generator_factory
        self.include_saved = include_saved
        self.apps, self.lock = {}, threading.Lock()

    @staticmethod
    def _fingerprint(deck_id: str):
        """Changes when the pipeline rewrites this deck's outputs."""
        ws = deck_workspace.load(deck_id)
        files = [ws.parsed_deck, ws.gap_input, ws.reports_index, ws.questions, ws.editor_dir / "reviewed_suggestions.json"]
        return tuple(f.stat().st_mtime if f.exists() else None for f in files)

    def _deck_of(self, scope) -> str:
        from http.cookies import SimpleCookie
        from urllib.parse import parse_qs

        query = parse_qs(scope.get("query_string", b"").decode("latin-1")).get("deck")
        if query:
            return query[0]
        for name, value in scope.get("headers", []):
            if name == b"cookie":
                cookie = SimpleCookie()
                cookie.load(value.decode("latin-1"))
                if "deck" in cookie:
                    return cookie["deck"].value
        return self.default_deck or deck_workspace.resolve().deck_id

    def app_for(self, deck_id: str):
        with self.lock:
            known = {d.deck_id for d in deck_workspace.list_decks()}
            if deck_id not in known:
                raise LookupError(f"Unknown deck '{deck_id}'")
            fingerprint = self._fingerprint(deck_id)
            cached = self.apps.get(deck_id)
            # Rebuild when the pipeline has produced new outputs for this deck
            # (e.g. stage 4 finished), but never under a running job.
            if cached is None or (cached[1] != fingerprint and not cached[0].state.busy()):
                state = self.state_root / deck_id if self.state_root else None
                app = create_app(state_dir=state, generator_factory=self.generator_factory, deck_id=deck_id,
                                 include_saved=self.include_saved)
                self.apps[deck_id] = (app, fingerprint)
            return self.apps[deck_id][0]

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":  # sub-apps clean up through their own lifespans
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        try:
            deck_id = self._deck_of(scope)
            app = self.app_for(deck_id)
        except (LookupError, FileNotFoundError, ValueError, RuntimeError) as exc:
            decks = [{**d.summary(), "ready": d.parsed_deck.exists()} for d in deck_workspace.list_decks()]
            response = JSONResponse({"detail": str(exc), "decks": decks}, status_code=409)
            if isinstance(exc, LookupError):  # a stale cookie must not lock the user out
                response.delete_cookie("deck", path="/")
            await response(scope, receive, send)
            return
        await app(scope, receive, send)


def create_server(default_deck=None, state_root=None, generator_factory=None, include_saved=None):
    return DeckDispatcher(default_deck=default_deck, state_root=state_root, generator_factory=generator_factory,
                          include_saved=include_saved)
