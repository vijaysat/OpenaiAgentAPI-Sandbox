import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
import uuid
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from urllib.parse import urlsplit

from .analytics import parse_csv, validate_targets, TEMPLATES
from .auth import Authentication
from .config import Settings
from .db import Repository, Portfolio, Run, Event, Record
from .reports import export_report
from .storage import make_store
from .proxy import OCIGatewayMiddleware


def run_json(run):
    state = run.state or {}
    failure = state.get("failure") or {}
    attempt = state.get("session_creating") or {}
    session_id = state.get("session", {}).get("id")
    mapping = state.get(str(session_id) + "-oci.json", {})
    observation = mapping.get("observation", {})
    # A terminal, never-started workspace is different from an uncertain command.
    ended_before_execution = (run.kind == "analysis" and observation.get("status") in ("STOPPED", "DELETED", "FAILED")
                              and not state.get("submission") and not mapping.get("setup_submitted")
                              and not mapping.get("setup_command_id") and not mapping.get("executor_submitted")
                              and not mapping.get("executor_command_id"))
    operator_required = (bool(attempt and not (run.state or {}).get("session") and attempt.get("outcome") != "rejected")
                         or failure.get("code") in ("OCI_SETUP_UNCONFIRMED", "OCI_SANDBOX_STOPPED"))
    return {"id": run.id, "portfolioId": run.portfolio_id, "status": run.status,
            "kind": run.kind, "parentId": run.parent_id, "created": run.created,
            "error": run.error, "result": run.result,
            "failure": {k: failure.get(k) for k in ("stage", "httpStatus", "requestId", "code")} if failure else None,
            "workspace": {k: observation.get(k) for k in ("status", "checkedAt", "createdAt", "expiresAt")} if observation else None,
            "recovery": "workspace_ended" if ended_before_execution else "operator_required" if operator_required else "retry_rejected" if attempt.get("outcome") == "rejected" else "reconcile"}


def create_app(settings=None, repository=None, store=None):
    settings = settings or Settings.load()
    repository = repository or Repository(settings.get("DATABASE_URL"))
    store = store or make_store(settings)
    repository.initialize()
    auth = Authentication(settings, repository)
    app = FastAPI(title="Portfolio Advisor", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.repository, app.state.auth, app.state.settings = repository, auth, settings
    app.add_middleware(SessionMiddleware, secret_key=settings.get("AUTH_SESSION_SECRET"),
                       session_cookie="pa_oidc", https_only=settings.origin.startswith("https://"),
                       same_site="lax", max_age=600, path=settings.base_path or "/")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.origin).hostname, "testserver"] if settings.rehearsal else [urlsplit(settings.origin).hostname])

    @app.middleware("http")
    async def security(request, next_handler):
        # Bound both ordinary JSON and multipart requests before Starlette parses them.
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Invalid content length."}, status_code=400)
            if not 0 < length <= 1_100_000:
                return JSONResponse({"detail": "Request must be no larger than 1 MiB plus upload metadata."}, status_code=413)
        response = await next_handler(request)
        response.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                                 "Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                 "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        return response

    @app.exception_handler(LookupError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Resource not found."}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RuntimeError)
    async def conflict(request, exc):
        return JSONResponse({"detail": "Another operation is active or needs review. Refresh the run status."}, status_code=409)

    auth.register(app)

    @app.get("/healthz")
    def health():
        with repository.session() as db:
            db.execute(select(Record).limit(1))
            heartbeats = [row.value.get("at", 0) for row in db.scalars(select(Record).where(Record.id.like("worker:%")))]
        return {"api": "ok", "worker": "ok" if any(t > time.time() - 45 for t in heartbeats) else "unavailable",
                "cloudVerified": False}

    @app.get("/api/config")
    def config():
        return {"mode": "rehearsal" if settings.rehearsal else "live", "loginUrl": settings.base_path + "/auth/login"}

    @app.get("/api/me")
    def me(request: Request):
        user = auth.current(request)
        return {"name": user.name, "csrf": user.csrf, "id": user.user_id, "email": user.email, "role": user.role}

    @app.get("/api/v1/admin/users")
    def access_users(request: Request, q: str = Query("", max_length=120), offset: int = Query(0, ge=0, le=100000)):
        return auth.access.list_users(auth.admin(request).owner, q, offset)

    @app.post("/api/v1/admin/users", status_code=201)
    async def grant_access(request: Request):
        actor = auth.admin(request)
        return auth.access.create(actor.owner, await request.json())

    @app.patch("/api/v1/admin/users/{uid}")
    async def edit_access(request: Request, uid: str):
        actor = auth.admin(request)
        return auth.access.update(actor.owner, uid, await request.json())

    @app.get("/api/v1/admin/access-history")
    def access_history(request: Request, offset: int = Query(0, ge=0, le=100000)):
        return auth.access.history(auth.admin(request).owner, offset)

    @app.get("/api/v1/templates")
    def templates(request: Request):
        auth.current(request)
        return TEMPLATES

    @app.get("/api/v1/portfolios")
    def portfolios(request: Request):
        user = auth.current(request)
        with repository.session() as db:
            return [{"id": p.id, "name": p.name, "created": p.created, "holdings": p.holdings}
                    for p in db.scalars(select(Portfolio).where(Portfolio.owner == user.owner, Portfolio.deleted.is_(False)).order_by(Portfolio.created.desc()))]

    @app.post("/api/v1/portfolios/preview")
    async def preview(request: Request, file: UploadFile = File(...)):
        auth.current(request)
        return {"holdings": parse_csv(await file.read(1_048_577))}

    @app.post("/api/v1/portfolios", status_code=201)
    async def upload(request: Request, file: UploadFile = File(...), name: str = Form(...)):
        user = auth.current(request)
        name = name.strip()
        if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
            raise ValueError("Portfolio name must contain 1–80 printable characters.")
        body = await file.read(1_048_577)
        holdings = parse_csv(body)
        pid = str(uuid.uuid4())
        # The durable DB record precedes object upload; upload failures mark it for cleanup.
        with repository.session.begin() as db:
            db.add(Portfolio(id=pid, owner=user.owner, name=name, holdings=holdings, created=time.time()))
        try:
            store.put(f"portfolios/{pid}/input.csv", body, "text/csv")
        except Exception:
            repository.delete(user.owner, pid)
            raise HTTPException(503, "Input storage failed. Cleanup is queued; upload again after recovery.") from None
        return {"id": pid, "name": name, "holdings": holdings}

    @app.get("/api/v1/portfolios/{pid}")
    def portfolio(request: Request, pid: str):
        p = repository.portfolio(auth.current(request).owner, pid)
        return {"id": p.id, "name": p.name, "holdings": p.holdings}

    @app.delete("/api/v1/portfolios/{pid}", status_code=202)
    def delete(request: Request, pid: str):
        repository.delete(auth.current(request).owner, pid)
        return {"status": "deletion_pending", "message": "Access revoked; stored data and remote resources are being removed."}

    @app.get("/api/v1/deletions")
    def deletions(request: Request):
        owner = auth.current(request).owner
        with repository.session() as db:
            return [{"id": p.id, "status": p.cleanup} for p in db.scalars(select(Portfolio).where(Portfolio.owner == owner, Portfolio.deleted.is_(True)))]

    @app.post("/api/v1/portfolios/{pid}/analyses", status_code=202)
    async def analyze(request: Request, pid: str):
        user = auth.current(request)
        data = await request.json()
        if not isinstance(data, dict) or data.get("processingAcknowledged") is not True:
            raise ValueError("Acknowledge OpenAI and OCI processing before running an analysis.")
        weights = {s: str(w) for s, w in validate_targets(data.get("weights")).items()}
        try:
            run = repository.submit(user.owner, pid, request.headers.get("idempotency-key"), {"weights": weights})
        except (AttributeError, TypeError):
            raise ValueError("Use a UUID idempotency key.") from None
        return run_json(run)

    @app.get("/api/v1/portfolios/{pid}/runs")
    def runs(request: Request, pid: str):
        user = auth.current(request)
        repository.portfolio(user.owner, pid)
        with repository.session() as db:
            return [run_json(r) for r in db.scalars(select(Run).where(Run.portfolio_id == pid, Run.owner == user.owner).order_by(Run.created.desc()))]

    @app.get("/api/v1/runs/{rid}")
    def run(request: Request, rid: str):
        return run_json(repository.run(auth.current(request).owner, rid))

    @app.get("/api/v1/runs/{rid}/events")
    async def events(request: Request, rid: str, after: int = 0):
        owner = auth.current(request).owner
        repository.run(owner, rid)
        async def stream():
            cursor = after
            while not await request.is_disconnected():
                try:
                    auth.current(request)
                    row = repository.run(owner, rid)
                except (LookupError, HTTPException):
                    return
                with repository.session() as db:
                    records = list(db.scalars(select(Event).where(Event.run_id == rid, Event.id > cursor).order_by(Event.id)))
                for event in records:
                    cursor = event.id
                    yield f"id: {cursor}\ndata: {json.dumps({'phase': event.phase, 'at': event.created})}\n\n"
                if row.status not in ("queued", "running", "recovering"):
                    yield f"event: finished\ndata: {json.dumps({'status': row.status})}\n\n"
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/v1/runs/{rid}/resume", status_code=202)
    def resume(request: Request, rid: str):
        owner = auth.current(request).owner
        if run_json(repository.run(owner, rid))["recovery"] == "workspace_ended":
            raise HTTPException(409, "This workspace has stopped. Start a new analysis; the saved run remains in your history.")
        return run_json(repository.resume(owner, rid))

    @app.post("/api/v1/runs/{rid}/questions", status_code=202)
    async def question(request: Request, rid: str):
        user = auth.current(request)
        parent = repository.run(user.owner, rid)
        if parent.status != "completed" or parent.kind != "analysis":
            raise ValueError("Select a completed analysis before asking a question.")
        data = await request.json()
        text = data.get("question", "") if isinstance(data, dict) else ""
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise ValueError("Questions must contain 1–2000 characters.")
        return run_json(repository.submit(user.owner, parent.portfolio_id, request.headers.get("idempotency-key"),
                                         {"question": text.strip()}, "question", parent.id))

    @app.get("/api/v1/runs/{rid}/artifacts/{format}")
    def artifact(request: Request, rid: str, format: str):
        row = repository.run(auth.current(request).owner, rid)
        if row.status != "completed" or row.kind != "analysis" or format not in ("html", "csv", "json"):
            raise HTTPException(404)
        # Read the durable artifact, not an unverified assistant response.
        try:
            body = store.get(f"portfolios/{row.portfolio_id}/runs/{rid}/report.{format}")
        except Exception:
            raise HTTPException(503, "Report storage is temporarily unavailable.") from None
        media = {"html": "text/html", "csv": "text/csv", "json": "application/json"}[format]
        return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="portfolio-review-{rid}.{format}"'})

    dist = Path(__file__).resolve().parents[1] / "frontend/dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        @app.get("/")
        def index():
            return FileResponse(dist / "index.html")
    if settings.base_path:
        parent = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        parent.mount(settings.base_path, app)
        if settings.get('APP_ENV') == 'hosted':
            parent.add_middleware(OCIGatewayMiddleware, origin=settings.origin, prefix=settings.base_path)
        return parent
    return app
