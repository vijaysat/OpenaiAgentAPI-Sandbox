"""Resolve explicit Vault references before constructing application services."""
from dataclasses import dataclass, field
import base64
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SECRET_NAMES = {
    "OPENAI_API_KEY": "portfolio-advisor-openai-api-key",
    "OPENAI_EXECUTOR_API_KEY": "portfolio-advisor-openai-executor-key",
    "AUTH_SESSION_SECRET": "portfolio-advisor-session-secret",
    "SSO_CLIENT_SECRET": "portfolio-advisor-sso-client-secret",
    "DATABASE_URL": "portfolio-advisor-database-url",
}


class ConfigurationError(RuntimeError):
    pass


def read_env(path):
    result = {}
    if Path(path).exists():
        for raw in Path(path).read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.removeprefix("export ").partition("=")
            if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
                raise ConfigurationError("Invalid environment entry; use NAME=value.")
            value = value.strip()
            if value[:1] in ("'", '"'):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ConfigurationError(f"Unclosed environment value for {key.strip()}.")
                value = value[1:-1]
            result[key.strip()] = value
    return result


def oci_auth(values):
    import oci
    region = values.get("OCI_REGION", "us-chicago-1")
    if values.get("OCI_AUTH_MODE", "security_token") == "resource_principal":
        return {"region": region}, oci.auth.signers.get_resource_principals_signer()
    if values.get("OCI_AUTH_MODE", "security_token") != "security_token":
        raise ConfigurationError("OCI_AUTH_MODE must be security_token or resource_principal.")
    try:
        config = oci.config.from_file(profile_name=values.get("OCI_PROFILE", "Sandbox"))
        config["region"] = region
        token = Path(config["security_token_file"]).expanduser().read_text().strip()
        signer = oci.auth.signers.SecurityTokenSigner(
            token, oci.signer.load_private_key_from_file(config["key_file"]))
        return config, signer
    except Exception:
        raise ConfigurationError("OCI login unavailable; authenticate the configured profile.") from None


def vault_client(values):
    import oci
    config, signer = oci_auth(values)
    return oci.secrets.SecretsClient(config, signer=signer, timeout=(5, 15),
                                    retry_strategy=oci.retry.NoneRetryStrategy())


def resolve_secrets(values, factory=vault_client, names=None):
    resolved = dict(values)
    refs = {}
    for name in (SECRET_NAMES if names is None else names):
        if name not in SECRET_NAMES:
            raise ConfigurationError('Unknown dedicated credential name.')
        try:
            value = json.loads(values.get(name, ""))
            secret_id = value["secretId"]
            if not isinstance(secret_id, str) or not secret_id.startswith("ocid1.vaultsecret."):
                raise ValueError()
            refs[name] = secret_id
        except (ValueError, KeyError, TypeError):
            raise ConfigurationError(f"{name} requires a dedicated Vault secretId reference.") from None
    try:
        client = factory(values)
    except Exception:
        raise ConfigurationError("OCI Vault client could not authenticate.") from None
    for name, secret_id in refs.items():
        try:
            bundle = client.get_secret_bundle(secret_id, stage="CURRENT").data.secret_bundle_content
            value = base64.b64decode(bundle.content, validate=True).decode("utf-8")
            if not value.strip():
                raise ValueError()
            resolved[name] = value
        except Exception:
            raise ConfigurationError(f"Cannot resolve CURRENT Vault secret for {name}; check access and version.") from None
    if {"OPENAI_API_KEY", "OPENAI_EXECUTOR_API_KEY"} <= refs.keys() and resolved["OPENAI_API_KEY"] == resolved["OPENAI_EXECUTOR_API_KEY"]:
        raise ConfigurationError("OpenAI application and executor keys must be distinct.")
    if "AUTH_SESSION_SECRET" in refs and len(resolved["AUTH_SESSION_SECRET"]) < 32:
        raise ConfigurationError("AUTH_SESSION_SECRET must contain at least 32 characters.")
    return resolved


@dataclass(repr=False)
class Settings:
    values: dict = field(repr=False)
    rehearsal: bool = False

    def get(self, key, default=""):
        return self.values.get(key, default)

    @property
    def base_path(self):
        return self.get("APP_BASE_PATH").rstrip("/")

    @property
    def origin(self):
        return self.get("APP_ORIGIN", "http://127.0.0.1:8780").rstrip("/")

    @classmethod
    def load(cls, env_file=None, factory=vault_client):
        values = {**read_env(env_file or ROOT / ".env"), **os.environ}
        rehearsal = values.get("PORTFOLIO_REHEARSAL") == "1"
        if rehearsal:
            if values.get("APP_ENV", "local") != "local" or values.get("APP_HOST", "127.0.0.1") != "127.0.0.1":
                raise ConfigurationError("Rehearsal mode is restricted to local loopback.")
            if not values.get("DATABASE_URL", "").startswith(("postgresql+psycopg://", "sqlite")):
                raise ConfigurationError("Rehearsal requires its own local DATABASE_URL.")
            values.setdefault("AUTH_SESSION_SECRET", "local-rehearsal-only-session-key-00000000")
        else:
            values = resolve_secrets(values, factory)
            for key in ("OPENAI_PROJECT_ID", "OCI_SANDBOX_PROJECT_ID", "OCI_OBJECT_BUCKET", "OCI_NAMESPACE",
                        "SSO_ISSUER_URL", "SSO_CLIENT_ID", "APP_ORIGIN"):
                if not values.get(key):
                    raise ConfigurationError(f"{key} is required.")
            if not values["DATABASE_URL"].startswith("postgresql"):
                raise ConfigurationError("PostgreSQL is required for live mode.")
            if values.get("APP_ENV") == "hosted":
                if not values["APP_ORIGIN"].startswith("https://") or values.get("OCI_AUTH_MODE") != "resource_principal":
                    raise ConfigurationError("Hosted mode requires HTTPS and OCI resource-principal authentication.")
                if "sslmode=verify-full" not in values["DATABASE_URL"]:
                    raise ConfigurationError("Hosted PostgreSQL requires sslmode=verify-full.")
        path = values.get("APP_BASE_PATH", "")
        if path and (not path.startswith("/") or ".." in path or "?" in path):
            raise ConfigurationError("APP_BASE_PATH must be an absolute URL path.")
        return cls(values, rehearsal)
