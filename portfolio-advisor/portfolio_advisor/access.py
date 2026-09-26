"""Named-user authorization, independent of Oracle authentication and portfolio ownership."""
import re
import time
import uuid
from contextlib import contextmanager
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from .db import AccessAudit, AppUser, Login, Record, Run, Portfolio

DENIED = "Your account does not have access. Ask a Portfolio Advisor administrator to enable your Oracle sign-in email."


def email_address(value):
    if not isinstance(value, str):
        raise ValueError("Enter an Oracle sign-in email.")
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", value):
        raise ValueError("Enter a valid Oracle sign-in email.")
    return value


def identity_email(claims):
    # An opaque Oracle username must not hide a usable email claim or prevent
    # the callback from requesting the signed-in user's profile.
    for key in ("email", "preferred_username", "user_name", "sub"):
        try:
            return email_address(claims.get(key))
        except ValueError:
            continue
    return None


def user_fields(data, allow_missing_email=False):
    if not isinstance(data, dict) or set(data) - {"email", "name", "role", "active", "version"}:
        raise ValueError("Unexpected access fields.")
    email = None if allow_missing_email and data.get("email") is None else email_address(data.get("email"))
    name = data.get("name", "")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or any(ord(c) < 32 for c in name):
        raise ValueError("Use a display name of 1–120 printable characters.")
    role, active = data.get("role", "member"), data.get("active", True)
    if role not in ("admin", "member") or not isinstance(active, bool):
        raise ValueError("Choose an admin or member role and an access status.")
    return {"email": email, "name": name.strip(), "role": role, "active": active}


def user_json(user):
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role,
            "active": user.active, "version": user.version, "created": user.created,
            "updated": user.updated, "lastLogin": user.last_login, "hasSignedIn": bool(user.owner)}


def access_snapshot(user):
    return {key: getattr(user, key) for key in ("email", "name", "role", "active")}


class AccessControl:
    def __init__(self, repository):
        self.repo = repository

    @contextmanager
    def transaction(self, actor=None):
        # Serialize role/status changes, including concurrent last-admin updates.
        with self.repo.session.begin() as db:
            policy = db.scalar(select(Record).where(Record.id == "access-policy").with_for_update())
            if policy is None:
                raise RuntimeError("Access policy storage is unavailable.")
            if actor:
                self.require_admin(db, actor)
            yield db, policy

    @staticmethod
    def require_admin(db, owner):
        user = db.scalar(select(AppUser).where(AppUser.owner == owner, AppUser.active.is_(True)))
        if not user or user.role != "admin":
            raise HTTPException(403, "Administrator access is required.")
        return user

    @staticmethod
    def audit(db, actor, user, action, before=None):
        db.add(AccessAudit(actor=actor, target=user.id, action=action, before=before,
                           after=access_snapshot(user), created=time.time()))

    def bootstrap(self, email, *, owner=None, name="Initial administrator"):
        """Operator-configured identity only. A consumed bootstrap never grants again."""
        email = email_address(email)
        with self.transaction() as (db, policy):
            if policy.value.get("bootstrapped"):
                return
            if db.scalar(select(func.count()).select_from(AppUser)):
                raise RuntimeError("Access bootstrap requires an empty user directory.")
            now = time.time()
            user = AppUser(id=str(uuid.uuid4()), email=email, name=name, owner=owner,
                           role="admin", active=True, created=now, updated=now)
            db.add(user)
            self.audit(db, "configured-bootstrap", user, "bootstrap")
            policy.value = {"bootstrapped": True}

    def bootstrap_from_run(self, rid):
        """Bind the operator-selected, already authenticated portfolio owner.

        The run is selected from that user's private signed-in UI. This is a
        deployment setting, never an HTTP parameter or a first-login shortcut.
        """
        if str(uuid.UUID(rid)) != rid:
            raise ValueError("Use the exact saved run identifier for the approved administrator.")
        with self.transaction() as (db, policy):
            if policy.value.get("bootstrapped"):
                return
            if db.scalar(select(func.count()).select_from(AppUser)):
                raise RuntimeError("Access bootstrap requires an empty user directory.")
            run = db.scalar(select(Run).join(Portfolio, Portfolio.id == Run.portfolio_id).where(
                Run.id == rid, Portfolio.deleted.is_(False)))
            login = db.scalar(select(Login).where(Login.owner == run.owner, Login.expires > time.time())) if run else None
            if not login:
                raise RuntimeError("The approved bootstrap account has no current authenticated session. Sign in before activating access controls.")
            now = time.time()
            name = login.name if login.name != "Portfolio owner" else "Initial administrator"
            user = AppUser(id=str(uuid.uuid4()), email=None, owner=run.owner, name=name,
                           role="admin", active=True, created=now, updated=now)
            db.add(user)
            self.audit(db, "configured-bootstrap", user, "bootstrap")
            policy.value = {"bootstrapped": True}

    def active_user(self, owner):
        with self.repo.session() as db:
            user = db.scalar(select(AppUser).where(AppUser.owner == owner, AppUser.active.is_(True)))
            if not user:
                raise HTTPException(403, DENIED)
            return user

    def authorize_sso(self, owner, claims):
        # Claims come exclusively from Authlib's validated Oracle ID token.
        # Oracle may omit email_verified; an explicit false is never accepted.
        email = identity_email(claims)
        with self.transaction() as (db, _):
            bound = db.scalar(select(AppUser).where(AppUser.owner == owner))
            if bound:
                if not bound.active:
                    raise HTTPException(403, DENIED)
                if bound.email is None and email and claims.get("email_verified") is not False:
                    if not db.scalar(select(AppUser).where(AppUser.email == email)):
                        bound.email = email
                        bound.version += 1
                        bound.updated = time.time()
                bound.last_login = time.time()
                return bound
            if not email or claims.get("email_verified") is False:
                raise HTTPException(403, DENIED)
            user = db.scalar(select(AppUser).where(AppUser.email == email))
            if not user or not user.active or user.owner not in (None, owner):
                raise HTTPException(403, DENIED)
            user.owner, user.last_login = owner, time.time()
            return user

    def list_users(self, actor, query="", offset=0):
        with self.repo.session() as db:
            self.require_admin(db, actor)
            where = []
            if query:
                where.append(AppUser.email.contains(query.strip().lower(), autoescape=True)
                             | func.lower(AppUser.name).contains(query.strip().lower(), autoescape=True))
            total = db.scalar(select(func.count()).select_from(AppUser).where(*where))
            users = db.scalars(select(AppUser).where(*where).order_by(AppUser.email).offset(offset).limit(50))
            return {"users": [user_json(user) for user in users], "total": total, "offset": offset}

    def create(self, actor, data):
        fields = user_fields(data)
        if "version" in data:
            raise ValueError("A new user must not include a version.")
        try:
            with self.transaction(actor) as (db, _):
                admin = self.require_admin(db, actor)
                now = time.time()
                user = AppUser(id=str(uuid.uuid4()), **fields, created=now, updated=now, version=1)
                db.add(user)
                db.flush()
                self.audit(db, admin.id, user, "grant")
                return user_json(user)
        except IntegrityError:
            raise HTTPException(409, "This email is already listed. Edit its existing access instead.") from None

    def update(self, actor, uid, data):
        fields = user_fields(data, allow_missing_email=True)
        if type(data.get("version")) is not int:
            raise ValueError("Refresh the user list before editing access.")
        try:
            with self.transaction(actor) as (db, _):
                admin = self.require_admin(db, actor)
                user = db.get(AppUser, uid)
                if not user:
                    raise LookupError()
                if user.version != data["version"]:
                    raise HTTPException(409, "This user was changed by another administrator. Refresh before saving.")
                if fields["email"] != user.email:
                    raise ValueError("Sign-in email cannot be changed. Disable this entry and grant the new email separately.")
                if user.id == admin.id and (not fields["active"] or fields["role"] != "admin"):
                    raise HTTPException(409, "You cannot remove your own administrator access.")
                if user.active and user.role == "admin" and (not fields["active"] or fields["role"] != "admin"):
                    count = db.scalar(select(func.count()).select_from(AppUser).where(AppUser.active.is_(True), AppUser.role == "admin"))
                    if count <= 1:
                        raise HTTPException(409, "Keep at least one active administrator.")
                before = access_snapshot(user)
                for key, value in fields.items():
                    setattr(user, key, value)
                user.version, user.updated = user.version + 1, time.time()
                if not user.active and user.owner:
                    db.execute(delete(Login).where(Login.owner == user.owner))
                self.audit(db, admin.id, user, "update", before)
                return user_json(user)
        except IntegrityError:
            raise HTTPException(409, "The access entry conflicts with an existing user.") from None

    def history(self, actor, offset=0):
        with self.repo.session() as db:
            self.require_admin(db, actor)
            rows = db.scalars(select(AccessAudit).order_by(AccessAudit.id.desc()).offset(offset).limit(50))
            names = {u.id: u.email or u.name for u in db.scalars(select(AppUser))}
            return {"events": [{"id": row.id, "actor": names.get(row.actor, row.actor),
                    "target": row.after["email"] or row.after["name"], "action": row.action, "before": row.before,
                               "after": row.after, "created": row.created} for row in rows],
                    "total": db.scalar(select(func.count()).select_from(AccessAudit)), "offset": offset}
