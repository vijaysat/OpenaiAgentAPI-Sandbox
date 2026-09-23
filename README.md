# GenaiAgent-G — OpenAI Agents API and OCI demos

A command-line application and a separate React Native stock demo for OpenAI project. Python manages session state and function handlers;
**all Agents HTTP requests run through Bash and curl** in `scripts/api.sh`.
No OpenAI SDK or private preview `agent_api_sdk` is required.

Three application modules are available:

| Module | Model/session service | Execution |
| --- | --- | --- |
| `main.py` and `scripts/run_oracle_guide.py` | OpenAI Agents API at `api.openai.com`; uses the saved GeniAgent-G agent | OCI sandbox with `codex exec-server` |
| [`stock-demo/`](stock-demo/README.md) — Ticker | OpenAI Agents API; continues the selected existing session | React Native / Expo UI, local Python API, stock commands in OCI |
| `python3 -m geniagent.oci_inference` | OCI Responses API with separate OCI credentials | Requests run locally or inside an existing OCI sandbox |

The OCI inference module does not create OpenAI Agents API agents or sessions and
does not use the OpenAI account's API credit balance. Its continuation IDs belong
to OCI Responses. OCI service access, billing, and model availability still apply.

## Ticker stock application

Search a company name, select a stock symbol and exchange, and view its latest
available regular-session price, currency, daily change, previous close, daily
range, and provider timestamp. The app displays the agent's response, model,
session ID, turn ID, execution environment, and command completion alongside the
quote. **Refresh price** runs another turn in the same session.

The demo defaults to the user-selected session:

```text
<SESSION_ID>
```

The verified session uses **GeniAgent-G / gpt-6-luna**. The API reads the model from
the selected session; it does not replace it with the `gpt-6-astra` template in
`config/agent.json`. No stock-demo route creates a new agent or session.

### Request flow

```mermaid
sequenceDiagram
    participant UI as React Native / Expo
    participant API as Local Python API
    participant OpenAI as OpenAI Agents API
    participant OCI as OCI workspace and executor
    participant Yahoo as Yahoo Finance
    UI->>API: Company search or selected symbol + request ID
    API->>OpenAI: Check existing session and environment
    API->>OCI: Check health; upload helper and request; read back
    API->>OpenAI: Open event stream, then submit follow-up message
    OpenAI->>OCI: command_execution runs the stock helper
    OCI->>Yahoo: Fetch search results or latest price
    Yahoo-->>OCI: Provider data
    OCI-->>OpenAI: Command result and exit status
    OpenAI-->>API: Command evidence and root turn completion
    API->>OCI: Read result file verify request and helper
    API-->>UI: Structured stock data and agent activity
```

The local API reuses `geniagent`'s Bash/curl transport. The stock helper runs in
OCI and writes a unique result file. The API requires a matching successful
command, completed root turn, unchanged helper, and matching result request ID
before returning data. Prices are populated from the result file, not generated
assistant text. The existing `get_current_time` function is unchanged; stock
lookup uses the session's built-in `command_execution` capability.

### Start the stock demo

Prerequisites: the OpenAI and OCI configuration described below, a connected
`/workspace` OCI environment for the selected session, the `.venv-oci` environment,
and Node.js 22.13 or later. The sandbox needs outbound access to Yahoo Finance in
addition to the executor hosts. The live demo used an isolated `allow-all` project;
the connector's three-host `allowlist` does not include Yahoo Finance.

Run these commands from the repository root:

```bash
cd stock-demo
npm ci
npm run api -- --session-id sess_09f68edbca8dff11006ab2fd2957b081939db122cb9ce9cccb
```

In a second terminal, from the repository root:

```bash
cd stock-demo
npm run web
```

Open [Ticker](http://localhost:8081/), search **Apple**, and select **AAPL / NASDAQ**.
The backend listens on `127.0.0.1:8766`; the browser runs on port `8081`. To serve
a compiled preview instead of Expo's development server, use:

```bash
# From stock-demo/; keep the API running in the other terminal.
EXPO_NO_TELEMETRY=1 EXPO_OFFLINE=1 npm run build:web
npm run preview
```

Run only one frontend server on port 8081. Root `.env` credentials remain on the
backend. `EXPO_PUBLIC_API_URL` configures the frontend's backend address only;
never put credentials in a public Expo variable. See the [module README](stock-demo/README.md)
for iOS/Android addresses, physical-device setup, and data-field details.

### Stock API and session lifecycle

| Route | Body / result |
| --- | --- |
| `GET /api/health` | Local service configuration, selected session ID, execution mode; does not check cloud health |
| `POST /api/search` | `{"query":"Apple","requestId":"<new UUID>"}` → listings and agent activity |
| `POST /api/quote` | `{"symbol":"AAPL","requestId":"<new UUID>"}` → quote and agent activity |

POST requests require `Content-Type: application/json`. Identical completed
requests with the same UUID return their saved response. Failed or ambiguous
requests are not automatically resubmitted. The workspace lock serializes local
CLI and demo activity, and a busy session is rejected before another turn starts.

State lives under `stock-demo/.agent-state/sessions/SESSION_ID/requests/REQUEST_ID/`:
`report.json` records the command, result path, status, response, and agent evidence;
session event and progress logs support diagnosis. The OCI mapping remains under
the root `.geniagent/`. Closing the browser does not cancel a remote turn.

The sandbox expires 30 minutes after provisioning; follow-up turns do not renew
it. An expired login needs OCI reauthentication, and an expired sandbox separately
needs reconnection to the **same session**. Use [existing-session connection](#connect-an-existing-session)
instructions and inspect retained resources first. The demo does not provision
replacement resources automatically. For a retained stopped sandbox, the connector
refuses replacement until the existing resources have been handled explicitly.
Cleanup deletes OCI workspace files; retain needed outputs first.

Yahoo Finance supplies the latest regular-session quote, which may be delayed or
from a closed market. Always read **Price as of**. This is an unofficial public
data integration; provider errors are surfaced rather than replaced with invented
prices. Search includes company equities and lets the user choose the listing.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `main.py`, `geniagent/app.py` | CLI, saved agents/sessions, stream handling, function results, recovery |
| `geniagent/transport.py`, `scripts/api.sh` | Agents HTTP/SSE over Bash and curl |
| `geniagent/oci_runtime.py` | OCI authentication, sandbox/executor lifecycle and health |
| `scripts/connect_oci_session.py` | Connect an existing session; retain IDs and clean up OCI resources |
| `scripts/run_oracle_guide.py`, `scripts/run_temporary_project_test.py` | Opt-in live workflows that use cloud resources |
| `geniagent/oci_inference.py`, `scripts/oci_responses.sh` | Separate OCI Responses inference |
| `config/`, `.env.example`, `requirements-oci.txt` | Agent/environment templates and dependency/configuration examples |
| `stock-demo/backend/` | Local API, existing-session orchestration, helper uploaded to OCI, tests |
| `stock-demo/src/` | Native UI components, screen, request hook, copy, types, and theme |
| `tests/` | CLI, transport, recovery, OCI lifecycle and inference tests |

## Setup and run

Use macOS or Linux with Python 3.10+, Bash, curl 7.76+, and system timezone data.
The OpenAI-hosted path uses only the Python standard library. This checkout
currently selects an OCI self-hosted environment; its additional setup is below.

From the project directory:

```bash
test -f .env || cp .env.example .env
```

Edit `.env` and set `OPENAI_API_KEY` to an **OpenAI application API key** belonging
to the project above. Grant `api.agents.read`, `api.agents.write`, and
`api.responses.write`, as specified in the
[current quickstart](https://developers.openai.com/api/docs/guides/agents-api/quickstart#prerequisites).
Project access, model access, and billing must also permit the requested work.
An OCI API credential cannot replace this key.

In the [API keys dashboard](https://platform.openai.com/api-keys), select this
project, edit the application key, keep **Restricted**, and set **Agents → Write**
and **Model capabilities → Responses → Write**. These dashboard options also
include their read permissions. Save the changes and allow a few minutes for them
to take effect before repeating `check`. The key owner's project role must also
allow these operations.

The application loads `.env` automatically without executing it as shell code.
Exported environment variables take precedence. It reads only its documented
settings and ignores unrelated OCI settings already in your file. Never paste
credentials into chat or commit `.env`.

```bash
python3 main.py check
.venv-oci/bin/python main.py check --oci
.venv-oci/bin/python -u scripts/run_temporary_project_test.py --network-policy allow-all
```

The initial configured-project validation found `DENY_ALL` egress. The temporary-project
runner below provides an isolated live test with explicit egress. For an OCI project that
already permits the required outbound traffic, use `main.py start --oci`, then
`main.py send "Read startup-check.json and explain the result."` with the same
`.venv-oci/bin/python` interpreter.

For an OpenAI-hosted session, set `config/environment.json`'s `type` to
`openai_hosted` and use `python3 main.py start` instead. For OCI follow-ups,
use `.venv-oci/bin/python` so the sandbox health check can load the OCI SDK.

`start` creates a reusable agent, saves its returned ID, then creates a session
using **`agent_id`**, not another inline definition. Later starts reuse that agent
and create separate sessions. The initial message exercises both the local
`get_current_time` function and Python execution in the remote workspace.
It asks the agent to write and read back `/workspace/outputs/startup-check.json`.

Assistant text streams to stdout. Progress events and tool outcomes go to stderr.
Server events are saved as `.geniagent/SESSION_ID-events.jsonl`. Local progress and
errors are saved separately as `.geniagent/SESSION_ID-client.jsonl`, including OCI
health checks, stream setup, message submission, and failures before any event
arrives. IDs, snapshots,
and cached tool results are stored with private filesystem permissions. Logs and
snapshots can contain conversation content; they are ignored by Git.

The supplied agent settings are completed in `config/agent.json`:

- Name: `GeniAgent-G`, using the explicit name in the request.
- Model: `gpt-6-astra`, the direct OpenAI API identifier approved for this
  application. The supplied `openai.gpt-6-astra` returned `model_not_found`;
  the corrected identifier was accepted when creating the reusable agent.
  No automatic model fallback occurs.
- Medium reasoning, automatic reasoning summary, text output, and medium verbosity.
- General technical-assistant instructions and one read-only time function.

Edit the JSON to customize behavior. After an edit, use
`python3 main.py start --new-agent`; configuration changes do not update old
sessions. To use another saved definition explicitly, pass `--agent-id AGENT_ID`.

## Environment options

### OpenAI-hosted

Set `config/environment.json`'s `type` to `openai_hosted`. OpenAI provisions the Linux
sandbox and connects the executor automatically. The setup command verifies Python
and Node.js and creates an output directory. Outbound sandbox networking is disabled
because the startup task needs no external packages or sites. The application key
stays on your computer. See the
[hosted environment guide](https://developers.openai.com/api/docs/guides/agents-api/environments/openai-hosted).

For a task without code or files, use a JSON file containing `{"type":"none"}`
with `--environment-config PATH` and supply a suitable `--message`.

### OCI sandbox: Oracle guide integration

This option follows the application-managed lifecycle in the supplied
[Oracle Confluence guide](https://confluence.oraclecorp.com/confluence/display/OCAS/Sandbox%3A+With+OpenAI+Agent+API)
and the [current OpenAI OCI guide](https://developers.openai.com/api/docs/guides/agents-api/environments/providers/oci).
The API/control plane remains on OpenAI. OCI hosts the workspace and executor.

The isolated `.venv-oci` uses the requested preview versions:

- OCI SDK `2.186.1+preview.1.20260916185222`
- OCI CLI `3.93.1+preview.1.1806`

They are pinned in `requirements-oci.txt`. With access to Oracle's internal
package registry, install both together and verify dependencies:

```bash
bash scripts/setup_oci.sh
.venv-oci/bin/oci --version
```

The ordinary PyPI OCI package may not include `oci.generative_ai_sandbox`. Keep
these pins together to prevent another CLI installation from replacing the SDK.
The setup script also accepts an Oracle-provided SDK wheel path for offline SDK
installation. The existing `.venv` is separate from `.venv-oci`.

Use a sandbox-enabled OCI Generative AI project and a security-token profile with
permission to manage its sandboxes. Authenticate with the installed OCI CLI:

```bash
.venv-oci/bin/oci session authenticate --profile-name Sandbox --region us-chicago-1
```

Add these settings to the existing `.env`:

```dotenv
OCI_SANDBOX_PROJECT_ID=ocid1.generativeaiproject.REPLACE_ME
OCI_SANDBOX_PROFILE=Sandbox
OCI_SANDBOX_REGION=us-chicago-1
OCI_SANDBOX_RUNTIME=python-3.11
OCI_SANDBOX_SHAPE=SMALL
OPENAI_EXECUTOR_API_KEY=REPLACE_WITH_ENVIRONMENT_KEY
```

Create the separate environment key from the
[OpenAI environment keys page](https://platform.openai.com/agents?tab=environments&environment_view=keys)
under the same organization, project, and user/service account as the application
key. Keep its other permissions disabled. This implements the
[current executor authentication guidance](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted#authentication),
which differs from the older Confluence example that passes the application key
into the sandbox. This app passes **only the environment key** as `CODEX_API_KEY`.

```bash
.venv-oci/bin/python main.py check --oci
.venv-oci/bin/python main.py start --oci
.venv-oci/bin/python main.py send "Read the previous result and add an explanation."
.venv-oci/bin/python main.py delete
```

The OCI path creates a self-hosted session, opens its stream, provisions a SMALL
sandbox, checks for Python/Node/npm, installs `@openai/codex@alpha`, and starts
`codex exec-server`. It passes the returned environment ID and `remote_url`
unchanged, including routing parameters. It verifies OpenAI reports `connected`
before sending the initial user message.

The lifecycle was also compared with the local reference
`/Users/vpurshot/Work/WorkProjects/oci-sandbox-dev-test/oci-openai-agents-sandbox/oci_sandbox/main.py`:
security-token authentication, the `python-3.11` SMALL runtime, installation under
`/workspace/.local`, and `exec-server` with the returned environment routing match
that example. This application uses Bash/curl for the Agents HTTP API, the saved
`GeniAgent-G` definition with `gpt-6-astra`, and a separate environment key as
required by the current executor authentication documentation.

`--oci` constructs the self-hosted API environment and manages setup through the
OCI SDK. Hosted-only network and setup-command fields in `config/environment.json`
are not applied to OCI. Without `--oci`, a `self_hosted` configuration produces a
message showing the required OCI command.

### Connect an existing session

If the dashboard requests a sandbox connection after its old OCI sandbox was
deleted, connect a replacement to that same session:

```bash
.venv-oci/bin/python -u scripts/connect_oci_session.py \
  --session-id SESSION_ID --network-policy allow-all
```

This creates an isolated OCI project in the source project's compartment, leaving
the source project's network policy and `.env` unchanged. It confirms the old
sandbox is absent, preserves its mapping, and connects the replacement executor
using the session's existing environment ID and routing URL. A connected
environment is reused; a still-existing sandbox prevents duplicate creation.
Connection events and the verified session snapshot are saved in `.geniagent`.

The command retains compute for interactive use, with a 30-minute sandbox
expiration. It does not submit or replay a user message; pending input may continue
when the executor connects. Replacement compute does not restore the deleted
sandbox's files. Follow-up messages and application function calls can be handled
through the existing client:

```bash
.venv-oci/bin/python main.py send --session-id SESSION_ID "Introduce yourself and report the current UTC time using your tool."
.venv-oci/bin/python main.py resume --session-id SESSION_ID
```

To watch local diagnostics and incoming server events in a second terminal:

```bash
SESSION_ID="your-session-id"
tail -F ".geniagent/${SESSION_ID}-client.jsonl" ".geniagent/${SESSION_ID}-events.jsonl"
```

The event file is created when the first server event arrives. The connector also
records its connection events there. Connecting an executor does not create a
model turn. If `send` waits silently or only reports HTTP acceptance, inspect the
session's saved turns and items: HTTP acceptance alone does not establish that a
turn started. Use `--timeout 120` **before** `send` to bound the wait. Do not
automatically repeat an input whose outcome is uncertain.

Open this agent's session under OpenAI Platform → Agents → GeniAgent-G → Sessions.
The conversation and tool activity appear after the session starts processing a
turn. OCI executor stdout/stderr and this client's progress are separate logs;
the independent OCI inference module does not populate OpenAI session history.

When finished, delete both the sandbox and its isolated project with this command,
which retains OpenAI session history:

```bash
.venv-oci/bin/python scripts/connect_oci_session.py --session-id SESSION_ID --cleanup
```

The isolated project remains until cleanup even after its sandbox expires. On
failure, resource IDs and retry tokens are retained for inspection and cleanup.

### Disposable OCI network test

This follows the temporary-project pattern in the referenced
`govtech-codex-sandbox/README.md`, using this application's OpenAI Agents HTTP flow.

```bash
.venv-oci/bin/python -u scripts/run_temporary_project_test.py --network-policy allow-all
```

The runner creates a sandbox-enabled project in the configured source project's
compartment and verifies `MANAGED / ALLOW_ALL`. This enables unrestricted outbound
network access only for that disposable project. The original project's policy
and `.env` are not changed. An optional `--network-policy allowlist` instead requests
HTTPS/WSS access to the three required hosts; it has not been validated here.

The runner reuses the saved agent ID, streams an initial turn, checks the local
time-tool result, sends a follow-up, and independently reads the generated JSON
inside OCI to verify result `60`. It saves events, a session snapshot, and a report
under `.geniagent/live-tests/`. It stops/deletes the temporary sandbox and project,
checks deletion, and retains the OpenAI session history. After cleanup that session
has no running executor; use a new run for further execution. Inspect the retained
history with `main.py --state-dir PATH_TO_RUN inspect`.

`python-3.11` is the documented default; the provisioned image must include Node
and npm. Change `OCI_SANDBOX_RUNTIME` to an enabled compatible runtime if needed.
The sandbox needs outbound HTTPS to `registry.npmjs.org` and `api.openai.com`, plus
WSS to `codex-cloud-environments.chatgpt.com`.

Follow-up turns reuse the session, sandbox, and executor. The sandbox has a fixed
30-minute expiration, which is not renewed by follow-up messages. SDK calls have
bounded network waits; startup, setup, and registration have separate deadlines.
IDs and mutation retry tokens are saved immediately. Writes are not automatically
retried. If creation returns no ID, reconcile the saved token in OCI. A running
executor command alone is not treated as proof of registration.

`delete` independently attempts OCI stop/delete and OpenAI session deletion, and
reports incomplete cleanup. Resources are retained after a turn or an error for
inspection and follow-up; they are not silently deleted on Ctrl-C. Clean them up
when finished. Expiration is not proof of deletion. Retrieve needed remote files
before deleting the sandbox. The app does not automatically replace sandboxes or
restart executors after expiry; use the existing-session connection command above
after cleanup.

## Run the Oracle guide workflow

This follows the application-managed flow in the
[Oracle guide](https://confluence.oraclecorp.com/confluence/display/OCAS/Sandbox%3A+With+OpenAI+Agent+API)
and the referenced `oci-openai-agents-sandbox/oci_sandbox/main.py`. Changes are
confined to this current workspace. The referenced checkout's old preview SDK is
not required by this curl-based application.

After connecting a session as described above:

```bash
.venv-oci/bin/python -u scripts/run_oracle_guide.py \
  --session-id sess_0efddaa5bff8176c006ab1a93ecba8819cbe2e9780446b0426 \
  --follow-up
```

The runner uploads `/workspace/brief.txt` through OCI and verifies an exact
readback. It sends the agent a request to write a five-step migration plan, streams
events, then independently downloads `/workspace/plan.md` through OCI. With
`--follow-up`, the same session appends and verifies a rollback section. Local
artifacts and a report are saved privately under `.geniagent/guide-runs/`.

Use `--prepare-only` to verify OCI file upload/readback without a model request.
The runner refuses to overwrite a different existing brief or an existing plan,
retains the session and compute, and never fabricates a plan after a failed turn.
Use the connection command's `--cleanup` option when finished.

## Independent OCI-hosted inference

This module follows the [OCI Responses API documentation](https://docs.oracle.com/en-us/iaas/Content/generative-ai/responses-api.htm).
Every model request goes through Bash/curl to
`https://inference.generativeai.<region>.oci.oraclecloud.com/openai/v1/responses`.
It does not redirect the OpenAI Agents API or change the saved agent definition.

Configure these separate values in this workspace's `.env`:

```dotenv
OCI_GENAI_API_KEY=YOUR_OCI_GENERATIVE_AI_KEY
OCI_GENAI_PROJECT_ID=ocid1.generativeaiproject.YOUR_PROJECT
OCI_GENAI_REGION=us-chicago-1
OCI_GENAI_MODEL=openai.gpt-6-astra
```

They are already configured locally using the existing OCI inference credentials
from the referenced sandbox setup. The OpenAI application and executor keys remain
unchanged. `--model` can explicitly select another model available to your OCI
project; no automatic fallback occurs. Requests omit Astra's unsupported
`temperature` parameter.

Run a streamed inference request from your Mac:

```bash
python3 -m geniagent.oci_inference "Explain the OCI sandbox architecture briefly."
```

Run the request itself inside the existing OCI sandbox:

```bash
.venv-oci/bin/python -m geniagent.oci_inference \
  "Calculate sum([10, 20, 30]) and report the answer." \
  --sandbox-session-id sess_0efddaa5bff8176c006ab1a93ecba8819cbe2e9780446b0426
```

The latter uses the locally saved session-to-sandbox mapping without calling the
OpenAI Agents service. It passes only the OCI inference key, project, and region
to the remote command. The request is plain inference: it does not install an
agent loop, expose the sandbox files to the model, or enable arbitrary shell tools.
The surrounding OpenAI executor can remain connected independently.

Continue an OCI response using its saved ID:

```bash
python3 -m geniagent.oci_inference "Expand your previous answer." \
  --previous-response-id OCI_RESPONSE_ID
```

Each run saves `report.json`, `response.json`, and streamed `events.jsonl` under
`.geniagent/oci-inference/`. Sandbox runs also retain the command ID and uploaded
script/payload paths. These uploaded files contain no API keys and are removed
with sandbox deletion. The OCI command's output is polled and rendered incrementally;
only a completed response with a successful command exit counts as success.
Interrupted requests are not automatically resubmitted.

## Continuing, recovery, and cleanup

```bash
# Optional custom initial task
python3 main.py start --message "Introduce yourself and tell me the current UTC time using your tool."

# Attach to an interrupted session without resending its message
python3 main.py resume

# Target an older session explicitly
python3 main.py inspect --session-id SESSION_ID
python3 main.py send --session-id SESSION_ID "Continue the task."

# Request cancellation, then inspect its outcome
python3 main.py cancel
python3 main.py inspect

# Delete the remote session (also its OCI sandbox when tracked locally)
python3 main.py delete

# Set a shorter streaming deadline; flags precede the subcommand
python3 main.py --timeout 300 start
```

Use `.venv-oci/bin/python` for OCI lifecycle commands. `send` only submits a new
message when the session is idle; use `resume` to finish an active turn.

Streams do not replay missed events. `resume` opens a new stream, buffers incoming
events while fetching paginated items/turns, restores completed text, handles
currently pending actions, and then consumes buffered/live events. It never
automatically resends user input. If creation disconnected before returning a
session ID, inspect your project for the resource before creating another one.

Only a root `agent.session.turn.completed` indicates successful turn completion;
idle, EOF, and subagent completion do not. Failure/cancellation/environment errors
exit nonzero. Read the actual tool results too: a completed turn is not proof that
every requested task succeeded. HTTP errors include status and request ID, with
credentials redacted. Closing a stream does not cancel remote execution.

Function calls are taken from current `required_actions`. The app validates and
runs only `get_current_time`, returns success/error with the exact `turn_id` and
`call_id`, and stores results before submission. Repeated pending calls reuse
the saved result. Unknown functions are returned as tool errors; no arbitrary
function name or shell command is executed on your computer. When adding tools
with side effects, add durable execution reconciliation before enabling them.

## Direct curl examples

The application already invokes curl for every Agents operation. To call the
checked-in transport directly, export `OPENAI_API_KEY` securely in your shell and
run the following. These commands require `jq` for JSON construction and show the
same reusable-agent flow. Do not run them in addition to `start` unless you want
another agent/session. Use the Python CLI for automatic function handling.

```bash
export OPENAI_PROJECT_ID=proj_VD6ShY81c4nQ7iY3dFVuRzfo

agent_id=$(bash scripts/api.sh POST /agents < config/agent.json | jq -er '.id')

jq -n --arg agent_id "$agent_id" \
  '{agent_id:$agent_id, environment:{type:"openai_hosted"}, input:"Introduce yourself.", stream:true}' \
  | bash scripts/api.sh POST /agents/sessions /dev/null 900
```

The underlying HTTP request is directly expressible with curl:

```bash
curl --no-buffer --fail-with-body \
  https://api.openai.com/v1/agents \
  --header @<(printf 'Authorization: Bearer %s\n' "$OPENAI_API_KEY") \
  --header 'OpenAI-Project: proj_VD6ShY81c4nQ7iY3dFVuRzfo' \
  --header 'OpenAI-Beta: agents=v1' \
  --header 'Content-Type: application/json' \
  --data-binary @config/agent.json
```

For follow-ups, subscribe with
`GET /v1/agents/sessions/SESSION_ID/events?stream=true` **before** posting
`agent.session.input.message` to the same `/events` path. Return function results
there using `agent.session.input.tool_result`. See
[events](https://developers.openai.com/api/docs/guides/agents-api/sessions/events)
and [functions](https://developers.openai.com/api/docs/guides/agents-api/tools/functions).

## Validation and troubleshooting

```bash
python3 -m unittest discover -s tests -v
```

Tests need localhost networking permissions. They run actual Bash/curl against a
local fake HTTP server and fake OCI lifecycle calls. They cover creation by saved
agent ID, streaming, subscriptions before follow-ups, tool results, recovery,
pagination, errors, credential handling, executor setup, routing, and cleanup.
They do not establish live OpenAI inference or OCI execution.

### Local validation — September 22, 2026

| Check | Result |
| --- | --- |
| Core Python suite (`tests/`) | **50 passed** |
| Stock backend suite (`stock-demo/backend/`) | **16 passed** |
| TypeScript (`npm run typecheck`) | Passed |
| Component conventions (`npm run validate`) | Passed for 9 files |
| Expo web export (`npm run build:web`) | Passed; output in `stock-demo/dist/` |
| Python compilation and Bash syntax | Passed |
| OCI dependency compatibility (`pip check`) | Passed; no broken requirements |
| Installed frontend dependencies (`npm ls --depth=0`) | Passed |
| OCI sandbox SDK import | Passed; installed SDK/CLI match `requirements-oci.txt` |
| Local HTTP smoke checks | Preview and API health returned 200; configured session matched; invalid request ID returned 400 before cloud execution |

**66 automated tests passed in total.** The initial core run could not bind its
local fake server under the restricted execution environment; the same suite
passed after granting loopback access. No cloud resources were created by these
tests. Component checks inspect conventions and accessibility attributes; they
are not runtime component tests or a native-device audit.

Validation found and fixed an error-response bug in the stock backend: OCI
transport failures can carry the status `"unknown"`. The API now maps invalid
error codes to HTTP 502, retaining the redacted error and session evidence. A
regression test reproduces the failure and verifies that no user turn is submitted.

Run all checks from the repository root:

```bash
.venv-oci/bin/python -m unittest discover -s tests -v
.venv-oci/bin/python -m pip check
python3 -m compileall -q main.py geniagent scripts tests stock-demo/backend
bash -n scripts/api.sh scripts/oci_responses.sh scripts/setup_oci.sh
cd stock-demo
npm ci
npm run typecheck
npm run validate
npm run test:api
npm ls --depth=0
EXPO_NO_TELEMETRY=1 EXPO_OFFLINE=1 npm run build:web
```

The stock tests use controlled responses for provider parsing, same-session
orchestration, stream-before-input ordering, OCI result readback, cached requests,
and rejection of busy/disconnected sessions, stale turns, and mismatched results.
Local tests and dependency checks do not establish current cloud connectivity or
constitute a complete security audit.

### Recorded live stock validation — September 22, 2026

The browser workflow successfully used session
`sess_09f68edbca8dff11006ab2fd2957b081939db122cb9ce9cccb`, model `gpt-6-luna`, and OCI
sandbox `sbx-fczzdcw7k5fpsh23`. Both successful requests had a completed
`command_execution`, exit code 0, a root `agent.session.turn.completed`, and an
independently read OCI result file.

| Browser action | Verified result | Saved request ID |
| --- | --- | --- |
| Search Apple | AAPL / NASDAQ, APLE / NYSE, AAPL.TO / Toronto | `fc8a2ad0-ee08-4bf7-971e-17c62dccff6d` |
| Select AAPL | 339.75 USD; provider timestamp `2026-09-22T20:00:01+00:00` | `86725ff2-58b3-4710-86e9-1a578b83e6f2` |

The quote was retrieved at `2026-09-22T23:11:44.926909+00:00`. These are historical
test observations, not a current-price or currently-connected claim. Reports and
stream logs are stored under
`stock-demo/.agent-state/sessions/SESSION_ID/requests/REQUEST_ID/` and were reviewed
again during this documentation update; no additional cloud turn was submitted.

A later saved NVIDIA search (`5ac33f72-1774-460d-b58b-26dddf467dfe`) ended with
OpenAI `rate_limit_exceeded` at `2026-09-22T23:14:05Z`, without root turn completion.
The app retained its failure report and did not display a successful result. Inspect
that session before retrying; successful command execution alone is insufficient.

Earlier September 21 permission, billing, connection, Oracle-guide, and independent
OCI inference observations are preserved in the
[validation history](docs/validation-history-2026-09-21.md). Those failures are
historical and do not negate the later successful stock turns. iOS/Android simulator
or physical-device execution has not been validated in these checks.

| Symptom | Action |
| --- | --- |
| 401 / invalid_api_key | Replace the key with a valid OpenAI project application key. |
| 401 / api.responses.write | Check effective Responses Write access on the application key and its owner's role; Agents read access alone is insufficient. |
| 403 / api.agents.read | Enable Agents read/write and Responses write; check project eligibility. |
| 404 | Verify endpoint access and that the saved ID belongs to this project. |
| 429 | Check project quota, billing, or rate limits before retrying. |
| credit_balance_exhausted | Add credits in OpenAI billing for the project's organization, then retry. |
| usage_limit_exceeded | Review the organization's billing and usage limits; inspect the existing turn before retrying. |
| Invalid model/settings | Verify Astra access and edit the agent JSON; no automatic model fallback occurs. |
| Missing OCI sandbox module | Use `.venv-oci` and the Oracle beta wheel. |
| OCI authentication error | Renew the named OCI security-token profile. |
| A sandbox already exists for this session | Inspect the retained sandbox and project mapping; stopped/expired does not mean deleted. Handle existing resources before reconnecting the same session. |
| Stock demo says executor disconnected | Reconnect the configured session; reauthentication alone does not provision or restart its sandbox. |
| Stock command cannot reach Yahoo Finance | Check sandbox egress to `query1.finance.yahoo.com`; the connector's default three-host allowlist covers executor setup only. |
| Stock request failed or timed out | Inspect its request report and the existing session before another submission. A completed command without a completed root turn is not a successful lookup. |
| rate_limit_exceeded | Allow the service limit to recover and inspect the retained turn before retrying; the demo does not automatically resend the request. |
| Early EOF or timeout | Run `inspect`/`resume`; do not blindly resend uncertain input. |
| HTTP acceptance but no events or turns | Inspect saved session history and `SESSION_ID-client.jsonl`; acceptance does not prove execution. Check inference access and billing separately. |
| OCI setup/connection failure | Check runtime, outbound network, environment key, and saved resource IDs. |
| npm ENOTFOUND | Inspect the OCI project's sandbox egress policy; the verified ALLOW_ALL workaround is scoped to a disposable project. |

The linked Oracle guide and current OpenAI
[overview](https://developers.openai.com/api/docs/guides/agents-api/overview),
[configuration](https://developers.openai.com/api/docs/guides/agents-api/configuration),
and environment/session/function guides linked above.
