"""Durable worker. No live call is made in explicitly selected rehearsal mode."""
from datetime import datetime, timezone
import json
import os
import signal
import socket
import threading
import time
import uuid
from sqlalchemy import select
from .analytics import calculate
from .config import Settings
from .db import Repository, Run, Portfolio, Record, Event
from .remote import AgentExecutor, NeedsReview, provider_failure
from geniagent.transport import APIError
from .reports import export_report
from .storage import make_store

SAMPLE_PRICES = {"AAPL": "200", "MSFT": "400", "NVDA": "120", "VTI": "250", "BND": "75", "VOO": "500", "AMZN": "180"}


def rehearsal_result(run, holdings):
    if run.kind == "question":
        return {"mode": "rehearsal", "explanation": "This is a local rehearsal response. In live mode, the OpenAI agent answers your question using the verified OCI analysis. No model was called.", "evidence": {"execution": "Local fixture; no OpenAI or OCI call"}}
    symbols = ({p["symbol"] for p in holdings} | {s for s, w in run.payload["weights"].items() if float(w) > 0}) - {"CASH"}
    if any(s not in SAMPLE_PRICES for s in symbols):
        raise ValueError("Rehearsal only includes AAPL, MSFT, NVDA, AMZN, VTI, VOO and BND. Use the supplied sample CSV.")
    quotes = {s: {"symbol": s, "price": SAMPLE_PRICES[s], "currency": "USD", "source": "Synthetic rehearsal fixture",
                  "asOf": "2026-01-01T00:00:00+00:00", "retrievedAt": "2026-01-01T00:00:00+00:00",
                  "instrumentType": "ETF" if s in ("VTI", "BND", "VOO") else "EQUITY"} for s in symbols}
    analysis = calculate(holdings, run.payload["weights"], quotes)
    largest = max(analysis["rows"], key=lambda r: r["weight"])
    return {"mode": "rehearsal", "analysis": analysis,
            "explanation": f"Synthetic rehearsal: {largest['symbol']} is the largest current position at {largest['weight']:.1f}%. The selected targets imply reallocating {analysis['allocationDrift']:.1f}% of portfolio value. The table shows the calculated changes, with fractional-share rounding retained as cash. This explanation is a fixture; no OpenAI or OCI call was made.",
            "evidence": {"execution": "Local fixture; no OpenAI or OCI call", "requestId": run.id, "commandVerified": False}}


def process(settings, repository, store, run, executor_factory=AgentExecutor):
    with repository.exclusive("run:" + run.id) as acquired:
        if acquired:
            _process(settings, repository, store, run, executor_factory)


def _process(settings, repository, store, run, executor_factory):
    stopped = threading.Event()
    def keepalive():
        while not stopped.wait(10):
            repository.update(run.id, heartbeat=time.time())
    heartbeat = threading.Thread(target=keepalive, daemon=True)
    heartbeat.start()
    try:
        portfolio = repository.portfolio(run.owner, run.portfolio_id)
        repository.event(run.id, "rehearsal_calculating" if settings.rehearsal else "checking_cloud_session")
        result = rehearsal_result(run, portfolio.holdings) if settings.rehearsal else executor_factory(settings, repository, run).execute(portfolio.holdings)
        # Deletion takes precedence even if it arrives while cloud work is in flight.
        repository.portfolio(run.owner, run.portfolio_id)
        if run.kind == "analysis":
            repository.event(run.id, "saving_reports")
            for format in ("html", "csv", "json"):
                body, content_type = export_report(result, format)
                store.put(f"portfolios/{run.portfolio_id}/runs/{run.id}/report.{format}", body, content_type)
        repository.finish(run.id, "completed", result=result)
    except LookupError:
        repository.finish(run.id, "cancelled")
    except NeedsReview as exc:
        repository.finish(run.id, "needs_review", error=str(exc))
    except APIError as exc:
        with repository.session() as db:
            stage = db.scalar(select(Event.phase).where(Event.run_id == run.id).order_by(Event.id.desc()).limit(1))
        failure = provider_failure(exc, stage or "checking_cloud_session")
        repository.save_state(run.id, "failure", {**failure, "retryable": False})
        repository.finish(run.id, "needs_review", error=failure["message"])
    except ValueError as exc:
        # Analytics validation contains no credentials. Provider SDK errors never reach this path.
        repository.finish(run.id, "failed", error=str(exc) if settings.rehearsal else "Market data or result validation failed. Inspect the saved run evidence before retrying.")
    except Exception:
        repository.finish(run.id, "needs_review", error="The service could not finish. Remote identifiers are retained; no automatic resubmission was made.")
    finally:
        stopped.set()
        heartbeat.join(timeout=2)


def cleanup_deleted(settings, repository, store):
    with repository.exclusive("deletion-cleanup") as acquired:
        if acquired:
            _cleanup_deleted(settings, repository, store)


def refresh_startup_status(settings, repository, executor_factory=AgentExecutor):
    """Refresh stalled startup evidence with GET only, including after a restart.

    Never replay a submission, create a replacement, or infer success from age.
    The run lock also excludes a concurrent user-requested reconciliation.
    """
    if settings.rehearsal:
        return
    with repository.session() as db:
        runs = list(db.scalars(select(Run).join(Portfolio, Portfolio.id == Run.portfolio_id).where(
            Portfolio.deleted.is_(False), Run.status == "needs_review", Run.kind == "analysis")))
    for run in runs:
        if (run.state.get("failure") or {}).get("code") not in ("OCI_STARTUP_TIMEOUT", "OCI_WORKSPACE_READY"):
            continue
        with repository.exclusive("run:" + run.id) as acquired:
            if not acquired:
                continue
            try:
                current = repository.run(run.owner, run.id)
            except LookupError:
                continue
            if current.status != "needs_review":
                continue
            state = current.state
            sid = state.get("session", {}).get("id")
            mapping = state.get(str(sid) + "-oci.json", {})
            if (not sid or not mapping.get("sandbox_id") or mapping.get("deleted")
                    or state.get("submission") or mapping.get("setup_submitted") or mapping.get("setup_command_id")
                    or mapping.get("executor_submitted") or mapping.get("executor_command_id")
                    or time.time() - mapping.get("observation", {}).get("checkedAt", 0) < 30):
                continue
            try:
                executor = executor_factory(settings, repository, current)
                observed = executor.runtime.observe(mapping["sandbox_id"], sid)
                code = {"RUNNING": "OCI_WORKSPACE_READY", "STOPPED": "OCI_SANDBOX_STOPPED",
                        "DELETED": "OCI_SANDBOX_STOPPED", "FAILED": "OCI_SANDBOX_STOPPED"}.get(observed["status"], "OCI_STARTUP_TIMEOUT")
                failure = provider_failure(executor.runtime.issue("Lifecycle refreshed", code, observed.get("requestId")), "provisioning_oci")
                # Do not overwrite a resume or deletion that arrived during the GET.
                with repository.session.begin() as db:
                    saved = db.scalar(select(Run).where(Run.id == run.id).with_for_update())
                    portfolio = db.get(Portfolio, run.portfolio_id)
                    if saved.status == "needs_review" and not portfolio.deleted:
                        saved.state = {**saved.state, "failure": {**failure, "retryable": False}}
                        saved.error = failure["message"]
            except Exception:
                # Keep last confirmed evidence; a failed read is not a stopped sandbox.
                continue


def _cleanup_deleted(settings, repository, store):
    with repository.session() as db:
        ids = [p.id for p in db.scalars(select(Portfolio).where(Portfolio.deleted.is_(True), Portfolio.cleanup.in_(["pending", "retrying"])))]
    for pid in ids:
        try:
            with repository.session() as db:
                runs = list(db.scalars(select(Run).where(Run.portfolio_id == pid)))
                if any(r.status in ("running", "recovering") and r.heartbeat > time.time() - 90 for r in runs):
                    continue
            for run in runs:
                if settings.rehearsal or run.kind != "analysis":
                    continue
                executor = AgentExecutor(settings, repository, run)
                state = repository.state(run.id)
                session = state.get("session", {})
                if not session:
                    if state.get("session_creating") and state["session_creating"].get("outcome") != "rejected":
                        raise NeedsReview("Unknown remote session; operator reconciliation required.")
                    continue
                sid = session["id"]
                mapping = state.get(sid + "-oci.json", {})
                if mapping and not mapping.get("sandbox_id"):
                    raise NeedsReview("Unknown sandbox creation; operator reconciliation required.")
                if mapping:
                    errors = executor.runtime.cleanup(sid)
                    if errors:
                        raise NeedsReview("Sandbox deletion is pending.")
                    # An accepted DELETE is not confirmed physical removal.
                    from geniagent.transport import APIError
                    try:
                        executor.runtime.call("get_sandbox", mapping["sandbox_id"])
                    except APIError as exc:
                        if exc.status != 404:
                            raise
                    else:
                        raise NeedsReview("Sandbox deletion is still pending.")
                from geniagent.transport import APIError
                try:
                    executor.client.request("DELETE", f"/agents/sessions/{sid}")
                except APIError as exc:
                    if exc.status != 404:
                        raise
                try:
                    executor.client.request("GET", f"/agents/sessions/{sid}")
                except APIError as exc:
                    if exc.status != 404:
                        raise
                else:
                    raise NeedsReview("Session deletion is still pending.")
            store.delete_prefix(f"portfolios/{pid}/")
            with repository.session.begin() as db:
                portfolio = db.get(Portfolio, pid)
                portfolio.holdings, portfolio.name, portfolio.cleanup = [], "Deleted portfolio", "completed"
                for run in db.scalars(select(Run).where(Run.portfolio_id == pid)):
                    for event in db.scalars(select(Event).where(Event.run_id == run.id)):
                        db.delete(event)
                    db.delete(run)
        except Exception:
            with repository.session.begin() as db:
                db.get(Portfolio, pid).cleanup = "retrying"


def main():
    settings = Settings.load()
    # The legacy runtime reads this fixed subset. Values stay in process memory;
    # only the separate executor credential is sent to OCI as a command environment value.
    if not settings.rehearsal:
        for name in ("OPENAI_API_KEY", "OPENAI_EXECUTOR_API_KEY", "OCI_SANDBOX_PROJECT_ID"):
            os.environ[name] = settings.get(name)
    repository = Repository(settings.get("DATABASE_URL"))
    repository.initialize()
    store = make_store(settings)
    worker_id = "worker:" + str(uuid.uuid4())
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    def pulse():
        while not stop.is_set():
            with repository.session.begin() as db:
                db.merge(Record(id=worker_id, value={"at": time.time()}))
            stop.wait(10)
    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    last_cleanup = 0
    while not stop.is_set():
        if time.monotonic() - last_cleanup > 30:
            refresh_startup_status(settings, repository)
            cleanup_deleted(settings, repository, store)
            last_cleanup = time.monotonic()
        run = repository.claim()
        if run:
            process(settings, repository, store, run)
        else:
            stop.wait(1)
    thread.join(timeout=2)


if __name__ == "__main__":
    main()
