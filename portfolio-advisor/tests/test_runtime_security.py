import pytest

from portfolio_advisor import runtime_security


@pytest.mark.parametrize("number,label,tuple_value", [
    (0x30000160, "OpenSSL 3.0.22 25 Aug 2026", (3, 0, 0, 22, 0)),
    (0x30000170, "OpenSSL 3.0.23", (3, 0, 0, 23, 0)),
    (0x30500000, "OpenSSL 3.5.0", (3, 5, 0, 0, 0)),
])
def test_accepts_patched_openssl_with_python_compatibility_tuple(monkeypatch, number, label, tuple_value):
    monkeypatch.setattr(runtime_security.ssl, "OPENSSL_VERSION_NUMBER", number)
    monkeypatch.setattr(runtime_security.ssl, "OPENSSL_VERSION", label)
    monkeypatch.setattr(runtime_security.ssl, "OPENSSL_VERSION_INFO", tuple_value)
    assert runtime_security.check_python_tls() == label


@pytest.mark.parametrize("number,label", [
    (0x30000140, "OpenSSL 3.0.20"),
    (0x30000150, "OpenSSL 3.0.21"),
    (0x1010117F, "OpenSSL 1.1.1w"),
    (0x40000000, "LibreSSL 4.0.0"),
])
def test_rejects_unpatched_or_unexpected_tls_library(monkeypatch, number, label):
    monkeypatch.setattr(runtime_security.ssl, "OPENSSL_VERSION_NUMBER", number)
    monkeypatch.setattr(runtime_security.ssl, "OPENSSL_VERSION", label)
    with pytest.raises(ValueError, match="requires OpenSSL 3.0.22"):
        runtime_security.check_python_tls()
