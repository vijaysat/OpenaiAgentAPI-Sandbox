import base64
import json
from types import SimpleNamespace as NS
import pytest
from portfolio_advisor.config import SECRET_NAMES, resolve_secrets, ConfigurationError, Settings


def references():
    return {name: json.dumps({"secretId":f"ocid1.vaultsecret.test.{name}"}) for name in SECRET_NAMES}


class Vault:
    def __init__(self): self.calls=[]
    def get_secret_bundle(self, sid, **kwargs):
        self.calls.append((sid,kwargs))
        value = (sid.split('.')[-1]+"-value-"+"x"*32).encode()
        return NS(data=NS(secret_bundle_content=NS(content=base64.b64encode(value).decode())))


def test_current_version_memory_resolution():
    refs=references(); vault=Vault()
    result=resolve_secrets(refs,lambda _:vault)
    assert all(kwargs == {"stage":"CURRENT"} for _,kwargs in vault.calls)
    assert result["OPENAI_API_KEY"] != refs["OPENAI_API_KEY"]
    assert json.loads(refs["OPENAI_API_KEY"])["secretId"].startswith("ocid1.")


def test_missing_references_do_not_request_vault():
    with pytest.raises(ConfigurationError,match="requires a dedicated"):
        resolve_secrets({"OPENAI_API_KEY":"a-literal-key"},lambda _:pytest.fail("must not resolve"))


def test_denied_bundle_never_exposes_provider_message():
    class Denied:
        def get_secret_bundle(self,*a,**k): raise RuntimeError("credential=top-secret")
    with pytest.raises(ConfigurationError) as error:
        resolve_secrets(references(),lambda _:Denied())
    assert "top-secret" not in str(error.value)


def test_identical_keys_rejected():
    class Same:
        def get_secret_bundle(self,*a,**k): return NS(data=NS(secret_bundle_content=NS(content=base64.b64encode(b'x'*40).decode())))
    with pytest.raises(ConfigurationError,match="distinct"):
        resolve_secrets(references(),lambda _:Same())


def test_rehearsal_cannot_bind_publicly(monkeypatch,tmp_path):
    monkeypatch.setenv("PORTFOLIO_REHEARSAL","1")
    monkeypatch.setenv("APP_HOST","0.0.0.0")
    with pytest.raises(ConfigurationError,match="loopback"):
        Settings.load(tmp_path/"absent")


def test_connection_resolves_only_two_distinct_openai_credentials():
    refs=references();vault=Vault()
    result=resolve_secrets(refs,lambda _:vault,names=('OPENAI_API_KEY','OPENAI_EXECUTOR_API_KEY'))
    assert len(vault.calls)==2
    assert result['AUTH_SESSION_SECRET']==refs['AUTH_SESSION_SECRET']
