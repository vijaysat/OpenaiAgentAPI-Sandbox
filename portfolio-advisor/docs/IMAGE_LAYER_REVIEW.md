# Image-layer remediation review — September 25

## Historical blocker and prepared change

The patched image `source-94a0ba95e06fca31` built, passed runtime checks and published successfully. Its digest is `sha256:e9749e05872cc4d7612e4ff8c0c3a6ddf5f08062e44affeffec231a8b0b1618d`. The image reports curl 8.22.0 with OpenSSL 3.0.22, and both OpenSSL package minimum-version assertions passed during assembly. Certificate-verified HTTPS also passed.

OCI's exact-tag report nevertheless contains two critical findings, CVE-2026-34182 and CVE-2026-75803, both naming OpenSSL `3.0.20-1~deb12u2`. It reports 29 findings overall, down from 75 in the preceding image. The critical result still blocks activation. The previous original artifact remains ACTIVE, with the unresolved public UI routing defect.

The temporary publishing rule has expired and was independently confirmed removed at 11:49:15 UTC. The repository remains private. No new publishing grant is active.

## Prepared change

Add a final `FROM scratch` stage that copies the complete, merged filesystem from the upgraded runtime stage. This preserves Python, Bash, curl, CA certificates, libraries, the application, non-root user and the installed-package database, while excluding superseded lower image layers. Restore the runtime environment, working directory, user, port, health check and startup command explicitly.

The cloud build must verify that the resulting image contains exactly one filesystem layer; it must also print and validate the installed `openssl` and `libssl3` versions, validate Python's linked OpenSSL version, and rerun the API/import, non-root and HTTPS checks. OCI's scan requirement stays enabled. No findings are suppressed and no package inventory is removed.

Superseded layer content is a **diagnostic hypothesis**, inferred from the discrepancy between runtime and scan versions. A successful build or a changed digest is not sufficient evidence that it resolves OCI's findings. Only the new exact-tag scan and accepted artifact registration can establish that. Docker documents the [multi-stage filesystem-copy mechanism](https://docs.docker.com/build/building/multi-stage/); Podman documents the [image RootFS inspection interface](https://docs.podman.io/en/latest/markdown/podman-image-inspect.1.html).

Local build-YAML parsing, shell syntax, Terraform formatting and whitespace checks passed. A local image build is not claimed because Docker is stopped and local disk capacity is constrained. The next cloud build must validate the final filesystem and runtime.

## Requested bounded rollout

Authorize **one build** of the prepared immutable source, with the same dedicated-pipeline repository-management permission in the dedicated Portfolio Advisor compartment. Record a new fixed UTC expiry 30 minutes after recording this approval, before the permission plan is submitted. The temporary permission includes repository creation, deletion and visibility changes; the build will only publish the prepared application image. It grants no Vault access and makes no changes to SSO, database, sandbox networking or unrelated resources.

Expected incremental usage is one cloud build, approximately 15–20 minutes, plus image storage at existing OCI rates. Remove the temporary statement immediately after success or failure, or at its fixed expiry, and independently verify removal and private repository visibility. Do not extend it or start another build under this approval.

After publishing, retain and inspect the new exact-image scan. Register and activate only if OCI accepts the required scan checks. Then verify the active digest, public UI/assets, API/database/worker health, anonymous denial and SSO redirect. A completed browser login and real model analysis remain separate acceptance checks.

Rollback: remove the temporary grant; its fixed expiry also ends access if cleanup is interrupted. Retain the prior image, database, Vault secrets, saved reports and scan evidence. A scan-rejected image does not replace the active artifact.

Prepared immutable source: **`75ab98d560f09abe8d450de6b090d73019ad0f26`** (80 filtered files), snapshot SHA-256 `f3ab1ddd0613b73da9eaead9df937903718811a1be95f334172969c2f887c7c9`. The source is in the dedicated private OCI repository. The focused deployment/rollout suite passed **26 tests**; the existing Python regressions passed **50 tests and 4 subtests** after granting loopback access to their mock server. This source has not been built or scanned.

## Completed attempt and corrected check

The user approved one build on September 25 at 12:00:49 UTC. OCI authentication was renewed before the fixed 12:04:30–12:34:30 UTC window. Build `ocid1.devopsbuildrun.oc1.us-chicago-1.amaaaaaafigrwqyanp7e3vpnmzeesm2rlulzd5sjsksz7f2mxzw4v76grxaa` built the merged-filesystem image, passed the single-layer assertion, and passed non-root application/database-driver/transport imports. It then failed the newly added Python TLS assertion before publishing. No new registry image or scan was produced.

The assertion was incorrect: Python exposes OpenSSL 3.0.22 as a compatibility tuple `(3, 0, 0, 22, 0)`, so comparing that tuple to `(3, 0, 22)` rejects the patched release. The check now uses the library's encoded version number, requires at least `0x30000160` (3.0.22), rejects another TLS implementation, and prints the detected library before checking. Seven regression cases cover the threshold, newer releases, older releases and a different TLS library. All **33 focused runtime/deployment/rollout tests passed**, along with build YAML and shell syntax checks. See [Python's documented version fields](https://docs.python.org/3.13/library/ssl.html#ssl.OPENSSL_VERSION_NUMBER).

The temporary publishing statement was independently confirmed removed at **12:19:52 UTC**, before its expiry. The repository remains private. Failure messages that omitted the run-ID field were recovered using only the dedicated service log and this build's exact terminal-time interval; no audit records were queried.

Corrected immutable source: **`b94767f06643a27712abb707bf38e2daa74dfc7f`** (82 filtered files), snapshot SHA-256 `0aca2df587cd44a663136066c47fdf34416e2175273f3b4307b5c65884e521b5`, published to the dedicated private OCI repository. The existing regressions also passed: **50 tests and 4 subtests**. This corrected source has not been built or scanned.

**Pending request superseded:** no further cloud build or permission window was used. Existing authorized registry access repackaged the already-built patched image with identical merged filesystem and runtime configuration. The new exact-image scan had no critical findings, and OCI accepted activation with scanning required. The hosted UI and actual Oracle sign-in now work. See [the complete repackaging evidence](IMAGE_REPACKAGE.md) and [current verification](VERIFICATION.md).
