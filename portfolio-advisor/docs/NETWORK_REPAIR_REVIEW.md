# Dedicated demo network workaround

Status: approved by the user with “Allow All” on 2026-09-24, applied under `user-2026-09-24-allow-all`, and verified ACTIVE with the exact reviewed ALLOW_ALL configuration. The retained Luna session is connected to OCI sandbox `sbx-yfpfqwfhks7nyyau`; npm setup exited 0, executor `cmd-f31c9b991d1f` is running, and all four required hostnames resolve. Yahoo VTI/BND quote retrieval also succeeded inside this sandbox. No model prompt was submitted. See [live verification](VERIFICATION.md#approved-allow_all-connection--successful-live-verification).

## Exact change

Only change the network policy on `portfolio-advisor-sandbox` in `us-chicago-1`:

`ocid1.generativeaiproject.oc1.us-chicago-1.amaaaaaafigrwqyadjp6z6pvvhkapjpijibpxdgt66yeoevnr2ndxup7cuua`

| Before | Proposed demo exception |
|---|---|
| MANAGED / ALLOW: the four approved HTTPS hosts on 443 and `216.146.35.35` on 53 | MANAGED / ALLOW_ALL: unrestricted outbound access |

The connected sandbox expires at **2026-09-25 00:56:03 UTC (September 24, 8:56 PM EDT)**. ALLOW_ALL remains the project policy after that expiration unless explicitly restored.

This is a persistent project policy until explicitly restored, including future sandboxes in this project. A sandbox's 30-minute expiration does **not** restore the project policy. The exception does not prove the original restricted policy works and is not acceptance of restricted hosted networking.

The exact current sandbox settings, policy and proposed replacement were captured in the protected rollout journal under plan `0bcbc7e2-7118-4e61-a487-ce87f5208434`. Ownership matches this deployment. Keep sandbox enablement, disabled persistence and zero warm-pool counts unchanged. Do not alter other projects, IAM, inbound endpoints or the application's default restricted project-creation policy.

## Why this is proposed

Repeated live checks in this project found UDP DNS timeouts and immediate TCP resets to its configured resolver. Forcing TCP did not fix resolution. The exposed preview project API has managed ALLOW, ALLOW_ALL and DENY_ALL policies; it does not expose a separate UDP DNS control. An ALLOW_ALL demo exception is a workaround to test, not a verified root-cause repair. A restricted production solution still requires OCI service remediation or separately reviewed customer networking.

This expands outbound access beyond the earlier resolver-only authorization. The user's explicit ALLOW_ALL approval covers this dedicated demo project and restoration of the saved policy if connection verification fails. No further network confirmation is required.

## Recorded apply and verification workflow

```bash
PYTHONDONTWRITEBYTECODE=1 portfolio-advisor/.venv/bin/python portfolio-advisor/scripts/change_sandbox_network.py apply --review-id user-2026-09-24-allow-all
PYTHONDONTWRITEBYTECODE=1 portfolio-advisor/.venv/bin/python portfolio-advisor/scripts/change_sandbox_network.py status
```

Wait for ACTIVE and `matches_demo_exception: true`. Create at most one replacement sandbox for the retained empty Luna session using `connect_environment.py --replace-deleted`. Recheck the previous sandbox's confirmed absence before replacement. Verify npm setup, successful OCI executor startup and OpenAI's connected environment state. Do not replay any uncertain prompt or claim completed analysis from connection alone. Use only synthetic demo holdings for later live analysis verification.

The existing project is reused, so no new VCN, NAT gateway, database, IAM policy or hosted application is proposed. One SMALL sandbox is limited to 30 minutes, corresponding to at most 0.5 OCPU-hour and 2 GB-hours from its 1 OCPU / 4 GB shape. Applicable preview sandbox rates remain unverified. Connection verification submits no model prompt; subsequent model usage is metered separately. Clean up failed temporary compute and confirm absence.

## Exact rollback

```bash
PYTHONDONTWRITEBYTECODE=1 portfolio-advisor/.venv/bin/python portfolio-advisor/scripts/change_sandbox_network.py restore --review-id user-2026-09-24-allow-all-rollback
PYTHONDONTWRITEBYTECODE=1 portfolio-advisor/.venv/bin/python portfolio-advisor/scripts/change_sandbox_network.py status
```

Wait for ACTIVE and `matches_original: true`. Restoration uses the captured four-host HTTPS plus resolver-only policy and preserves all other sandbox settings. Restoring the failed DNS configuration may disconnect the executor or prevent future startup. If the exception does not restore connectivity, clean up the new sandbox and restore the saved policy during the same attempt. Preserve the Vault, OpenAI session and diagnostic evidence.

The script verifies project ownership, compares the entire sandbox configuration against the reviewed source, submits with the current ETag, and journals intent before writing. Lost responses are reconciled against exact ACTIVE readback; an unconfirmed update is never blindly replayed. Drift stops the operation. Local tests cover apply, exact rollback, ownership/configuration drift, missing approval references and uncertain writes.

The first update attempt was rejected locally because the preview SDK does not accept `opc_retry_token` for project updates. That rejection is preserved in the journal; a fresh read confirmed the original policy before resubmission. The corrected script sends `opc_request_id` for correlation, the current ETag, and an explicit no-retry strategy. A test exercises the actual preview SDK method with a fake transport. The network/rollout/connection checks passed (23 tests), as did all 50 existing core regressions.
