#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 0 ]; then
  echo 'Usage: bash scripts/setup_oci.sh (installs the public SDK from requirements-oci.txt)' >&2
  exit 2
fi
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 -m venv "$project_root/.venv-oci"
"$project_root/.venv-oci/bin/python" -m pip install -r "$project_root/requirements-oci.txt"
"$project_root/.venv-oci/bin/python" -m pip check
"$project_root/.venv-oci/bin/python" -c 'import oci; import oci.generative_ai_sandbox; print("OCI sandbox SDK ready:", oci.__version__)'
