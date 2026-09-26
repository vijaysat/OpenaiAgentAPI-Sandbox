"""Check configuration without printing values, creating resources or invoking a model."""
import argparse
from pathlib import Path
import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2])]
from portfolio_advisor.config import Settings, ConfigurationError, read_env, SECRET_NAMES, ROOT

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--resolve-vault", action="store_true", help="Read CURRENT values from explicitly configured secrets; never prints them")
args = parser.parse_args()
if not args.resolve_vault:
    import json
    env = read_env(ROOT / ".env")
    for name in SECRET_NAMES:
        try:
            valid = json.loads(env.get(name, ""))["secretId"].startswith("ocid1.vaultsecret.")
        except (ValueError, KeyError, TypeError, AttributeError):
            valid = False
        print(f"{name}: {'reference configured' if valid else 'dedicated secret reference pending'}")
    raise SystemExit(0)
try:
    Settings.load()
    print("Configuration and CURRENT Vault versions resolved. No inference or sandbox operation was performed.")
except ConfigurationError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
