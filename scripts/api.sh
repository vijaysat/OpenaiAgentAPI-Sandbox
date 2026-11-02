#!/usr/bin/env bash
# HTTP transport shared by the app and manual curl calls. Payload arrives on stdin.
set -euo pipefail
set +x
: "${OPENAI_API_KEY:?Set OPENAI_API_KEY in your environment or project .env}"
: "${OPENAI_PROJECT_ID:?Set OPENAI_PROJECT_ID in your environment or project .env}"
project="$OPENAI_PROJECT_ID"
case "$project" in *[!A-Za-z0-9_-]*) echo 'Invalid OPENAI_PROJECT_ID; use the exact project ID without whitespace.' >&2; exit 2 ;; esac
base="${GENIAGENT_API_BASE:-https://api.openai.com/v1}"
method="${1:?Usage: bash scripts/api.sh METHOD /agents/path [headers-file] [timeout]}"
route="${2:?API path required}"
headers="${3:-/dev/null}"
timeout="${4:-60}"
case "$base" in
  https://api.openai.com/v1|http://127.0.0.1:[0-9]*/v1) ;;
  *) echo 'Unsupported API origin.' >&2; exit 2 ;;
esac
case "$route" in /agents|/agents/*|/agents\?*) ;; *) echo 'Expected an /agents path.' >&2; exit 2 ;; esac
case "$OPENAI_API_KEY$project" in *$'\n'*|*$'\r'*) echo 'Invalid credential or project header.' >&2; exit 2 ;; esac
args=(--request "$method")
if [ "$method" = POST ]; then args+=(--data-binary @-); fi
# Keep the bearer token out of the command line and temporary files.
# --disable prevents .curlrc from adding unsafe logging or redirects.
curl --disable --silent --show-error --no-buffer --fail-with-body \
  --connect-timeout 15 --max-time "$timeout" --dump-header "$headers" \
  --header @<(printf 'Authorization: Bearer %s\nOpenAI-Project: %s\nOpenAI-Beta: agents=v1\nContent-Type: application/json\nAccept: application/json, text/event-stream\n' "$OPENAI_API_KEY" "$project") \
  "${args[@]}" "$base$route"
