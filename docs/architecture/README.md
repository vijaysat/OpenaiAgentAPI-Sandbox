# End-to-end architecture

Open **[the interactive architecture diagram](end-to-end.html)**. It contains search, zoom, relationship tracing, light/dark themes, and image/SVG export. The editable source is [end-to-end.architecture.json](end-to-end.architecture.json).

This diagram represents the inspected working tree on 25 September 2026, including the untracked `portfolio-advisor/` application and modified runtime files. It is a component-and-flow overview of the OCI-backed Agents API path, not a deployment-health report or a claim that all entry points share a single running process. No application behavior was changed and no cloud analysis was submitted for this task. Component numbers show the logical request and result path; asynchronous events can arrive before final results.

## Reading the flow

1. **User input.** The CLI accepts a prompt; Ticker accepts a company search or selected symbol; Folio accepts a CSV portfolio and allocation model. Folio authenticates with Oracle Identity Domain SSO, checks enabled application access and ownership, and requires CSRF protection for writes.
2. **Application orchestration.** Ticker calls its local Python API. Folio validates inputs, persists an idempotent job in PostgreSQL, returns HTTP 202, and lets one of two workers claim it. PostgreSQL is both the job store and the durable state store; there is no separate message broker in this implementation.
3. **Prepare the session and workspace.** The CLI creates/reuses an agent and creates a session. Ticker only continues a selected existing session. Folio claims the prepared session once or creates an isolated session for an analysis. Application code uses `OCIRuntime` for provisioning/resumption, health checks, and helper/input upload with readback. `CurlClient` and `OCIRuntime` are separate shared adapters: the diagram groups their common library boundary under “Shared transport.”
4. **Submit and observe.** Agents API HTTP requests and SSE use Bash/curl. Follow-up event subscriptions are established before message submission. OpenAI manages model turns and dispatches `command_execution` to the connected OCI environment. The sandbox's `codex exec-server` connects outbound using the separate executor credential; the full application key is not passed to it.
5. **Run in OCI.** The agent invokes the exact uploaded Python command. The sandbox lasts 30 minutes. Ticker's helper queries Yahoo Finance for search/quotes. Folio's helper obtains timestamped quotes and performs deterministic allocation, concentration, drift, and rebalancing calculations, then writes request-specific JSON. The model supplies an explanation; structured market values come from the helper result.
6. **Return two kinds of evidence.** Command status and completed-root-turn evidence arrive through the Agents API. The application separately reads the output through the OCI SDK. The diagram's return rail shows this file readback; the HTTPS/SSE relationship covers the event channel. Neither a connected executor nor an accepted POST proves a completed analysis.
7. **Verify in the application.** Ticker checks successful command execution, root completion, helper integrity, and the output's request/operation identity. Folio additionally checks helper/input hashes, requires exactly one matching successful command from the completed root turn, and independently recalculates the result. The generic CLI handles registered local functions such as `get_current_time` and streams output; the demos' financial-result verification gate is not imposed on every arbitrary CLI prompt.
8. **Persist and return.** Ticker saves request evidence locally and returns structured data plus agent activity. Folio saves HTML/CSV/JSON reports in private OCI Object Storage, records the completed result in PostgreSQL, streams progress, and serves owner-authorized downloads. The API also stores the original uploaded CSV in Object Storage. CLI/Ticker use local journals rather than the Folio database.

## Alternate paths and recovery

- **Folio questions:** create a durable follow-up job that continues the parent analysis session. A returned command execution causes the answer to be withheld; saved helper/input/result integrity is checked again. No refresh of prices, trades, or modification of the saved report is part of this path.
- **Folio rehearsal:** explicitly local synthetic quotes and fixture explanations; it bypasses OpenAI, OCI, and Yahoo, and uses local artifact storage. It is not evidence of live analysis.
- **OpenAI-hosted CLI:** the CLI also supports an OpenAI-hosted environment, which bypasses the OCI sandbox branch shown here.
- **Independent OCI Responses:** `geniagent.oci_inference` sends a separate `/responses` request through `scripts/oci_responses.sh`, using an OCI key and OCI response IDs. It can run locally or issue curl inside an existing sandbox. This is not the OpenAI Agents API session path.
- **Uncertain execution:** retain submission journals and remote identifiers for reconciliation; do not blindly replay an uncertain write. Folio records `needs_review` or `failed` as appropriate. Ticker returns an error with saved evidence. Provider failures do not produce fabricated prices.
- **Deletion:** Folio revokes owner access first and asynchronously removes remote artifacts/session/sandbox resources. Cleanup remains pending/retrying until deletion is confirmed.
- **Credentials:** Folio resolves dedicated OCI Vault `CURRENT` secret versions at startup. Authentication, database, application API, and executor credentials have separate roles. The diagram omits credential values and private resource IDs.

## Code evidence

These paths and line references were read directly in the working tree. A file hash manifest accompanies this document. The native Archify commit-source verifier was not used because it reads committed blobs and would omit the current untracked Folio implementation.

| Diagram responsibility | Inspected source |
|---|---|
| CLI agent/session lifecycle and local function handling | `geniagent/app.py:124`, `:169`, `:197`, `:272`, `:309` |
| Bash/curl request and event-stream adapters | `geniagent/transport.py:63`, `:106`, `:127`, `:137` |
| OCI environment provisioning, executor and file operations | `geniagent/oci_runtime.py:36`, `:156` |
| Ticker API and request orchestration | `stock-demo/backend/server.py:46`; `stock-demo/backend/agent.py:98`, `:107`, `:126` |
| Folio API, ownership, job submission, progress and downloads | `portfolio-advisor/portfolio_advisor/api.py:36`, `:166`, `:190`, `:231` |
| Folio SSO, cookies, CSRF and enabled-user checks | `portfolio-advisor/portfolio_advisor/auth.py:19`, `:40`, `:63`, `:88` |
| Durable jobs, idempotency and locking | `portfolio-advisor/portfolio_advisor/db.py:94`, `:157`, `:193` |
| Worker execution, report storage, error state and cleanup | `portfolio-advisor/portfolio_advisor/worker.py:38`, `:83`, `:151` |
| Session creation/claim, event collection and verified execution | `portfolio-advisor/portfolio_advisor/remote.py:78`, `:91`, `:237`, `:289` |
| Sandbox quotes and deterministic calculations | `portfolio-advisor/portfolio_advisor/sandbox_entry.py:8`; `market.py:11`; `analytics.py:81` |
| Private object storage vs local rehearsal storage | `portfolio-advisor/portfolio_advisor/storage.py:8`, `:34`, `:62` |
| Dedicated Vault resolution | `portfolio-advisor/portfolio_advisor/config.py:67` |
| One API and two hosted workers | `portfolio-advisor/deploy/supervise.py:1` |
| Separate OCI Responses path | `geniagent/oci_inference.py:17`, `:82`, `:90`, `:143` |

## Diagram verification

- [Deterministic delivery receipt](end-to-end.delivery.json): source/artifact SHA-256, byte counts, nine artifact checks, and composition results.
- [Automated browser receipt](end-to-end.visual-check.json): exact delivered artifact, viewport containment, text readability, viewer controls, and captures in light/dark themes.
- [Browser screenshot contact sheet](end-to-end.visual-check.html).
- [Working-tree evidence manifest](source-evidence.json): hashes of inspected implementation files, independent of diagram validation.

The browser check and a separate visual review verify the diagram artifact. They do not test application APIs, database availability, deployed services, model responses, or cloud connectivity.
