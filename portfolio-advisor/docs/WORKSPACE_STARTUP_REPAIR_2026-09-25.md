# OCI workspace startup investigation — September 25, 2026

**Latest outcome:** The hosted analysis started at 20:31:31 UTC completed successfully using gpt-5.6-luna and OCI Sandbox. Its command, input/helper/result hashes, timestamped Yahoo quotes and report are present. See the final section for the successful recheck; earlier sections retain the original startup and billing failures.

## Application correction

A run that exceeded the three-minute startup wait retained an old `OCI_STARTUP_TIMEOUT` message indefinitely. A stopped workspace could therefore continue to appear as starting, and the user was offered reconciliation of a workspace that could never resume.

The runtime now persists allowlisted lifecycle evidence (state, created/expiry/check times and provider request reference), without storing provider environment values. The worker refreshes startup-only failures using read-only OCI requests, with the run lock and a shared 30-second check interval. The open UI refreshes that saved evidence. A workspace that becomes RUNNING offers continuation of the same analysis. A confirmed stopped workspace with no submitted setup, executor or agent input directs the user to a new analysis and rejects attempts to resume the ended workspace. The old review remains in history. Uncertain writes still require reconciliation; no replacement sandbox or agent input is automatically submitted.

New workspace creation uses the preview SDK's `expires_in_minutes=30` field. The previous deprecated duration field already produced the correct expiry; this change is not evidence of a startup root cause.

## Live OCI evidence

Dedicated region: `us-chicago-1`. Project: `ocid1.generativeaiproject.oc1.us-chicago-1.amaaaaaafigrwqyadjp6z6pvvhkapjpijibpxdgt66yeoevnr2ndxup7cuua`. The project is ACTIVE, sandbox enabled, with the previously approved managed ALLOW_ALL network policy.

- Saved review `26477da4-1f48-482c-913b-589a604a1048`: its workspace `sbx-cgsx636nm2awx7l5` was confirmed STOPPED; created 13:48:02 UTC, expiry 14:18:02 UTC. The old UI still showed its earlier startup timeout.
- Latest review `cb4de256-4bb6-4bea-a3c7-3ed481ec9223`, created 14:53:05 UTC, also showed `OCI_STARTUP_TIMEOUT`. The UI retained provider request `674EAD0B568E49F78D7DA0D7DC0898DE/61EFC8AED2F79B1A8A8BB66A4F5244C2/F9AE0D30992B6F35E842196856F3D303`.
- Direct SDK diagnostic `sbx-zt6zzqrxswe4czs3`: python-3.11, SMALL, created 14:59:25 UTC, still CREATING at 15:12:29 UTC. No setup command or OpenAI call was sent. Creation request: `1FD4326D3035436B8C6A0BC8D28880C8/591D46A30AE3E81F4D8CB66A4F5244FE/2FB5AB3938478B9A772372D2E71064F9`.
- One bounded shape comparison, `sbx-jae45nuwf2fcw7oy`: python-3.11, MEDIUM, created 15:06:48 UTC with a 15-minute expiry, still CREATING at 15:12:29 UTC. Creation request: `9B2CD1C3DB9240D0A63AE057A38F9713/AEA444A110E6A98A088EB66A4F524428/CA2B28F9F0F60324A6C071056CF62A1C`.

The startup stall occurs before executor installation or model execution through both the application and direct OCI SDK. This rules out a portfolio calculation or OpenAI response as the cause of these specific startup failures. It does not establish whether OCI capacity, runtime-image startup, or another internal provisioning problem is responsible. OCI exposes no diagnostic lifecycle detail on these sandbox responses. Longer waiting or replacement sessions have not been demonstrated to repair it.

Stop and delete attempts for both diagnostics initially returned HTTP 409 `InvalidLifecycleState` while CREATING. Expiry alone is not confirmed deletion. The protected journal `deploy/private/startup-repair-probe.json` records subsequent observations and cleanup results. No unrelated sandbox was modified.

## Verification

- Core and Portfolio Advisor Python suites: **210 passed, 4 skipped, 4 subtests passed**. Skipped tests require an isolated PostgreSQL test database.
- TypeScript and production frontend build: passed.
- Added tests cover lifecycle timestamps and redaction, stopped and subsequently ready workspaces, no replay of uncertain submissions, read failures, deletion isolation, a simultaneous resume, and the API's refusal to resume a confirmed ended workspace.
- Separate stock demo suite: 10 passed, 6 failed, 5 subtests passed. All six failures use the existing `DEFAULT_SESSION_ID = "<SESSION_ID>"` placeholder, rejected before OCI is called. The stock demo was not changed for this repair. Re-running its 16 tests with a valid synthetic session ID injected only in the test process passed all 16; this is a fixture-controlled check, not an unmodified-suite pass.

Hosted image activation and browser results are recorded below after verification. A healthy hosted API or passing simulated tests do not prove an OCI sandbox can start or an analysis can finish.

## Hosted activation and browser verification

Image `fix-c860acf42c68273d` is ACTIVE with registry and active-artifact digest `sha256:9eb9b5f4be0b8ff5b3e7698e966490ff37a015190f2e6befad19576864ee4134`. The merged filesystem, non-root identity and unchanged package inventory were verified. Mandatory OCI scanning stayed enabled; its 14 findings (highest HIGH) match the base image. All ten hosted UI/assets, API/database/worker, anonymous-access and SSO-entry checks passed at 15:16:19 UTC.

Authenticated browser verification confirmed that the expired 09:47 EDT review now shows STOPPED, its actual 10:18 EDT expiry, a fresh provider request reference, and guidance to start a new analysis. Its obsolete reconciliation button is absent. The latest 10:53 EDT review shows CREATING, the 11:23 EDT expiry, a current check time, and the new Check workspace again action. Both portfolios and all three existing reviews remained available. The user-selected administrator still has Manage access.

## Guide and service escalation

The [Oracle GenAI Sandboxes User Guide](https://confluence.oraclecorp.com/confluence/display/OCAS/GenAI+Sandboxes+User+Guide), last updated September 23, was successfully read in the signed-in browser during this investigation. It confirms Chicago availability, the python-3.11 runtime, and SMALL/MEDIUM support. It identifies [#genai-sandbox-users](https://oracle.enterprise.slack.com/archives/C0B4P9NCXGB) as the support channel. No message or ticket was sent.

The documented regional limits were checked through OCI Limits: sandbox-cpu-count = 32 and sandbox-memory-gb = 64. Resource availability returned null for usage, remaining capacity and effective quota; those nulls do not mean zero usage or available capacity. A capacity or quota cause is not established. The service team should inspect the retained project/sandbox creation requests and the CREATING lifecycle that persists past configured expiry, before requesting any limit increase or changing IAM/networking.

The live Check workspace again action completed its bounded check using review cb4de256-4bb6-4bea-a3c7-3ed481ec9223. No replacement sandbox appeared in the dedicated project. The UI continued to show CREATING with fresh check times and no report, including at 15:26:39 UTC, after the saved workspace's 15:23:10 expiry. This is deliberately not labelled STOPPED until OCI confirms a terminal state.

## Diagnostic cleanup follow-up

The MEDIUM diagnostic became STOPPED at the 15:28:43 UTC observation. Deletion was independently confirmed by HTTP 404 at 15:29:14 UTC. The SMALL diagnostic remained CREATING at 15:30 UTC, despite its 15:29:25 configured expiry; its cleanup is still tracked in the protected journal. No saved user-review workspace was deleted.

Final authenticated browser check: the latest review automatically transitioned to STOPPED with check time 15:31:44 UTC. The obsolete reconciliation button disappeared without a page reload, no verified report appeared, and the saved review remained accessible. Both affected saved reviews now show OCI-confirmed terminal states.

The SMALL diagnostic stop/delete attempts after expiry initially returned HTTP 409 InvalidLifecycleState, and a fresh GET still reported CREATING. At the next investigation, 16:01 UTC, OCI reported STOPPED. It was deleted and a separate GET confirmed HTTP 404. Both original diagnostics are now deleted. The exact request references remain in the protected diagnostic journal.

## Recheck after the 12:00 EDT screenshot

Sign-in and the deployed application were rechecked. The same image remains ACTIVE with a matching registry digest. The authenticated UI displays the saved 10:53 EDT review with OCI-confirmed STOPPED state and no report. Signing in does not restart a persisted analysis. The preceding application correction fixed lifecycle reporting and recovery controls; it did not resolve OCI provisioning.

One new data-free diagnostic, `sbx-lgsjto6uvoju2vsu`, used python-3.11 and SMALL in the same dedicated project, with a two-minute expiry. OCI accepted creation at 16:02:44 UTC; checks continued to show CREATING, including after its 16:04:44 expiry. STOPPED was first observed at 16:05:45 UTC. No RUNNING state was observed, and no command, executor, holdings, or OpenAI input was submitted. This short recheck supplements the earlier 15- and 30-minute stalled workspaces; it is not evidence that every future startup will fail.

Creation request reference: `10835B28C4624889A1DA452A7E9A1298/C7869551A41BF2A3249BB66A4F5244C4/FE4076FB1E7D1FCC7466DBBEE950C9D7`.

Terminal-state request reference: `2B2298B1FC204003AB0B460713E30DD2/DC3F79A45DEEC8FFDA9BB66A4F524428/A9E453FDBFDE2D94EEF854E79AA33014`.

The raw OCI API response was also inspected to check whether SDK deserialization omitted diagnostics. It contained only basic sandbox metadata and lifecycle state; no additional startup failure reason was returned. Service-side investigation is still required to identify the provisioning cause. No IAM, network, warm-pool, credential, application code, or deployment changes were made during this recheck.

The new diagnostic was deleted after STOPPED, and an independent GET confirmed HTTP 404 at 16:06:06 UTC. All three temporary startup diagnostics are now deleted. Saved user-review workspaces and history were preserved. The bounded cleanup process completed; no background task remains running. Evidence is retained in `deploy/private/startup-recheck-1600.json`. The diagnostic report has not been sent to the service support channel.

## Working project repair and hosted verification

The original project's last successful connection preceded its compartment move. OCI recorded that move as SUCCEEDED at 01:08:57 UTC on September 25. Subsequent startup failures occurred in the moved project. This timeline suggested testing a fresh project; it does not by itself prove the internal cause of the moved project's failure.

Created one replacement, `ocid1.generativeaiproject.oc1.us-chicago-1.amaaaaaafigrwqyaqjp74ig6wybppsmpbs4m2nbkb2ftijtbu7qu7r5hjroq`, directly in the existing dedicated application compartment. Ownership tags, region, managed ALLOW_ALL policy, disabled persistence and zero warm-pool counts match the original. Its creation work request completed successfully. No IAM, SSO, Vault, database or network access changes were required.

Data-free probe `sbx-mdinytb7wdpewagv` reached RUNNING within the first ten-second polling interval. Command `cmd-8ca3920fd096` exited 0 with `OCI_STARTUP_READY 3.11.13`. A second command, `cmd-4f56ecfc970d`, exited 0, resolved OpenAI/npm/Yahoo, retrieved a timestamped VTI quote from Yahoo and confirmed Python, Node and npm. The probe subsequently expired and was deleted with an independent HTTP 404 check.

The rollout now records `activeSandboxProject` separately from the immutable original project-creation and move receipts. `select-sandbox-project` verifies ownership, identical settings, ACTIVE state, sandbox/project association and successful readiness-command output before selection. Environment regeneration and network diagnostics use the selected project. Existing analysis mappings are not rewritten, resumed in replacement compute or silently resubmitted. Old network-change plans retain their original project identity and cannot be applied to the replacement without a new scoped plan.

The hosted configuration update changed only `OCI_SANDBOX_PROJECT_ID`. The root `.env` retains mode 0600, security-token authentication and Vault references. The previous hosted configuration is retained in `deploy/private/hosted-env-before-project-repair.json`. Both application and deployment were ACTIVE after the change; the existing image digest remained verified.

Through the signed-in hosted UI, analysis `c260ec00-3210-4d18-bfd9-45519f324a68` created workspace `sbx-a3e5esnhwjk4gvje` in the replacement project at 16:24:04 UTC and observed RUNNING at 16:24:10. Setup command `cmd-9e24664d52c3` exited 0. Executor `cmd-4af0118b98c6` started and the OpenAI environment connected. This is live hosted proof that the reported OCI startup failure has been bypassed by the verified replacement project.

OpenAI session `sess_0382677e31fc6707006ab6a022fcb08195b3d29d9d7da1a442` then received one root turn, `turn_0382677e31fc6707006ab6a040637c8195bd33b2fc677febbd`. It failed with `usage_limit_exceeded`: the organization reached a usage or billing limit. Only the user-input message exists; no analysis command or completed report is claimed. Resolving this limit requires the OpenAI organization's administrator. Billing settings and keys were not changed, and input was not replayed.

The application now recognizes usage/credit/rate-limit codes from stream events and retained failed-root-turn history. It records safe, actionable application wording without exposing provider message bodies. Reconciling a failed turn reads its existing evidence and never repeats the input. Local verification: **224 tests passed, 4 skipped, 4 subtests passed** across core and Portfolio Advisor. The skipped cases require an isolated PostgreSQL test database. TypeScript and the production frontend build passed. The first restricted-shell attempt could not bind the local fake API server; the full suite passed with loopback binding permitted.

Repair journals: `deploy/private/project-startup-repair.json`, `deploy/private/repaired-project-agent-evidence.json`, and the updated protected rollout journal. Original portfolios, reports, history and project receipts remain intact. The original project remains available for evidence and eventual user-directed cleanup. No support message was sent.

Reconciliation was also corrected to inspect an already-submitted failed root turn before connecting to OCI. This prevents an expired workspace from masking the known OpenAI billing failure. The regression test proves that this path performs no OCI connection and no model-input POST.

### Final activated image and browser result

Image `fix-03b02a4f18f7e92d`, digest `sha256:cf1c0f93c1e1828bdaa560e77fa81a30e8372a558d7fb3d08f4004f9086750f0`, is ACTIVE with matching registry and artifact identity. Full merged-file verification passed with unchanged base packages, runtime configuration and non-root user. Mandatory scanning remained enabled. All ten hosted UI/assets, API/database/worker, anonymous-access and SSO-entry checks passed at **18:54:04 UTC** after the final activation.

The signed-in browser reconciled the same saved 12:23 EDT analysis after its workspace expired. It now displays the actionable OpenAI organization usage/billing-limit message and `usage_limit_exceeded`, replacing the misleading expired-workspace error. An independent OpenAI history read confirmed exactly one root turn, with the same ID and failed status, and zero command-execution items. No input was replayed. The complete live portfolio report remains blocked on the organization limit; this is not a completed-analysis claim. No billing or account settings were changed.

### Subsequent hosted recheck: successful analysis

During the later recheck, the synthetic analysis `139b347f-3e18-4ad1-9869-8a730c7b9d28`, started at 20:29:46 UTC, stopped at the configured-agent verification guard before workspace creation. No automatic configuration overwrite or replay was performed. A separate, newer analysis started through the application at 20:31:31 UTC subsequently completed and was independently inspected in the signed-in hosted UI:

- Run: `79ff0cd7-34cb-441b-bb26-2b753ee2dce5`.
- OCI sandbox: `sbx-fhhj6ontdgg2ueys`; executor command: `cmd-ac259d19d48b`.
- OpenAI session: `sess_03b596df71f2b021006ab6da259168819583cd703415e55b10`.
- Root turn: `turn_03b596df71f2b021006ab6da3ef1ec8195a55620a5c4814810`.
- Model: `gpt-5.6-luna`; verified analysis command `exec_82aa3c06bbf850109cb5ef8ea46f82d3802ac666aee88f4cba` completed with exit code 0 and the matching turn ID.
- The application displayed input, helper and result hashes and timestamped Yahoo quotes retrieved at approximately 20:32 UTC.
- Independent decimal arithmetic reconciled the displayed holdings total and proposed values; proposed cash was nonnegative. Holdings and amounts are omitted from this document.
- The authenticated printable report download started successfully. CSV and JSON links were present; those downloads and follow-up questions were not retested in this pass.

The OpenAI agent settings visible after completion match the expected instructions, empty additional tools, and Luna model. The cause and timing of the earlier temporary configuration mismatch were not established. This pass changed no agent settings, credentials, billing or deployment. The redundant local OCI sign-in renewal was cancelled once browser verification provided the completed-run evidence. Older failed reviews remain in history; their failure messages are historical and are not the status of this completed run.
