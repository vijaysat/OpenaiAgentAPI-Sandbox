import hashlib
import logging
import secrets
import time
import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit
from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature
from starlette.responses import RedirectResponse, JSONResponse
from starlette.requests import Request
from sqlalchemy import select
from .db import Login, AppUser
from .access import AccessControl, identity_email


class Authentication:
    def __init__(self, settings, repository):
        self.settings, self.repository = settings, repository
        self.access = AccessControl(repository)
        if repository:
            if settings.rehearsal:
                self.access.bootstrap("presenter@example.test", owner="rehearsal-user", name="Demo presenter")
            elif settings.get("AUTH_BOOTSTRAP_ADMIN_RUN_ID"):
                if settings.get("AUTH_BOOTSTRAP_ADMIN_EMAIL"):
                    raise ValueError("Configure only one administrator bootstrap identity.")
                self.access.bootstrap_from_run(settings.get("AUTH_BOOTSTRAP_ADMIN_RUN_ID"))
            elif settings.get("AUTH_BOOTSTRAP_ADMIN_EMAIL"):
                self.access.bootstrap(settings.get("AUTH_BOOTSTRAP_ADMIN_EMAIL"))
        self.signer = URLSafeTimedSerializer(settings.get("AUTH_SESSION_SECRET"), salt="portfolio-session-v1")
        self.oauth = OAuth()
        if not settings.rehearsal:
            self.oauth.register("oracle", client_id=settings.get("SSO_CLIENT_ID"),
                                client_secret=settings.get("SSO_CLIENT_SECRET"),
                                server_metadata_url=settings.get("SSO_DISCOVERY_URL") or
                                    settings.get("SSO_ISSUER_URL").rstrip("/") + "/.well-known/openid-configuration",
                                client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"})

    def issue(self, owner, name, response):
        # Test-only direct identities cannot bypass authorization in live mode.
        if self.settings.rehearsal:
            with self.repository.session.begin() as db:
                if not db.scalar(select(AppUser).where(AppUser.owner == owner)):
                    now = time.time()
                    db.add(AppUser(id=str(uuid.uuid4()), owner=owner, email=hashlib.sha256(owner.encode()).hexdigest()+"@example.test",
                                   name=name, role="member", active=True, created=now, updated=now))
        self.access.active_user(owner)
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        with self.access.transaction() as (db, _):
            user = db.scalar(select(AppUser).where(AppUser.owner == owner, AppUser.active.is_(True)))
            if not user:
                raise HTTPException(403, "Your account does not have access. Ask an administrator to enable it.")
            user.last_login = time.time()
            db.add(Login(id=hashlib.sha256(token.encode()).hexdigest(), owner=owner, name=name,
                         csrf=csrf, expires=time.time() + 8 * 3600))
        response.set_cookie("pa_session", self.signer.dumps(token), httponly=True,
                            secure=self.settings.origin.startswith("https://"), samesite="lax",
                            max_age=8 * 3600, path=self.settings.base_path or "/")
        return response

    def current(self, request):
        try:
            token = self.signer.loads(request.cookies.get("pa_session", ""), max_age=8 * 3600)
            digest = hashlib.sha256(token.encode()).hexdigest()
        except (BadSignature, ValueError, TypeError):
            raise HTTPException(401, "Sign in to access your portfolio.") from None
        with self.repository.session() as db:
            login = db.get(Login, digest)
            if not login or login.expires <= time.time():
                raise HTTPException(401, "Your session has expired. Sign in again.")
        user = self.access.active_user(login.owner)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("origin") not in (None, self.settings.origin):
                raise HTTPException(403, "Request origin is not allowed.")
            if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), login.csrf):
                raise HTTPException(403, "Refresh the page before submitting this request.")
        return SimpleNamespace(id=login.id, owner=login.owner, name=user.name, csrf=login.csrf,
                               user_id=user.id, email=user.email, role=user.role)

    def admin(self, request):
        user = self.current(request)
        if user.role != "admin":
            raise HTTPException(403, "Administrator access is required.")
        return user

    def register(self, app):
        @app.get("/auth/login")
        async def login(request: Request):
            if self.settings.rehearsal:
                return self.issue("rehearsal-user", "Demo presenter", RedirectResponse(self.settings.base_path + "/"))
            callback = self.settings.origin + self.settings.base_path + "/auth/callback"
            return await self.oauth.oracle.authorize_redirect(request, callback)

        @app.get("/auth/callback")
        async def callback(request: Request):
            if self.settings.rehearsal:
                raise HTTPException(404)
            try:
                # Authlib validates the authorization state, ID-token signature, issuer,
                # audience, expiry and nonce; its client handles PKCE exchange.
                token = await self.oauth.oracle.authorize_access_token(request)
                info = token["userinfo"]
                if not info.get("sub") or info.get("iss", "").rstrip("/") != self.settings.get("SSO_ISSUER_URL").rstrip("/"):
                    raise ValueError()
                owner = hashlib.sha256((info["iss"] + "|" + info["sub"]).encode()).hexdigest()
                if not identity_email(info):
                    profile = await self.oauth.oracle.userinfo(token=token)
                    if profile.get("sub") != info["sub"]:
                        raise ValueError()
                    info = {**info, **{key: profile[key] for key in ("email", "email_verified", "preferred_username", "user_name") if key in profile}}
                    if not identity_email(info):
                        logging.getLogger(__name__).warning("Oracle profile has no usable sign-in email; new email grants cannot bind. Check Identity Domain email/profile claims.")
                request.session.clear()
                user = self.access.authorize_sso(owner, info)
                return self.issue(owner, user.name, RedirectResponse(self.settings.base_path + "/"))
            except HTTPException as exc:
                if exc.status_code == 403:
                    request.session.clear()
                    response = RedirectResponse(self.settings.base_path + "/?access=not-enabled")
                    response.delete_cookie("pa_session", path=self.settings.base_path or "/")
                    return response
                raise
            except Exception:
                raise HTTPException(401, "Oracle sign-in could not be verified. Try signing in again.") from None

        @app.post("/auth/logout")
        def logout(request: Request):
            login = self.current(request)
            with self.repository.session.begin() as db:
                db.delete(db.get(Login, login.id))
            response = JSONResponse({"signedOut": True})
            response.delete_cookie("pa_session", path=self.settings.base_path or "/")
            return response
