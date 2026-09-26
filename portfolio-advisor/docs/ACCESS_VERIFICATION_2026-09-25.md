# Access administration verification — September 25, 2026

## Scope

Adapted the reference RFx Intelligence named-user authorization pattern to Portfolio Advisor's existing FastAPI/PostgreSQL ownership model. Added administrator/member roles, grants by Oracle sign-in email, immediate next-request revocation, live role checks, self-lockout prevention, optimistic edits, directory search/pagination, and an access audit trail. Existing owner IDs and private portfolios are unchanged.

The user selected their existing signed-in Oracle account as initial administrator. Deployment binds the owner of the run observed in that account's private UI; no first-login bootstrap or broad Oracle-company allowlist is used. Root `.env` remains mode 0600 and retains Vault references. The only new hosted setting is `AUTH_BOOTSTRAP_ADMIN_RUN_ID`. No OCI infrastructure resource, IAM grant, or credential was added for this feature.

## Local and simulated evidence

- Full Portfolio Advisor and existing core regression suites: **197 passed, 4 skipped, 4 subtests passed**. Four tests require a separate PostgreSQL test database; this run does not prove concurrent PostgreSQL behavior.
- Focused authorization suite after the final redacted diagnostic addition: **19 passed**.
- TypeScript and hosted-prefix production build: passed.
- Tests cover unknown/disabled/unverified identities, mismatched userinfo subjects, opaque-username email fallback, approved identity binding, duplicate grants, invalid fields without partial records, stale edits, CSRF/origin checks, anonymous/member denial, live role changes, invalidated sessions after disable/re-enable, consumed bootstrap protection, additive migration, and private portfolio/stream/download isolation from administrators.
- Local browser: synthetic member grant, disable, audit history, keyboard submission, cancellation with restored focus, and 390-pixel layout without horizontal overflow passed. No real user was granted access through the local test.

## Hosted evidence

The initial authorization image `fix-8161d73bf4f31fbc` activated with digest `sha256:e15be3d49db928a7c79effdc3d2634adb2c231a53be5e636cc980dbfa0888ab4`. Your existing session acquired administrator access; two portfolios and saved reviews remained visible. The directory contained only the approved initial administrator. Edit controls prevented self-disable/demotion, grant form defaulted to Member, cancellation added no user, and bootstrap appeared in access history. Fresh Oracle sign-out/sign-in returned successfully with administrator access.

The fresh sign-in exposed an email fallback edge case: an opaque username could suppress a userinfo lookup. The final correction checks for a usable email rather than any nonempty username, and still requires userinfo's subject to match the validated ID token. No email claim values or tokens are logged.

Final image: `fix-dbd69945c8ffeb79`, digest `sha256:987247828f4ea6e772ad2b684a5196a6bc99302e8cc8514e0afed408703b8ebc`. Merged-file comparison passed with unchanged non-root runtime configuration, architecture and package inventory. Mandatory OCI scanning remained enabled; 14 noncritical findings, highest HIGH, remain from the base image.

**Final result at 14:47 UTC:** the corrected image is ACTIVE with matching artifact and registry digest. All ten hosted HTTP checks passed at 14:46:22 UTC. Fresh Oracle logout/login returned to the same private portfolios and **Manage access**; the directory now displays the approved account's actual Oracle sign-in email and a current last-login time. The same single administrator entry survived the second container replacement. No additional hosted user was created. The admin workspace was left open for the user.

## Operational findings and boundaries

An activation attempted while the application configuration was UPDATING received an explicit HTTP 409 rejection. The saved attempt and provider request ID were preserved in reconciliation history. After confirming configuration SUCCEEDED, application/deployment ACTIVE, and the new artifact INACTIVE, the same artifact was activated. The rollout helper now checks the application state before recording an activation attempt; its regression test passed.

One pre-migration health request timed out and the browser initially loaded slowly. The later ten-check hosted smoke test passed UI/assets, database/worker health, live configuration, anonymous portfolio/identity/admin-directory/audit denial, PKCE SSO redirect, and email/profile scopes. The earlier timeout's root cause is not established.

New real employee onboarding and a second independent Oracle account were not exercised. Those boundaries are covered with synthetic API identities, not claimed as hosted two-user proof. No screen-reader audit was performed.

The existing OCI sandbox startup failure remains visible on the saved analysis. This feature does not establish completed model analysis, follow-up or report-download evidence, nor resolve earlier pending remote deletion cleanup. No fresh paid analysis was submitted during access testing.

See [administrator instructions and recovery](ACCESS_ADMIN.md). Schema version 2 requires an authorization-aware rollback image; do not restore the old unrestricted-SSO image or downgrade its version marker.
