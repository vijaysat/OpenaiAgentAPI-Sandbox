#!/usr/bin/env bash

set -euo pipefail
set +x
: "${OCI_GENAI_API_KEY:?Set OCI_GENAI_API_KEY}"
: "${OCI_GENAI_PROJECT_ID:?Set OCI_GENAI_PROJECT_ID}"
region="${OCI_GENAI_REGION:-us-chicago-1}"
[[ "$region" =~ ^[a-z]+-[a-z]+-[0-9]+$ ]] || { echo 'Invalid OCI region.' >&2; exit 2; }
[[ "$OCI_GENAI_PROJECT_ID" == ocid1.generativeaiproject.* ]] || { echo 'Expected an OCI project OCID.' >&2; exit 2; }
case "$OCI_GENAI_API_KEY$OCI_GENAI_PROJECT_ID" in *$'\n'*|*$'\r'*) echo 'Invalid header value.' >&2; exit 2 ;; esac
method="${1:?HTTP method required}"
route="${2:?API route required}"
headers="${3:-/dev/null}"
timeout="${4:-300}"
case "$method:$route" in POST:/responses) ;; GET:/responses/*) ;; *) echo 'Unsupported OCI Responses operation.' >&2; exit 2 ;; esac
[[ "$route" =~ ^/responses(/[A-Za-z0-9_-]+)?$ ]] || { echo 'Invalid response route.' >&2; exit 2; }
args=(--request "$method")
if [ "$method" = POST ]; then args+=(--data-binary @-); fi
curl --disable --silent --show-error --no-buffer --fail-with-body \
  --connect-timeout 15 --max-time "$timeout" --dump-header "$headers" \
  --header @<(printf 'Authorization: Bearer %s\nOpenAI-Project: %s\nContent-Type: application/json\nAccept: application/json, text/event-stream\n' "$OCI_GENAI_API_KEY" "$OCI_GENAI_PROJECT_ID") \
  "${args[@]}" "https://inference.generativeai.${region}.oci.oraclecloud.com/openai/v1${route}"
