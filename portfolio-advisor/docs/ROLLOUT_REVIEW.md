# Rollout review — authorized, activation in progress

The user explicitly authorized the broader rollout on September 24: “Deploy the application as a hosted application with SSO and activate it, and create the required terraforms.” The journal records `user-2026-09-24-deploy-hosted-sso`. This supersedes the earlier pending rollout decision. Terraform now runs in OCI Resource Manager; the dedicated private DevOps repository and pipeline build the filtered source snapshot. Activation and hosted acceptance remain pending until verified.

The existing dedicated Vault and sandbox project are preserved. Terraform references the Vault without reading secret content; the journaled SDK manages SSO credentials and preview hosted-application lifecycle so client secrets do not enter Terraform state. A temporary Bastion connection restricted to the setup client's single IP creates a restricted PostgreSQL runtime role; it is removed after verification. The runtime never receives the administrator password.

The Chicago registry rejects its `isImmutable` setting. The rollout therefore uses unique commit-derived image tags, refuses an existing tag before build submission, verifies its digest before activation, and rechecks the active artifact against the registry. This changes the registry mechanism, not the image-identity acceptance requirement.

The separately approved dedicated-project ALLOW_ALL exception remains in effect. Earlier live evidence proved a connected Luna executor and Yahoo quotes, not a completed analysis. The previous setup sandbox expired September 24 at 8:56 PM EDT and must not be treated as a persistent runtime. See VERIFICATION.md and NETWORK_REPAIR_REVIEW.md for retained evidence and rollback.

## Resources and defaults

| Resource | Proposed quantity/configuration |
|---|---|
| Dedicated compartment | One approved compartment; do not apply broad runtime policies in the RFx compartment |
| VCN | One `10.94.0.0/16`, subject to actual CIDR inventory |
| Private subnets | Application `.1.0/24`, PostgreSQL `.2.0/24`, build `.3.0/24` |
| Networking | One NAT gateway, service gateway, route table; three NSGs; HTTPS egress and DNS; PostgreSQL ingress only from application/build NSGs |
| PostgreSQL 16 | One E5 Flex instance, 2 OCPU, 32 GB, durable managed storage; seven-day backups; protected from Terraform destroy |
| Object Storage | Private artifact bucket with versioning disabled; separate versioned build-input bucket; no public access |
| Vault | One DEFAULT Vault, one software AES-256 key; five dedicated app secrets plus a separate PostgreSQL administrator secret |
| Registry/DevOps | One private OCIR repository with unique commit-derived tags, source repository, build pipeline, delivery stage, notification topic; no automatic deployment trigger |
| Hosted application | One replica; API and two supervised workers in one non-root image; resource principals and private outbound subnet |
| Identity Domain app | One dedicated confidential OIDC client in the approved internal domain; exact hosted URL callback; created through SDK to keep its secret out of Terraform state |
| Sandbox project | Existing dedicated project moved into the new child compartment; no persistence/warm pool; separately authorized ALLOW_ALL demo exception, with original policy retained for rollback |
| Per analysis | One sandbox, 30-minute expiry, one isolated OpenAI session; one active analysis per user |

The application public URL exposes only sign-in/static resources and health/config metadata without SSO. Portfolio content, event streams, and downloads require the application’s Oracle OIDC session. Review internal-domain membership and application assignment policy before enabling the client.

## Permissions for review

- Human rollout identity: create the listed resources in the dedicated compartment; tenancy authority only for dynamic groups/IAM; Identity Domain app administration for the dedicated OIDC client.
- Hosted dynamic group: matches exact hosted application resource IDs. Reads only the exact five app secret OCIDs, manages objects only in the dedicated artifact bucket, and manages `generative-ai-family` only in the dedicated compartment. The preview family grant is broader than sandbox-only lifecycle; approve its scope or replace it with documented sandbox-specific permissions after checking the guide.
- Build group: matches the one dedicated pipeline. DevOps source access, build-input object reads, private network use, and READ/UPDATE permissions for the exact `target.repo.name` in the dedicated application compartment. A separate `inspect repos` grant permits collection metadata lookup in that compartment; it cannot read image contents or change any repository. Push permissions use separate flat conditions. A proposed compartment-wide repository grant was rejected by automatic approval review and was not applied. No repository creation/deletion/visibility-management grant, application secret-bundle access or hosted deployment permission. See [Oracle's registry policy reference](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/registrypolicyreference.htm).
- PostgreSQL administrator credential stays separate from the runtime DB account. Provision a `portfolio_app` database/role, grant ownership only over its schema, and store that connection URL directly in the dedicated Vault secret. App startup creates schema version 1; the runtime account does not need instance administration rights.
- Approve service policies, quotas, Oracle internal-domain assignment, CA trust and preview availability in the target region based on actual tenancy inventory. Terraform schema validation is not proof that policies or preview quotas will be accepted.

## Budget estimate — planning assumptions, not a vendor quote

For a seven-day demo, assume one always-on hosted replica, 168 database hours, 100 analyses averaging ten sandbox minutes, 5 GB of artifacts/build inputs, and ten build hours. Reserve **$100–$250 for infrastructure**, plus the **actual OpenAI Agents/model usage and preview hosted/sandbox charges**. For a 730-hour month, reserve **$400–$900 for infrastructure**, plus those metered charges. These are provisional budget allowances, not measured service rates or a spending authorization.

The review must replace these allowances with the tenancy’s actual rate card and preview pricing: `database_hours × (2 × OCPU_rate + 32 × memory_rate) + database_storage + backups + replica_hours × hosted_rate + sandbox_minutes × sandbox_rate + object/registry storage + build_minutes + OpenAI input/output usage`. Some services bundle memory/storage; do not double count. The preview hosting and sandbox rates could not be verified from a public rate card, so **there is no verified all-in estimate yet**. Set a project spending cap, an analysis limit for the demo, and budget alarms before approval. User-level concurrency and sandbox expiry constrain activity, not total monthly spend.

Sources checked on 2026-09-24: [Oracle cloud price list](https://www.oracle.com/cloud/price-list/), [PostgreSQL billing](https://docs.oracle.com/en-us/iaas/Content/postgresql/billing.htm), [OCI AI pricing](https://www.oracle.com/artificial-intelligence/generative-ai/generative-ai-service/pricing/). The OpenAI project’s actual Agents entitlement/model rate must be checked in its account.

## Credential seeding

1. Create the foundation without PostgreSQL/runtime IAM first. Seed `POSTGRES_ADMIN_PASSWORD` directly into Vault with the rollout CLI; supply only that secret OCID to Terraform and apply the reviewed PostgreSQL plan.
2. Create the empty hosted app and independent sandbox project using the journaled CLI. Their actual IDs determine the runtime dynamic group and callback URL.
3. Create the dedicated Identity Domain app through `rollout.py sso`; the returned client secret goes directly to Vault. If the provider omits it, use its approved credential-creation process and `rollout.py seed --variable SSO_CLIENT_SECRET`. Do not put it in Terraform.
4. Use OpenAI’s appropriate project/application and executor-key creation processes. They are separate credentials. Store each with `rollout.py seed`; interactive secret input is hidden. No API key, DB password, or SSO secret may be passed in command arguments.
5. Seed `AUTH_SESSION_SECRET` (generated in memory) and the dedicated verified-CA database URL. No RFx DB/SSO values are reused.
6. `rollout.py environment` generates reference-only hosted configuration and updates root `.env` with actual secret OCIDs. Apply exact secret IDs and app ID to runtime IAM. Test `CURRENT` resolution using the intended runtime principal before any model request.

## Reviewed execution sequence

The [Oracle GenAI Sandboxes guide](https://confluence.oraclecorp.com/confluence/display/OCAS/GenAI+Sandboxes+User+Guide), its Agents API example and managed-network design were reviewed in authenticated Chrome on 2026-09-24; command-line Confluence DNS still failed. The guide confirms `216.146.35.35` for MANAGED sandbox DNS. No documented additional allowlist requirement resolved the observed restricted-policy failure. The separately approved ALLOW_ALL demo exception has now restored connectivity, but an OCI managed-network diagnosis is still needed before claiming restricted hosted networking works. Recheck preview changes before the broader rollout. The supplied wheel supports the required SDK methods; project updates require `opc_request_id`, not `opc_retry_token`, and the deployment helper now verifies that SDK contract in a test. Oracle’s separate preview CLI is not bundled.

Use the journaled `terraform_rollout.py` workflow in [HOSTED_DEPLOYMENT.md](HOSTED_DEPLOYMENT.md). The existing stack and state live in OCI Resource Manager. Do not apply this existing rollout from a new empty local state. Never use `oci_vault_secret` content or Identity Domain client-secret attributes in Terraform.

The existing private `vault-bootstrap.json` journal owns this rollout. Continue it through `adopt-foundation`, `application`, `sso`, database setup, `environment`, `configure`, build, `deploy`, and `status`. Do not initialize another journal or recreate the retained agent/project.

The CLI persists request intent and retry tokens before each SDK write. An unconfirmed write stops instead of recreating resources. Resolve that operation against OCI work requests/tags before editing its journal. CLI mutation flags record approval references; they are not an approval authority themselves.

Build with the **actual** APP_BASE_PATH from the application URL. Supply reviewed pipeline parameters APP_BASE_PATH, OCI_NAMESPACE, BUILD_INPUT_BUCKET, PREVIEW_WHEEL_OBJECT, PREVIEW_WHEEL_SHA256, and unique IMAGE_TAG. The build derives its exported tag from the checked-out commit because OCI clears exported variables initially. Deliver through DevOps into the private OCIR repository. Retrieve its digest and pass it to `rollout.py deploy`; verify the ACTIVE artifact and tag-to-digest identity using `status`. Do not claim deployment from an accepted work request or image push.

## Rollback and stop criteria

- Before activation: stop the pipeline, retain the last verified image and private journals, and leave the existing app unchanged. Do not destroy the database or Vault as routine rollback.
- After activation: restore the previous recorded artifact ID using the hosted deployment update API with an ETag, then verify ACTIVE status and immutable registry digest. Keep schema changes backward-compatible; schema version 1 has no destructive migration.
- Revoke a faulty SSO client and rotate only dedicated secrets through Vault CURRENT versions, then restart the application to reload them. Credentials are never recovered through logs.
- Stop on unknown work-request state, failed integrity checks, unresolved secrets, wrong image identity, unavailable quotas, or inability to enforce user isolation.
- Tear down only resources tagged with the dedicated deployment ID after explicit destructive approval. Confirm sessions/sandboxes and object cleanup first. Database backups have seven-day retention; scheduled Vault deletion has OCI-enforced recovery windows. Terraform `prevent_destroy` deliberately blocks casual teardown.

## Recorded rollout choices

The authorized rollout uses Chicago, a dedicated child compartment and `10.94.0.0/16` VCN, the existing dedicated Vault/project, a new confidential client in the tenancy's Default Identity Domain, and `gpt-5.6-luna`. Credential contents stay in Vault; exact references and resource/work-request IDs are recorded privately. The public pricing limitations above remain explicit. Completion requires live activation, image identity and application/SSO verification, not another general deployment approval.
