#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 -m venv "$project_root/.venv-oci"
if [ "$#" -gt 0 ]; then
  wheel="$1"
  test -f "$wheel" || { echo 'Beta SDK wheel not found.' >&2; exit 1; }
  "$project_root/.venv-oci/bin/python" -m pip install "$wheel"
else
  "$project_root/.venv-oci/bin/python" -m pip install -r "$project_root/requirements-oci.txt"
fi
"$project_root/.venv-oci/bin/python" -m pip check
"$project_root/.venv-oci/bin/python" -c 'import oci; import oci.generative_ai_sandbox; print("OCI beta sandbox SDK ready:", oci.__version__)'
