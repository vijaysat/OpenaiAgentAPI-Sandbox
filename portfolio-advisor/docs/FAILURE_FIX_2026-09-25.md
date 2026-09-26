# Hosted analysis failure repair — September 25, 2026

## Confirmed cause

A journaled request using the dedicated application key returned HTTP 403, request `req_752a0e38239142c2a0c280874aa65c9f`, with “The caller is not authorized to retrieve models.” The Vault key matched the masked OpenAI entry `genai-sandbox-test1`; its List models and Responses permissions were both None. Agent metadata reads succeeded, so those reads were insufficient credential validation.

The user explicitly approved List models Read and Responses Write. Those two changes were saved without changing the key value, other permissions or expiration. A read of `gpt-5.6-luna` with the same Vault key then returned HTTP 200. Complete session history at diagnosis contained only the two earlier dedicated-agent sessions; this supports the permission diagnosis but is not used as proof to clear each legacy unknown request.

## Changes

- Provider error receipts retain only safe status/code/request identifiers; raw response bodies are not put into run state or the UI.
- Confirmed session-creation rejection (400/401/403/404/422) permits a deliberate retry on the same run after remediation. Timeouts, 5xx, ambiguous responses and legacy unknown markers cannot be blindly replayed.
- Cleanup can proceed when a saved receipt confirms session creation was rejected. Historical unknown creations remain pending operator reconciliation.
- Run evidence displays a support reference, failure stage and safe provider receipt. Unknown session creation directs the user to support instead of another fruitless recovery click.
- OCI startup recovery resumes the retained sandbox and recorded setup command. Lost setup/executor receipts remain blocked; expired compute is not replaced automatically. Typed failure codes distinguish startup timeout, stopped resources and uncertain setup, retaining a safe OCI correlation ID.
- Each target must be finite and within 0–100, with a 100% total. Invalid fields show feedback and disable analysis.
- Upload dismissal restores focus to its opener. Deletion uses an explicit accessible dialog with initial focus on Cancel; cancellation preserves data and restores focus.
- Example CSV downloads retain their object URL long enough for the browser to consume it; hosted delivery remains a separate verification.

## Verification

**Local automated:** 177 passed, 4 skipped, 4 subtests passed. The four skips require an isolated PostgreSQL database. Frontend typecheck and production build passed. Local browser checks confirmed invalid 101/-6/5 weights disable analysis, Escape returns focus to Import holdings, and deletion Cancel preserves the example and returns focus to the delete opener. These local checks use synthetic data and do not certify live model execution.

**Image:** `fix-5ef0384f31d52d90`, digest `sha256:32d3589e097290f4d5761718d8f0e5fa758762bf97f8083243a8ad77102f8c20`. Published with existing private registry access. Every merged filesystem entry was compared against the prior active digest; only the reviewed application code and bundled frontend files changed. Runtime configuration, architecture, non-root user and package inventory were unchanged. OCI exact-tag scan reports 14 findings, highest severity HIGH (no CRITICAL). Activation keeps vulnerability scanning required.

**Hosted first patch:** ACTIVE at 13:47 UTC with matching digest; all seven HTTP smoke checks passed. The existing signed-in dashboard and saved portfolios survived replacement. Live browser checks passed invalid 101/-6/5 target rejection, upload Escape/focus restoration and explicit deletion Cancel/focus restoration. The browser did not emit a download event for the example CSV, so file delivery remains unverified.

**Fresh live run:** `26477da4-1f48-482c-913b-589a604a1048` created its OpenAI session successfully, then stopped at OCI startup. OCI sandbox `sbx-cgsx636nm2awx7l5`, created 13:48:02 UTC, remained CREATING on subsequent checks. The project is ACTIVE, runtime python-3.11, SMALL shape, 30-minute expiration, with the approved ALLOW_ALL networking. No completed analysis or report is claimed. The recovery patch `fix-59aff9ad917557ef`, digest `sha256:768bce6035be51aa704f700ea4bd1057f66cce7688af94f2938271e817f67ced`, is ACTIVE with matching registry identity. Complete merged-file comparison passed with unchanged runtime and base packages. Its exact-image scan reports 14 findings, highest HIGH, no CRITICAL. All seven hosted checks passed at 14:00:58 UTC. The same saved run was then reconciled through the hosted UI, with no new analysis submitted.

**Recovery browser result — 14:04 UTC:** The same run returned Needs Review with `OCI_STARTUP_TIMEOUT` and a visible OCI request reference. Sandbox inventory still contained exactly the retained new sandbox and the earlier STOPPED sandbox; no replacement was created. The new session remained at zero turns before recovery, and recovery remained in provisioning without reaching model submission. The hosted UI did not show a fabricated valuation, proposal or report.

OCI correlation reference from the final hosted startup poll:
`17C37FD77E9B4DF9B60E6B5E7CE4476B/E60D573B9139E32F617FB66A4F5244BD/E4927581B5F6D6FBA725DF96427C95DE`.
The independent command-list diagnostic returned `409 SandboxNotReady`. The service-side startup cause remains unknown; investigation should use this reference, project ID from the private diagnostic, sandbox `sbx-cgsx636nm2awx7l5`, region `us-chicago-1`, and creation time 13:48:02 UTC. Do not widen IAM or create more sandboxes based on an unverified cause. The two temporary local UI-check servers were stopped; the hosted application remains active.

## Preserved evidence and limits

The original sandbox `sbx-kehcvzxetauvd3b3` is STOPPED after expiry. No replacement was silently attached to that original analysis. Legacy unconfirmed session creations and the earlier deletion cleanup are not marked resolved without adequate evidence. Original portfolios and saved reviews are preserved.

Private journals: `deploy/private/session-create-diagnosis.json`, `session-diagnosis.json`, `patch-5ef0384f31d52d90/`, `patch-59aff9ad917557ef/`, `recovery-live-session.json`, and `sandbox-startup-diagnosis.json`. No credential values were added to images, deployment inputs or logs.
