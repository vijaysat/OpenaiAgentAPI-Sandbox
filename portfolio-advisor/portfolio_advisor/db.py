"""Durable jobs, owner isolation and restart-safe execution journals."""
from contextlib import contextmanager
import hashlib
import json
import time
import uuid
from sqlalchemy import JSON, Boolean, Column, Float, Integer, String, Text, UniqueConstraint, create_engine, select, text
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class Portfolio(Base):
    __tablename__ = "portfolios"
    id = Column(String, primary_key=True)
    owner = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    holdings = Column(JSON, nullable=False)
    created = Column(Float, nullable=False)
    deleted = Column(Boolean, default=False, nullable=False)
    cleanup = Column(String, default="none", nullable=False)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("owner", "request_key"),)
    id = Column(String, primary_key=True)
    owner = Column(String, nullable=False, index=True)
    portfolio_id = Column(String, nullable=False, index=True)
    request_key = Column(String, nullable=False)
    fingerprint = Column(String, nullable=False)
    active_owner = Column(String, unique=True, nullable=True)
    status = Column(String, nullable=False, index=True)
    kind = Column(String, default="analysis", nullable=False)
    parent_id = Column(String, nullable=True)
    payload = Column(JSON, nullable=False)
    state = Column(JSON, default=dict, nullable=False)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created = Column(Float, nullable=False)
    heartbeat = Column(Float, nullable=False)


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, nullable=False, index=True)
    phase = Column(String, nullable=False)
    created = Column(Float, nullable=False)


class Record(Base):
    __tablename__ = "records"
    id = Column(String, primary_key=True)
    value = Column(JSON, nullable=False)


class Login(Base):
    __tablename__ = "logins"
    id = Column(String, primary_key=True)
    owner = Column(String, nullable=False)
    name = Column(String, nullable=False)
    csrf = Column(String, nullable=False)
    expires = Column(Float, nullable=False)


class AppUser(Base):
    __tablename__ = "app_users"
    id = Column(String, primary_key=True)
    email = Column(String(254), nullable=True, unique=True)
    name = Column(String(120), nullable=False)
    owner = Column(String, nullable=True, unique=True)
    role = Column(String, nullable=False, default="member")
    active = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    created = Column(Float, nullable=False)
    updated = Column(Float, nullable=False)
    last_login = Column(Float, nullable=True)


class AccessAudit(Base):
    __tablename__ = "access_audit"
    id = Column(Integer, primary_key=True, autoincrement=True)
    actor = Column(String, nullable=False)
    target = Column(String, nullable=False)
    action = Column(String, nullable=False)
    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=False)
    created = Column(Float, nullable=False)


class Repository:
    @contextmanager
    def exclusive(self, name):
        """Connection-scoped locks survive transaction commits and release on process death."""
        if self.engine.dialect.name != "postgresql":
            yield True  # SQLite is used only in single-worker unit fixtures.
            return
        key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
        with self.engine.connect() as connection:
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            connection.commit()
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    connection.commit()

    def __init__(self, url):
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        self.engine = create_engine(url, pool_pre_ping=True,
                                    connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        # Version 2 is additive: keep portfolios, jobs and owner identities unchanged.
        # Serialize API/worker startup so the new tables cannot race on PostgreSQL.
        with self.engine.begin() as connection:
            if self.engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(714839026)"))
            Base.metadata.create_all(connection)
            with self.session(bind=connection) as db:
                self._initialize_records(db)
                db.commit()

    def _initialize_records(self, db):
        if not db.get(Record, "schema"):
            db.add(Record(id="schema", value={"version": 2}))
        elif db.get(Record, "schema").value not in ({"version": 1}, {"version": 2}):
            raise RuntimeError("Unsupported Portfolio Advisor schema version.")
        else:
            db.get(Record, "schema").value = {"version": 2}
        if not db.get(Record, "agent"):
            db.add(Record(id="agent", value={}))
        if not db.get(Record, "bootstrap-session"):
            db.add(Record(id="bootstrap-session", value={}))
        if not db.get(Record, "access-policy"):
            db.add(Record(id="access-policy", value={}))

    def portfolio(self, owner, pid):
        with self.session() as db:
            value = db.scalar(select(Portfolio).where(Portfolio.id == pid, Portfolio.owner == owner, Portfolio.deleted.is_(False)))
            if value is None:
                raise LookupError("Portfolio not found.")
            return value

    def run(self, owner, rid):
        with self.session() as db:
            value = db.scalar(select(Run).join(Portfolio, Portfolio.id == Run.portfolio_id).where(
                Run.id == rid, Run.owner == owner, Portfolio.deleted.is_(False)))
            if value is None:
                raise LookupError("Run not found.")
            return value

    def submit(self, owner, pid, request_key, payload, kind="analysis", parent_id=None):
        try:
            valid_key = isinstance(request_key, str) and str(uuid.UUID(request_key)) == request_key
        except (ValueError, TypeError, AttributeError):
            valid_key = False
        if not valid_key:
            raise ValueError("Use a UUID idempotency key.")
        self.portfolio(owner, pid)
        fingerprint = hashlib.sha256(json.dumps([pid, kind, parent_id, payload], sort_keys=True).encode()).hexdigest()
        from sqlalchemy.exc import IntegrityError
        try:
            with self.session.begin() as db:
                # Lock the portfolio against deletion while accepting a job.
                p = db.scalar(select(Portfolio).where(Portfolio.id == pid).with_for_update())
                if p.deleted:
                    raise LookupError("Portfolio not found.")
                previous = db.scalar(select(Run).where(Run.owner == owner, Run.request_key == request_key))
                if previous:
                    if previous.fingerprint != fingerprint:
                        raise ValueError("Idempotency key belongs to a different request.")
                    return previous
                if db.scalar(select(Run).where(Run.active_owner == owner)):
                    raise RuntimeError("An analysis is already active for this user.")
                run = Run(id=str(uuid.uuid4()), owner=owner, portfolio_id=pid, request_key=request_key,
                          fingerprint=fingerprint, active_owner=owner, status="queued", kind=kind,
                          parent_id=parent_id, payload=payload, state={}, created=time.time(), heartbeat=time.time())
                db.add(run)
            self.event(run.id, "queued")
            return run
        except IntegrityError:
            with self.session() as db:
                previous = db.scalar(select(Run).where(Run.owner == owner, Run.request_key == request_key))
                if previous and previous.fingerprint == fingerprint:
                    return previous
            raise RuntimeError("Another request is active; refresh its status.") from None

    def claim(self):
        with self.session.begin() as db:
            run = db.scalar(select(Run).join(Portfolio, Portfolio.id == Run.portfolio_id).where(
                Portfolio.deleted.is_(False),
                (Run.status == "queued") | (Run.status.in_(["running", "recovering"]) & (Run.heartbeat < time.time() - 90))
            ).order_by(Run.created).with_for_update(skip_locked=True).limit(1))
            if not run:
                return None
            run.status = "running" if run.status == "queued" else "recovering"
            run.heartbeat = time.time()
            return run

    def update(self, rid, **fields):
        with self.session.begin() as db:
            run = db.scalar(select(Run).where(Run.id == rid).with_for_update())
            if not run:
                return
            for key, value in fields.items():
                setattr(run, key, value)

    def resume(self, owner, rid):
        self.run(owner, rid)
        from sqlalchemy.exc import IntegrityError
        try:
            with self.session.begin() as db:
                run = db.scalar(select(Run).where(Run.id == rid).with_for_update())
                if run.status != "needs_review":
                    raise ValueError("Only a run needing review can be reconciled.")
                run.status, run.active_owner, run.error = "queued", owner, None
            self.event(rid, "reconciliation_requested")
        except IntegrityError:
            raise RuntimeError("Another analysis is active.") from None
        return self.run(owner, rid)

    def state(self, rid):
        with self.session() as db:
            run = db.get(Run, rid)
            return dict(run.state)

    def save_state(self, rid, name, value):
        with self.session.begin() as db:
            run = db.scalar(select(Run).where(Run.id == rid).with_for_update())
            run.state = {**run.state, name: value}

    def event(self, rid, phase):
        with self.session.begin() as db:
            db.add(Event(run_id=rid, phase=phase, created=time.time()))

    def finish(self, rid, status, result=None, error=None):
        with self.session.begin() as db:
            run = db.scalar(select(Run).where(Run.id == rid).with_for_update())
            portfolio = db.get(Portfolio, run.portfolio_id)
            run.status = "cancelled" if portfolio.deleted else status
            run.result = None if portfolio.deleted else result
            run.error = error
            run.active_owner = None
            run.heartbeat = time.time()
        self.event(rid, run.status)

    def delete(self, owner, pid):
        with self.session.begin() as db:
            portfolio = db.scalar(select(Portfolio).where(Portfolio.id == pid, Portfolio.owner == owner).with_for_update())
            if not portfolio:
                raise LookupError("Portfolio not found.")
            portfolio.deleted = True
            portfolio.cleanup = "pending"
            for run in db.scalars(select(Run).where(Run.portfolio_id == pid)):
                if run.status == "queued":
                    run.status, run.active_owner = "cancelled", None


class DatabaseState:
    """The existing OCI runtime's state interface, backed by PostgreSQL."""
    def __init__(self, repository, rid, project):
        self.repository, self.rid, self.project = repository, rid, project

    def load(self, name):
        return self.repository.state(self.rid).get(name, {})

    def save(self, name, value):
        self.repository.save_state(self.rid, name, value)
