# Dedicated credentials and OCI connection

The dedicated Vault, software AES key, session secret, OpenAI application secret and OpenAI executor secret are recorded in `deploy/private/vault-bootstrap.json`. Canonical credential variables in root `.env` contain their actual references; temporary plaintext aliases are removed only after CURRENT readback verification. Both OpenAI credentials resolve and differ. OpenAI accepted the key supplied as `OCI_APPLICATION_KEY` for the configured Luna agent; it was stored as the application secret and its plaintext alias removed after matching CURRENT readback. The environment key is stored in the executor secret. **Connection status:** OpenAI confirms the self-hosted Luna environment connected to OCI after the separately approved ALLOW_ALL exception. Setup exited 0 and the executor is running. No further key replacement was needed. Database and SSO configuration await the broader rollout.

## Which service issues each credential

OCI hosts the sandbox; the Agents control plane remains at `api.openai.com`. Storage in OCI Vault does not change a credential's issuer or permissions. The current [OpenAI self-hosted authentication guide](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted#authentication) and both local executor implementations agree on this mapping:

| Supplied local name | Issuer and role | Application configuration | Passed into OCI sandbox |
|---|---|---|---|
| `OPENAI_APPLICATION_KEY` | OpenAI project key with List models Read (`api.model.read`), Agents Write (including read), and Responses Write | `OPENAI_API_KEY` → dedicated application Vault secret | No |
| `OPENAI_ENVIRONMENT_KEY` | OpenAI restricted environment key, owned by the session's organization/project/user or service account | `OPENAI_EXECUTOR_API_KEY` → dedicated executor Vault secret | Its value becomes `CODEX_API_KEY` for `codex exec-server` |
| OCI profile / resource principal | OCI authentication for creating and accessing the sandbox and Vault | Local `Sandbox` profile; hosted resource principal | Not a substitute for either OpenAI key |

An OCI-issued Generative AI inference key authenticates the OCI inference endpoint; it does not authenticate an executor connection to OpenAI. The [Oracle Agents example](https://confluence.oraclecorp.com/confluence/display/OCAS/Sandbox%3A+With+OpenAI+Agent+API) reuses one OpenAI key for both roles; the current OpenAI self-hosted guide separates them. The Oracle guides were successfully reviewed through authenticated Chrome on 2026-09-24, although command-line DNS lookup of Confluence failed.

The user confirmed that the initially supplied `OPENAI_AGENT_APIKEY` was an **environment key**. It is assigned to `OPENAI_EXECUTOR_API_KEY`. A distinct project API key was subsequently supplied for `OPENAI_API_KEY`; its CURRENT version replaces the earlier misclassified value in the dedicated application secret. That older version is historical Vault content and is never selected by startup, which reads CURRENT.

For future credential imports, use hidden input with `rollout.py seed-openai` on a fresh journal. To import explicitly supplied temporary local entries, `import-app-key` reads `OPENAI_APPLICATION_KEY`, and `import-executor-key` reads `OPENAI_ENVIRONMENT_KEY`. These commands remove the alias only after matching CURRENT readback. An accepted asynchronous Vault update may need a second read; repeating the same journaled command does not resubmit an already accepted update.

An OpenAI environment key comes from the session's **Connect environment → Need an environment key?** form. It is not an OCI inference key. A separate project API key comes from **OpenAI Platform → API Keys**. Complete credential creation yourself; never paste values into chat or command arguments.

The connection command resolves only the two OpenAI Vault references, validates the actual saved agent/session snapshot, refuses nonempty session history, and uses the existing OCI SDK/curl transport. It records resource identifiers before writes, reuses retained IDs, subscribes before starting the executor, and requires both OCI runtime health and OpenAI's connected state. It submits no model input. Its default restricted network plan includes `api.openai.com` for registration and `codex-cloud-environments.chatgpt.com` for the documented outbound WebSocket, plus npm and Yahoo; all use port 443. The resolver-only addition `216.146.35.35:53` did not repair DNS. The user then separately approved ALLOW_ALL for this dedicated project. That policy is now ACTIVE; all four hostnames and Yahoo quote retrieval passed inside the connected sandbox. The original restricted policy is retained for rollback; see [verification](VERIFICATION.md#approved-allow_all-connection--successful-live-verification).

```bash
portfolio-advisor/.venv/bin/python portfolio-advisor/scripts/connect_environment.py \
  --create-project --review-id user-2026-09-24-connect-agents-session-to-oci
```

The `--create-project` option creates the independent restricted-egress project only if its actual ID is missing; journal reconciliation prevents duplicates. Only a confirmed connection saves the identifier-only bootstrap mapping into root `.env`; the first live application analysis then adopts it into PostgreSQL. That mapping is now saved for `sbx-yfpfqwfhks7nyyau`, expiring at 2026-09-25 00:56:03 UTC (September 24, 8:56 PM EDT). The ALLOW_ALL project policy persists after sandbox expiration. Failed mappings and command IDs remain in `deploy/private/connection`; all three earlier setup/diagnostic sandboxes are confirmed deleted. The successful replacement used the runbook's guarded `--replace-deleted` workflow. Live app startup additionally requires the dedicated database and SSO configuration.

The full hosted infrastructure rollout remains subject to its separate review. Keep private journals in durable protected operator storage and reconcile the already-created resources into Terraform before applying its broader foundation.
