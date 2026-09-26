# Cool Slate deployment — September 26, 2026

The user requested OCI deployment of the approved Cool Slate UI. Release `cool-slate-172945a665792d25` is ACTIVE with artifact `59309` and verified registry digest `sha256:3477bb65d6d7a1394bd906cffe6ee50111d2f207278e21e5507accdf01a64066`. The activation work request completed successfully.

## Release scope

Only the compiled frontend HTML, CSS, and JavaScript changed. The build uses the existing hosted application URL prefix. Full merged-filesystem comparison confirmed unchanged backend files, installed packages, runtime configuration, platform, and non-root user `10001:10001`. Publication used the existing private registry credentials; no IAM, runtime environment, database, or infrastructure change was required.

The UI applies Cool Slate throughout the dashboard, sign-in, dialogs, allocation editor, advisor content, and administration. Chart colours match the selected preview. Semantic success, warning, and error colours remain distinct. An existing mobile dialog overflow was corrected while preserving the desktop dialog size and the expanded allocation-editor default.

## Verification

- Local: all 9 frontend tests and the production build passed. Eighteen browser views using fictional fixtures covered desktop, tablet, mobile, dialogs, administration, sign-in, validation, and keyboard focus. The checked text had no contrast failures, and the final dialog bounds fitted the viewport.
- Image: the three frontend files matched the reviewed release; runtime and base-package verification passed.
- OCI scanning: 0 critical, 3 high, 9 medium, and 2 low findings. OCI accepted artifact registration with mandatory vulnerability scanning enabled. No findings were suppressed.
- Hosted checks at **01:55:53 UTC**: all 10 passed, including UI/assets, API/database/worker health, live configuration, anonymous-access denial, and Oracle sign-in redirect/profile scopes.
- The live HTML, CSS, and JavaScript each returned HTTP 200 and matched the deployed file SHA-256 exactly. The CSS contains the approved Cool Slate palette.

These checks did not submit a new portfolio analysis or certify a fresh authenticated SSO callback. Previous analysis evidence remains recorded separately.

A fresh live browser screenshot was not captured: the interactive browser reached Oracle sign-in, and clean headless navigation timed out. Live deployment verification therefore rests on OCI image identity, service checks, and exact served-asset hashes; visual checks used the local production build with fictional data.

## Rollback and evidence

Previous release: `fix-eeeea57ff9221417`, artifact `59306`, digest `sha256:61f7ed2c0cee426ecdb2a8322086ce2fd92dece06ed5b9c8b5a023dfa95fca94`. Use the existing journaled deployment workflow and verify the restored image identity if rollback is required; retain PostgreSQL, Vault, portfolios, and reports.

Protected evidence is under `deploy/private/patch-172945a665792d25/`, including the release journal, scan, activation status, and live asset verification. The shared `deploy/private/hosted-smoke.json` retains the hosted service checks. These records remain outside source publication.
