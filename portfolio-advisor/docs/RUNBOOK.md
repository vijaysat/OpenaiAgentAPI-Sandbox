# Operations runbook

[Open the active OCI application](https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/20251112/hostedApplications/ocid1.generativeaihostedapplication.oc1.us-chicago-1.amaaaaaafigrwqyacofmt5zjaripoblrh47ydu7shpjoljjlgkmb5enhybta/actions/invoke/). Sign in with Oracle. See [current verification](VERIFICATION.md) for the deployed image and remaining live-analysis checks.

## User authorization

Oracle sign-in also requires enabled application access. See [People and application access](ACCESS_ADMIN.md) for grants, roles, revocation, initial administrator configuration and recovery. Administrators manage access without gaining access to other users’ portfolios. Schema version 2 persists the directory and audit history in PostgreSQL.

## Startup and health

Use the root `.env` for non-secret values and Vault references; protect it with mode 0600. Live startup resolves CURRENT versions before dependencies initialize. Errors identify only the variable/stage. Never enable SDK HTTP wire logging, shell tracing, or container environment dumps. PostgreSQL connections must verify the managed-service certificate using its actual CA; put the non-secret CA file in the runtime trust bundle and include `sslrootcert` in the Vault-held URL.

`/healthz` reports API/database reachability and whether a worker heartbeat is fresh. It does not certify OpenAI/OCI. Supervision stops the API and both workers if a child exits; the host should restart the container. PostgreSQL and Object Storage hold durable state independently of container files.

One application replica and two workers are the reviewed defaults. `active_owner` enforces one queued/running job per user. Row locks claim jobs; connection-scoped advisory locks prevent concurrent execution/cleanup. A heartbeat older than 90 seconds allows a new worker to reconcile the saved run. It does not authorize another POST for an uncertain submission.

## Failed or interrupted run

Startup-only failures are refreshed from OCI every 30 seconds using read-only requests. In Run evidence, compare workspace status, expiry and the last checked time. If the workspace becomes RUNNING, continue the same saved analysis. A confirmed stopped workspace with no submitted setup or analysis cannot be resumed; keep its history and start a new analysis only when OCI startup is available. Uncertain command submissions still require operator reconciliation. See [the September 25 investigation](WORKSPACE_STARTUP_REPAIR_2026-09-25.md) for the service-side startup blocker and retained diagnostic request IDs.

1. Record the run ID and current status from the owner UI. Inspect `runs.state` through an authorized database session for `session`, `provision_attempted`, `<session>-oci.json`, `manifest`, `submission`, `completed_turn`, and `turn_evidence`. Do not paste credential/environment values into tickets.
2. An accepted event POST is not completion. Inspect the session's root turns and items via the existing curl transport. Require exact turn identity, successful helper command, unchanged files and validated artifact.
3. For `needs_review`, inspect **Run evidence** for the support reference, stage, HTTP status and provider request ID. A session creation rejected with HTTP 400/401/403/404/422 is retained as `session_creating.outcome=rejected`; **Retry after access is corrected** deliberately retries that same run after the cause is resolved. For HTTP 403, verify the dedicated application key has List models Read, Agents Write and Responses Write; a successful agent GET alone does not prove session-creation permission. Timeouts, 5xx and legacy creation markers remain uncertain and require operator reconciliation. They are never automatically replayed. For `OCI_STARTUP_TIMEOUT`, **Reconcile saved run** polls the same retained sandbox and resumes only recorded provisioning steps. It does not create replacement compute. Saved setup commands are inspected; uncertain setup/executor submissions are not replayed. Other **Reconcile saved run** actions read existing history. When no new root turn follows a recorded submission intent, the worker stops rather than resending. A confirmed failed root turn also stays reviewable.
4. Unconfirmed agent/session/sandbox creation needs operator reconciliation using recorded IDs and retry tokens. Do not clear journal markers simply to make the UI retry. If the provider confirms non-creation, archive the evidence before an explicitly authorized new attempt.
5. A stopped/expired sandbox needs a new analysis, not a fabricated continuation. Saved reports remain available. If quota/authentication fails, resolve it first; restarting workers cannot create credits or renew a local security-token login.

The app intentionally does not expose raw provider messages to users. Compare event stage, retained resource IDs and redacted error category. A worker or connected executor alone is not proof of model success.

## Deletion and retention

DELETE marks the portfolio inaccessible immediately. Every owner API, stream poll and download checks this marker. Workers avoid publishing results after revocation. Cleanup waits for active workers, stops/deletes OCI sandboxes, confirms 404, deletes the associated OpenAI sessions and confirms 404, and removes the artifact prefix. It removes holdings/run/event data only after those steps succeed. Status stays `retrying` on any failure; no deletion-success claim is made early.

The owner/id cleanup tombstone remains, containing no holdings or reports. PostgreSQL backups retain prior records for up to seven days under the proposed policy. Provider-side OpenAI retention is governed by the project's account settings and is not overridden by the app. Review that account setting before real portfolio data is used. Artifact bucket versioning is disabled; do not enable retention/versioning without adapting and retesting deletion.

## Secrets and rotation

Use the five dedicated names in README. Generate the app session secret only during approved rollout. Seed DB/SSO credentials directly to Vault and keep the DB administrator password in its separate secret. Update CURRENT and restart the app to resolve rotated values. Rotating AUTH_SESSION_SECRET signs everyone out. The app and executor keys must differ; an OCI inference key is neither.

Runtime IAM names exact secret OCIDs and exact principal IDs, not wildcards. OCI uses the application principal for image pulls and the deployment principal inside the container. Retain both IDs in Terraform inputs; the five secret conditions exclude the separate database administrator secret. The build identity cannot read them. A failed Vault access check stops startup; do not bypass it with a literal credential in `.env` or image build args. Local development uses `Sandbox`; hosted mode uses resource principals.

## Deployment, image identity and rollback

When a project's metadata is ACTIVE but every workspace remains CREATING, verify the project move history and test actual command execution before assigning a cause. On September 25, a fresh project in the same dedicated compartment and with identical settings restored startup. The original moved project's internal failure remains undiagnosed. See [the repair evidence](WORKSPACE_STARTUP_REPAIR_2026-09-25.md).

`rollout.py select-sandbox-project --sandbox-project <owned-project-id> --sandbox <verified-sandbox-id> --command-id <readiness-command-id> --journal <rollout-journal> --review-id <approved-review>` selects a tested replacement for future work. The command must have exited 0 and printed `OCI_STARTUP_READY <version>`; project ownership, settings and sandbox association are verified live. Run `environment` and `configure` afterward and check ACTIVE state, configuration readback and a hosted analysis. Selection preserves original creation/move receipts and existing analysis mappings. Never edit a saved run to point at replacement compute. Existing network rollback plans are project-specific and must not be reused for another project.

For configuration rollback, restore the captured previous hosted environment through a new journaled configuration operation, then verify ACTIVE state and readback. Retain the original project and databases. A rollback to the known-broken project restores configuration only, not working analysis. Reconcile the selected-project record and local references with the rolled-back environment before future configuration generation.

An OpenAI `usage_limit_exceeded` or `credit_balance_exhausted` failure requires the organization's administrator to resolve the API usage/billing limit. Preserve the failed root turn; do not automatically resubmit it or substitute sample prices. A connected OCI executor does not establish completed model execution. After the limit is resolved, start one explicitly requested new analysis and verify command, root-turn and artifact evidence.

Read ROLLOUT_REVIEW.md before any provisioning. This rollout uses `deploy/private/vault-bootstrap.json`; keep it in protected durable operator storage. Its operations are journaled before network writes; it stores no secret content. Back it up separately from the application image. Terraform runs in the dedicated OCI Resource Manager stack, with state access restricted to its operators.

Build the frontend with the actual hosted application prefix. Static assets, API, SSO callback, cookies, events and download URLs all use that public prefix. OCI strips it and rewrites Host on the internal hop; hosted middleware restores the configured path/origin before authentication, without trusting caller-supplied forwarding headers. Run both gateway and direct-prefix tests plus a hosted smoke check after activation. A root-path local build does not prove the hosted proxy works.

A unique commit-derived OCIR tag must resolve to the reviewed digest before activation. This region rejected the repository immutability option, so the publisher refuses an existing tag and verifies the digest again after activation. Deployment status must show the expected ACTIVE artifact ID, repository URI and tag. Record work request ID, digest, timestamp and smoke results. Restore only a compatible authorization-aware artifact on rollback; do not activate the older schema-1 unrestricted-SSO image after the access migration. See ACCESS_ADMIN.md. Do not destroy PostgreSQL/Vault as an image rollback.

For application-only fixes, `scripts/patch_hosted_image.py prepare` builds with the real URL prefix and prepares a content-addressed layer over the verified active digest. `publish --journal <prepared-path>` uses existing private registry access; `verify --journal <prepared-path>` compares every merged file and requires unchanged runtime configuration, platform, user and package inventory. Only its explicitly hashed code files and frontend assets may differ. Reconcile an uncertain push at the saved target; do not choose a new tag to bypass it. Register and activate through `rollout.py deploy` only after verification, keeping the mandatory OCI scan enabled.

## Hosted acceptance script (manual, authorized live scope)

Use two internal test identities and synthetic holdings. Verify SSO login/logout, each user's list/run/events/download isolation, malformed uploads, exact quote timestamps, completed root-turn/OCI/command/hash evidence, HTML/CSV/JSON downloads, follow-up session identity, restart recovery without repeated POST, storage durability after replacing the container, deletion revocation and eventual confirmed remote cleanup. Test Yahoo/OpenAI/npm egress under the approved ALLOW_ALL mode. Blocked-destination checks apply only after an explicitly approved return to restricted networking; an allowlist declaration is not network proof. Verify secret denial stops a fresh instance without printing values. Compare the actual active artifact against OCIR digest and record all results in VERIFICATION.md.

### Replacing a confirmed deleted connection sandbox

Retain setup-command and network diagnostic evidence before cleaning up failed temporary compute. After OCI confirms the old sandbox is absent with HTTP 404, `scripts/connect_environment.py --replace-deleted --review-id <approved-review>` archives its mapping and provisions one replacement for the same empty OpenAI session. It refuses replacement when the environment is connected, cleanup is unconfirmed, or OCI returns an authentication or transport error. Never remove a mapping to bypass reconciliation.

The user separately approved the [demo ALLOW_ALL exception](NETWORK_REPAIR_REVIEW.md), which is now ACTIVE and allowed the retained Luna session to connect. All four required hostnames resolved and Yahoo VTI/BND quote retrieval succeeded inside OCI. The original restricted policy remains captured for exact rollback; its DNS issue remains unresolved. `change_sandbox_network.py prepare` and `status` only read OCI; `apply` and `restore` journal guarded updates. Project updates use ETag protection, a request correlation ID, and no automatic retries. Do not treat a review-id argument as approval by itself.

The bootstrap sandbox `sbx-yfpfqwfhks7nyyau` expired at 2026-09-25 00:56:03 UTC (September 24, 8:56 PM EDT), was confirmed STOPPED, and was then deleted with an independent 404 check. Its stale mapping was removed from root `.env`; the first live analysis must provision fresh compute. The retained Luna session has zero turns and is disconnected. The ALLOW_ALL project policy persists after sandbox expiration. Historical connection and quote probes are not completed model-analysis evidence.
