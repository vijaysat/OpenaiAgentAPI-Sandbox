"""Check Python's linked TLS library without misreading OpenSSL 3.x tuples."""
import ssl


def check_python_tls():
    # OpenSSL 3.0.22 is encoded as 0x30000160. Python 3.13 exposes its
    # compatibility tuple as (3, 0, 0, 22, 0), not (3, 0, 22, ...).
    version = ssl.OPENSSL_VERSION
    if not version.startswith("OpenSSL ") or ssl.OPENSSL_VERSION_NUMBER < 0x30000160:
        raise ValueError(f"Python requires OpenSSL 3.0.22 or newer; found {version}")
    return version


if __name__ == "__main__":
    print("Python TLS library:", ssl.OPENSSL_VERSION, flush=True)
    try:
        check_python_tls()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
