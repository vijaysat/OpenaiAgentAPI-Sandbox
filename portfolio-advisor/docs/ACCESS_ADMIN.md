# People and application access

Oracle SSO verifies identity. Portfolio Advisor separately requires an enabled, named entry in its own user directory. This follows RFx Intelligence's SSO allowlist pattern; no RFx accounts, sessions, passwords, or database records are reused.

## Grant a person access

1. Sign in and choose **Manage access** at the top of the workspace.
2. Choose **Grant access**. Enter a display name and the email returned by their Oracle sign-in. Keep **Member** selected for ordinary users; **Administrator** also permits managing application access.
3. Leave **Access enabled** selected and save. Share the application link through your usual process. The app does not send invitations or create Oracle Identity Domain accounts.
4. The person signs in through the configured Oracle domain. Their approved email is bound to their validated immutable Oracle identity. Unknown or disabled accounts are denied. Existing Oracle domain access and any SSO-client assignments are still prerequisites.

Use **Edit access** to change a display name, role, or enabled status. Email is immutable; disable an old entry before granting a different email. Disabling access invalidates existing sessions on the next request, including progress streams and downloads. Re-enabling requires a fresh sign-in. Saved portfolios and reports are retained. A request already in progress may finish; background cleanup remains operational.

Admins cannot disable or demote themselves. At least one active administrator must remain. Concurrent edits use a version check and PostgreSQL serializes authorization changes. Every grant or change records its actor, time, and before/after settings under **Access history**.

Administrator status never grants visibility into another person's holdings, analyses, or reports. Those APIs retain their existing ownership checks.

## Initial administrator and upgrades

The approved rollout uses the existing signed-in account selected by the user. `AUTH_BOOTSTRAP_ADMIN_RUN_ID` identifies a run observed in that account's authenticated private workspace. On an empty directory, startup requires its nondeleted portfolio and a current login for its owner, then binds exactly that owner as administrator. The setting is server-only and is not accepted from a browser. It is not a first-sign-in grant.

For a fresh installation, use `AUTH_BOOTSTRAP_ADMIN_EMAIL` with an explicitly approved Oracle sign-in email instead. Set exactly one bootstrap identity. A durable consumed marker prevents a restart from regranting a disabled administrator. Never erase this marker or the directory to recover an account. A preexisting session with no enabled directory entry is denied.

The selected existing account can initially appear as **Initial administrator / Linked Oracle account** if the former sign-in did not retain email. A later sign-in requests `openid email profile`; userinfo is accepted only when its subject matches the validated ID token. An available nonconflicting email attaches to that already-bound identity. Opaque username claims trigger a userinfo lookup instead of hiding the missing email. The account does not depend on an email being returned to keep its approved binding.

Schema version 2 adds `app_users`, `access_audit`, and an access-policy record. Existing portfolios, ownership, jobs, sessions and reports remain in place. API/workers serialize startup migrations with a PostgreSQL advisory lock. The directory and audit trail persist in the existing managed database, so no additional OCI resource or new credential is needed.

## Recovery and rollback

Deploy an authorization-aware image before verifying the directory and existing owner UI. Remove a consumed bootstrap setting during a later configuration cleanup if desired; the database marker remains authoritative. Keep protected copies of the deployment journal and normal database backups.

**Do not activate the older unrestricted-SSO image after this migration.** It only understands schema version 1 and will refuse startup; downgrading the marker would remove the new access boundary. Use a corrected schema-2, authorization-aware image. If that is unavailable, keep access unavailable while restoring a reviewed authorization-capable version. Never expose unrestricted SSO as a rollback shortcut.

If every administrator is unavailable, an authorized operator must verify the intended Oracle identity and repair the directory in a controlled database transaction with an audit record. There is no public recovery endpoint or automatic first-arrival recovery.
