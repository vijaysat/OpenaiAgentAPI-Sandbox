# Ticker — stock lookup demo

A separate React Native / Expo application demonstrating the **OpenAI Agents API**.
Search a company name, choose the correct symbol and exchange, and view its latest
available regular-session price, currency, daily change, and quote timestamp.

Every search and quote request adds a turn to the existing Agents API session
selected with the required `--session-id` argument. Replace `SESSION_ID` in the
commands below with the exact ID of your connected session.

Its **GeniAgent-G** agent runs a stock helper using `command_execution` in its
connected OCI workspace. The UI shows the session ID, turn ID, actual session
model, completed commands, and agent response. No replacement agent or session
is created, and the existing agent definition is unchanged.

The Python backend reuses the workspace's curl-based Agents API transport and
loads `OPENAI_API_KEY` from the root workspace `.env`. Credentials stay on the
server. This uses your OpenAI project's API access and billing. Yahoo Finance is
the data provider behind the tools; the browser has no direct stock-data bypass.

The local service uploads a request-specific helper and input to OCI, asks the
agent to run the exact command, then reads the result file back from OCI. Stock
requests execute in the sandbox. The service checks successful command execution,
root turn completion, helper integrity, and matching request IDs before displaying
data. Model text is never used to populate prices.

## Start locally

Requires Node 22.13 or later and the workspace's `.venv-oci` Python environment.
The lockfile uses Expo 57.0.31, React Native 0.86.3, and TypeScript 6.0.3.
The selected session must already have a connected OCI executor and a valid sandbox
mapping under the workspace's `.geniagent/` directory. Set `OPENAI_PROJECT_ID` and
`OPENAI_API_KEY` in the root `.env`. From the repository root, install once:

```bash
bash scripts/setup_oci.sh
cd stock-demo
npm ci
```

In one terminal, from the repository root, start the data service:

```bash
cd stock-demo
npm run api -- --session-id SESSION_ID
```

In another terminal, from the repository root, start the app in your browser:

```bash
cd stock-demo
npm run web
```

Open http://localhost:8081. Search **Apple**, choose **AAPL** on NASDAQ, and check
the price and **Price as of** timestamp. Try a different company, **Refresh price**,
and an unrecognizable company name. Stop the API to see the connection error state.

To serve the compiled app without the development packager, run `npm run build:web`
once and use `npm run preview` instead of `npm run web`. Keep the API terminal running.
Use only one web server on port 8081 at a time. The API requires `--session-id`
and never chooses a session automatically. Select an existing session with a
connected OCI `/workspace` environment. Both search and quote appear as new turns in the same
session in OpenAI Platform.

OCI compute has a limited lifetime (30 minutes in this workspace's connection
workflow). If it expires, the demo reports the disconnected executor and stops.
Reconnect the same session before trying another lookup. It does not silently
create replacement sessions or fetch prices outside OCI. If OCI authentication
expires, renew it from the workspace root:

```bash
oci session authenticate --profile-name Sandbox --region us-chicago-1
```

Authentication alone does not reconnect an expired sandbox. Inspect retained
resource mappings before reconnecting; the connector deliberately refuses to
overwrite an existing mapping without an explicit lifecycle action.

The stock helper needs outbound access to `query1.finance.yahoo.com`. The
connector's default three-host `allowlist` only covers executor installation and
connection; it does not cover stock data. The recorded live demo used an isolated
`allow-all` project. See the [application README](../README.md#ticker-stock-application)
for the request-flow diagram and full session lifecycle.

## iOS / Android

`npm start` runs Expo for a compatible Expo Go client (SDK 57) or development build.
The iOS simulator uses `localhost:8766`; the Android emulator uses `10.0.2.2:8766`.
For a physical device, put the Mac and phone on the same network, set
`EXPO_PUBLIC_API_URL=http://YOUR_MAC_LAN_IP:8766` in this folder's `.env`, and start
the API with `npm run api -- --session-id SESSION_ID --host 0.0.0.0`. Restart Expo after changing
the URL. The API defaults to loopback; use the LAN binding only on a trusted network.
Do not put credentials in an `EXPO_PUBLIC_*` variable.

## Data details

- Uses Yahoo Finance's public `/v1/finance/search` and `/v8/finance/chart` endpoints
  from the stock helper executed by the OpenAI agent in OCI.
  This is an unofficial data integration, without a
  guaranteed service contract; endpoints may be rate limited or change.
- Search results include company equities; futures, funds, and cryptocurrencies
  are excluded. Choose the listing yourself so similarly named companies or
  multiple exchanges are not silently conflated.
- Price is `regularMarketPrice`, not after-hours price or a promised real-time
  quote. **Price as of** comes from `regularMarketTime`; **Retrieved** is the time
  this API fetched the response. Closed markets may show a prior session's price.
- Daily change uses the previous close from a **one-day** chart. Missing change
  data is shown as a dash; prices, timestamps, and currencies are never invented.
- Currency units are preserved, including `GBp` (pence). Numbers are displayed
  separately from the currency code to avoid accidentally converting pence to pounds.
- A root `agent.session.turn.completed` and successful matching stock command are both required
  before showing a result. Model text alone is never used to populate prices.
- Each lookup writes a unique JSON result in the OCI workspace. The API
  accepts request IDs and returns cached completed responses on identical retries.
  Failed or ambiguous requests are not automatically resubmitted. Each request's
  report, command, response, and server events are in
  `.agent-state/sessions/SESSION_ID/requests/REQUEST_ID/`. Inspect these before retrying a timeout;
  leaving the browser does not cancel a remote turn.
- Requests time out, provider and Agents API failures are shown to the user, and
  superseded responses cannot overwrite a newer selection. There is no polling.
- Local browser origins at port 8081 or 19006 are allowed. If you intentionally use
  another browser origin, set `STOCK_DEMO_ORIGINS` on the API (comma-separated).

## API

```text
GET http://127.0.0.1:8766/api/health
POST http://127.0.0.1:8766/api/search
  {"query":"Apple","requestId":"a-new-UUID"}
POST http://127.0.0.1:8766/api/quote
  {"symbol":"AAPL","requestId":"another-new-UUID"}
```

POSTs require `Content-Type: application/json`. No route creates agents or sessions.
Health reports the configured session; it does not claim remote connectivity.
This is a local demo service; do not expose it as a public unauthenticated API.

## Checks

```bash
npm run typecheck
npm run validate
npm run test:api
npm run build:web
```

The API tests use controlled provider and Agents API responses, covering symbol filtering,
exchange disambiguation, currency preservation, previous-close calculation,
timestamps, malformed payloads, and missing data. They do not prove live data
availability. Agent tests check reuse of the exact session, stream-before-input
ordering, OCI file readback, cached retries, disconnected/busy sessions, and rejection
of stale turns, mismatched commands, and results for another request.
Component validation checks the exported readonly props interfaces,
native primitives, theme colors, UI copy, and interactive accessibility labels.

Validation on September 22, 2026: **16 stock backend tests passed**, along with
TypeScript, component conventions (9 files), dependency checks, and the web export.
The regression suite includes OCI transport errors without numeric HTTP status
codes; the service returns HTTP 502 and retains redacted session evidence.
See [workspace validation](../README.md#validation-and-troubleshooting) for all
66 automated tests and the dated live browser results. Native-device execution
has not been validated. A later recorded NVIDIA lookup hit an OpenAI rate limit;
the earlier Apple search and AAPL quote completed successfully.

## Structure and design

`backend/agent.py` continues the selected Agents API session; `backend/provider.py`
and `backend/sandbox_runner.py` form the helper uploaded to OCI;
`backend/server.py` exposes the local demo routes. Shared workspace transport and
OCI libraries are reused without changes. The workspace state lock serializes
demo activity with the CLI.
`src/hooks/` handles requests and state.
`src/components/atoms`, `molecules`, and `organisms` contain reusable native UI;
`src/screens/StockScreen.tsx` composes the single screen. All UI copy lives in
`src/data/mockData.ts` (despite that skill-mandated name, it contains no quote
fixtures), and design tokens live in `src/theme.ts`.

The user approved an original design because Stitch MCP was unavailable. No
Stitch screen retrieval or design synchronization is claimed. React Native web
support follows [Expo's web workflow](https://docs.expo.dev/workflow/web/).
