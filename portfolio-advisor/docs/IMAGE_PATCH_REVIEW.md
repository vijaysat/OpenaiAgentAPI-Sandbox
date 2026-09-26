# Patched image publishing review — September 25

The history below is retained. Its patched filesystem was subsequently [repackaged and activated](IMAGE_REPACKAGE.md); the current hosted UI and Oracle sign-in are verified.

## Historical patch review

The approved routing-fix build published successfully as `source-56e30de295f0d0da`, digest `sha256:5e77e2fd174519613bc7bfdc2d5b09aa14854785588adfa233069e26039d4101`. Its temporary publishing grant was removed at **10:44:10 UTC**, before the 10:58:14 expiry; independent policy and repository checks passed.

The dedicated OCI scanner is ACTIVE. Its report for that exact tag contains 75 findings: 8 critical, 26 high, 33 medium and 8 low. Registration with `is_vulnerability_scan_required=True` was explicitly rejected because the report is critical. The scan requirement remains enabled. The original artifact `59052` is still ACTIVE and its normal UI URL still has the routing defect. No usable hosted UI or completed SSO callback is claimed.

## Prepared patch

- Build curl **8.22.0** from the official source archive in a separate container build stage. Its SHA-256 is pinned to `f7ef3ae8a22e521f289803fe93543eb64c329b58aa73a9e224dfd915a2a5f4f7`. The archive was independently verified using the maintainer signature and fingerprint `27EDEAF22F3ABCEB50DB9A125CC908FDB71E12C2`.
- Stop installing Bookworm's older curl package. Copy the new executable and license into the non-root runtime image, preserving the existing Bash/curl Agents transport.
- Apply Debian package upgrades and fail the image build unless both `openssl` and `libssl3` are at least **3.0.22-1~deb12u1**.
- Add a cloud runtime check for curl's version and an actual HTTPS request with certificate verification. Retain the existing API, database-driver and non-root checks.
- Keep the tested hosted path/Host correction and all existing SSO/ownership controls.

The source signature, archive contents, build YAML and shell syntax were checked locally. The updated container is **not built or scanned yet**. The optional local Docker check could not run because the Docker daemon is stopped; no full local image build was attempted. The cloud build and exact-image OCI report remain required before activation. Remaining noncritical findings are not silently dismissed, and no zero-vulnerability claim is made.

Oracle's generic scan lists upstream fix versions. Debian confirms that CVE-2026-34182 is already fixed in the previous `3.0.20-1~deb12u2` package, but CVE-2026-75803 requires the newer security package. The new curl release addresses the reported curl critical fix versions; its actual OCI scan must confirm the outcome.

Sources: [curl 8.22.0 release](https://curl.se/ch/8.22.0.html), [curl signature verification](https://curl.se/docs/verify.html), [Debian CVE-2026-34182](https://security-tracker.debian.org/tracker/CVE-2026-34182), [Debian CVE-2026-75803](https://security-tracker.debian.org/tracker/CVE-2026-75803).

Prepared private source commit: **`94a0ba95e06fca31328961ccc5d4f1d440554c04`**. Snapshot SHA-256: `5c45893e139a15795ddb7ea8b04bb136ec6595927cac835c435713772a102489`. No build has been submitted for this source.

## Requested publishing window

Enable the same reviewed registry policy for **one new patched-image build**, with a fixed UTC expiry 30 minutes after explicit approval. Only the dedicated Portfolio Advisor build pipeline receives repository-management permission in the dedicated Portfolio Advisor compartment. During that window this includes repository creation, deletion and visibility changes; the build performs publishing only. No Vault permissions, credential changes, additional compute resources or broader compartment access are proposed.

Expected incremental usage: one cloud build, approximately 15–20 minutes including curl compilation, plus image storage at the existing OCI rates. Success is not guaranteed until the cloud build and scan finish. Immediately remove the temporary grant after completion or failure, verify removal and repository privacy, then wait for the new image's scan report and register/activate only if OCI accepts its scan checks. Never extend the expiry or start another publishing attempt without further authorization.

Rollback: remove the temporary grant immediately; its fixed expiry also terminates access if cleanup is interrupted. Retain PostgreSQL, Vault, reports and the prior registered image. A failed new image does not replace the active artifact. Preserve the rejected image and scan evidence for diagnosis.

**Completed:** the user approved one build on September 25 at 11:18:46 UTC, with fixed expiry **11:48:46 UTC**. The image built and published successfully as `source-94a0ba95e06fca31`, digest `sha256:e9749e05872cc4d7612e4ff8c0c3a6ddf5f08062e44affeffec231a8b0b1618d`. The runtime reported curl 8.22.0 and OpenSSL 3.0.22; the minimum package versions, non-root imports and real HTTPS check passed. Upload completed at 11:47:20 UTC.

The removal apply began after the build reached SUCCEEDED. The fixed condition expired at 11:48:46 UTC; the statement was independently confirmed removed at **11:49:15 UTC**, and the repository remained private. The grant was not extended.

The exact-image scan reported **29 findings: 2 critical, 11 high, 13 medium and 3 low**. Both critical entries still identify OpenSSL `3.0.20-1~deb12u2`, despite the validated final runtime reporting 3.0.22. No registration or activation of this image was attempted. The mismatch needs further diagnosis; it is not a passed scan. A separate [image-layer remediation review](IMAGE_LAYER_REVIEW.md) describes the next prepared change. This completed approval does not authorize another build or publishing window.
