# Registry publishing approval and execution record

## September 25 routing-fix retry — approved

Renewed OCI sign-in was verified at approximately 10:18 UTC. The corrected source `56e30de295f0d0da57a379a5f2cdb24e7a1d0c1f` passed image assembly and runtime validation, but delivery failed at 03:50 UTC with an authorization denial at the initial layer upload. Exact build: `ocid1.devopsbuildrun.oc1.us-chicago-1.amaaaaaafigrwqyaklyppnl4lyrledfqt3z7hx4xng6xo7pn4fuzkk2dpgeq`. The previous permission window was already closed and its policy statement removed. The initial image is still ACTIVE; the routing correction is not deployed.

The user explicitly approved this retry with “Approved.” The new window is recorded as **2026-09-25 10:28:14–10:58:14 UTC** (06:28:14–06:58:14 AM EDT), review ID `user-2026-09-25-routing-fix-registry-30-min-approved`. The authorization covers the same temporary statement below for **one retry of this corrected commit**. The dedicated pipeline, compartment and private repository are unchanged. No additional infrastructure, Vault access or credential seeding is required. The statement permits repository creation, deletion and visibility changes within that compartment during the window, although the retry performs image publishing only.

Reviewable sequence: record approval and fixed expiry; prepare and inspect the Terraform policy-only plan; apply it; submit build attempt 2 for the same commit/tag; verify the registry digest; remove the temporary statement and verify repository privacy regardless of the build result; then scan, activate and verify the corrected hosted image. The addition and removal plans each changed only the build IAM policy. Build attempt 2 **SUCCEEDED**, publishing digest `sha256:5e77e2fd174519613bc7bfdc2d5b09aa14854785588adfa233069e26039d4101`. Removal apply also succeeded, and independent readback confirmed the temporary statement is absent and the repository remains private, before the fixed expiry. No renewal is active.

Expected incremental usage is one cloud build of approximately 13 minutes, based on this corrected build's observed duration, plus image storage under the existing OCI billing rates. No new continuously running resources are requested. Rollback removes the temporary statement immediately; the fixed expiry also ends the grant if cleanup is interrupted. Image rollback retains the previously registered artifact `59052`, whose UI routing limitation is already recorded.

## Completed initial publishing window

The user explicitly approved this proposal with “Yes Approved.” Approval was recorded at 2026-09-25 02:51:06 UTC, with fixed expiry **2026-09-25 03:21:06 UTC** (September 24, 11:21:06 PM EDT). The rollout journal records `user-2026-09-24-registry-30-min-approved`. The fifth build **published successfully**. Cleanup apply succeeded, independent IAM readback confirmed the temporary statement was removed before expiry, and the repository remains private. `registry_publish_until` is empty again.

The dedicated infrastructure, PostgreSQL and Oracle SSO registration exist. Four earlier cloud image builds passed runtime validation, but their delivery attempts failed at the initial OCIR layer upload with an authorization denial. The approved fifth run published source `8c19d34385bc6fcf836a20baff155964b5c0c667` as `source-8c19d34385bc6fcf`, verified digest `sha256:7e34939ef5426478a999d190add7ee5c720f4dfcc2d89ff3beb549fd615873a2`. Hosted deployment creation was then accepted; startup and SSO verification are tracked separately in [VERIFICATION.md](VERIFICATION.md).

Scoped attempts used the exact build pipeline and repository, ID and domain-qualified group references, flat READ/UPDATE permissions, and separate metadata inspection. These did not resolve the denial. This establishes the failure boundary, not which extra permission OCI actually requires.

## Proposed temporary permission

Apply Oracle's documented DevOps delivery grant to the **dedicated Portfolio Advisor compartment**, with a fixed expiry 30 minutes after approval. Only the dedicated build pipeline is a member of the group. The currently verified sole repository is private `portfolio-advisor`.

```text
Allow dynamic-group Default/portfolio-advisor-build to manage repos in compartment id ocid1.compartment.oc1..aaaaaaaakunp5q74aye3tlifbrffdguq5algwddayjmgzhu6ie5mxwf4qqhq where request.utc-timestamp before '<approved-fixed-UTC-expiry>'
```

This allows repository creation, deletion and metadata/visibility changes in that compartment during the window. It adds no Vault access and no permission in another compartment. The build performs image publishing only; it does not intentionally exercise repository deletion or visibility changes. A compromised build could exercise those additional permissions during the window, which is why explicit approval is required.

Terraform implements this as `registry_publish_until`, **empty/disabled by default**. The approved fixed expiry is now recorded in the protected journal. Automatic approval review rejected the earlier persistent compartment-wide grant; the narrower retries also failed. The subsequent explicit user approval authorizes only this temporary window. A review ID alone is not authorization to enable or renew it.

After explicit approval: retain a fixed expiry in the journal's non-secret foundation inputs, plan and inspect the single policy addition, apply, and retry the same source commit. Verify the published digest. Immediately remove the temporary input and apply the narrow policy again, whether the retry succeeds or fails. The fixed expiry independently stops authorization if cleanup is interrupted; never extend it without another approval. Repository privacy is checked before and after.

This is a proposed diagnostic/unblocking step, not a guarantee that publishing will succeed. One additional build is expected to take about 10–15 minutes based on the observed runs; normal OCI build charges apply. No additional infrastructure is proposed.

Sources: [Oracle DevOps delivery policy](https://docs.oracle.com/en-us/iaas/Content/devops/using/devops_iampolicies.htm), [registry permissions](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/registrypolicyreference.htm), and [fixed-time IAM conditions](https://docs.oracle.com/en-us/iaas/Content/Identity/Concepts/policyadvancedfeatures.htm).
