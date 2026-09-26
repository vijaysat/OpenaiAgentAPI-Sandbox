# Activated image repackaging — September 25

The corrected application is ACTIVE with tag `source-94a0ba95e06fca31-flat1` and digest `sha256:d5d59c226fae1ef26e21bccb38b57532a1397581c2aebb0fc49723d80daba435`. The hosted UI and real Oracle sign-in work. See [verification](VERIFICATION.md) for the live-analysis boundary.

## Source and transformation

The source is the successfully built patched image from commit `94a0ba95e06fca31328961ccc5d4f1d440554c04`, registry digest `sha256:e9749e05872cc4d7612e4ff8c0c3a6ddf5f08062e44affeffec231a8b0b1618d`. Its build verified non-root execution, application/database imports, curl 8.22.0, OpenSSL 3.0.22 and certificate-verified HTTPS. Its original multi-layer OCI scan still named superseded OpenSSL packages.

The official Google `crane` 0.22.1 utility flattened that image's merged filesystem into one layer. The existing operating-system registry credential helper supplied authorized access to the dedicated private repository. No credential was printed, no new credential was created, no cloud build was run, and no IAM or repository-visibility change was needed. The separate request for another temporary cloud-build publishing window was superseded.

The Darwin arm64 release archive was verified against the vendor checksum: `2231fc8df8806d20d680ff1225db44e095a55dd6ac1ae8eced4faf4b278b78fb`. Tool provenance is retained privately. The [official release](https://github.com/google/go-containerregistry/releases/tag/v0.22.1) and [flatten implementation](https://github.com/google/go-containerregistry/blob/v0.22.1/cmd/crane/cmd/flatten.go) document the tool and transformation.

## Independent validation

`scripts/verify_image_repackage.py` streams both merged filesystem archives without extracting or executing them. All **58,184 entries** matched: file-content SHA-256, type, mode, UID/GID, size, link target, modification time and extended metadata. The complete runtime configuration and platform matched. The target has one filesystem layer and retains user `10001:10001`. The installed package database remains present and records both `openssl` and `libssl3` as `3.0.22-1~deb12u1`.

OCI scan `ocid1.vsscontainerscanresult.oc1..aaaaaaaa22rwr6xpyhcrvm3yyduyejhcb3lsmbchlcpuopdi45ebsopjkgxq` reports **0 critical, 3 high, 9 medium and 2 low** findings. OCI accepted artifact registration with `is_vulnerability_scan_required=True`, and the deployment reached ACTIVE with the expected artifact/tag/digest. No finding was suppressed and no package inventory was removed. The remaining noncritical findings are retained; this is not a zero-vulnerability claim. The changed scan supports the superseded-layer hypothesis but does not establish OCI's internal scanning behavior.

The seven hosted HTTP checks passed. Real Oracle sign-in returned to the authenticated dashboard in the browser. A first live analysis subsequently stopped during OCI sandbox startup; activation success is not complete model-analysis acceptance.

## Reproduction and rollback

Keep the source digest immutable and choose an unused destination tag. Journal intent before publication. With the already authorized registry helper configured, the transformation used:

```bash
crane flatten ord.ocir.io/ax6ymbvwiimc/portfolio-advisor@sha256:e9749e05872cc4d7612e4ff8c0c3a6ddf5f08062e44affeffec231a8b0b1618d -t ord.ocir.io/ax6ymbvwiimc/portfolio-advisor:source-94a0ba95e06fca31-flat1
```

For this pinned tool, a digest-addressed source was published by digest, so a separate tag operation associated the verified output digest with the unused target tag. Do not blindly rerun these commands or overwrite the existing tag. Verify source/target files and configuration with the comparison script, wait for the exact-tag OCI scan, then use the journaled deployment workflow. The scan gate remains mandatory.

The corrected build-time TLS checker in prepared source `b94767f06643a27712abb707bf38e2daa74dfc7f` was not cloud-built or deployed. It remains available for subsequent builds; it is not the source identity of this activated image.

Private evidence: `deploy/private/repackage-verification.json`, source/target file inventories, `repackaged-image-scan.json`, `activation-status.json`, `hosted-smoke.json`, `browser-hosted-verification.json`, and the rollout journal's `image_repackage` record. Keep these files protected and out of source publication.

All temporary publishing IAM statements are removed, and the repository remains private. The original artifact `59052` is retained as a rollback target, with its known UI-routing defect documented. An image rollback must preserve PostgreSQL, Vault and stored reports.
