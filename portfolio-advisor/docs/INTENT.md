# Accepted implementation intent

**User intent:** demonstrate the OpenAI Agents API through an internal Portfolio Health & Rebalancing Advisor using deterministic calculations inside OCI Sandbox. An analyst must be able to inspect the data and execution evidence behind each proposal.

**Authority:** user-approved revised implementation plan (Revision 2), followed by authorization to create the dedicated agent, sessions, Vault credentials and sandbox connection. The September 24 instruction to deploy with SSO and activate the hosted application authorizes the dedicated rollout (`user-2026-09-24-deploy-hosted-sso`). Existing unrelated resources remain outside scope.

**Behavioral contract:** strict validated holdings; owner-private persistence; illustrative editable VTI/BND/CASH models; timestamped Yahoo data fetched inside OCI; durable jobs and streamed progress; exact command/root-turn/hash/artifact checks; no blind uncertain retries or fabricated live data; same-session follow-ups; immediate deletion revocation with honest deferred cleanup; Vault-only live credential references.

**Out of scope:** trade execution, tax calculations, personal suitability, automatic credit purchases, RFx resource/credential reuse, and claiming synthetic/local checks as cloud verification.

**Acceptance mapping:** AC-001 parser/API; AC-002 deterministic fixtures and conservation; AC-003 simulated and later live evidence; AC-004 durable failure/recovery; AC-005 authentication and ownership; AC-006 PostgreSQL/container durability and deletion; AC-007 keyboard/responsive/browser workflow; AC-008 deployable non-root package and separately verified hosted image; AC-009 CURRENT Vault resolution, key separation and redacted failure. Actual evidence and remaining gates are recorded in VERIFICATION.md.

**Key decisions:** PostgreSQL owns execution state and session assignment; object storage owns report durability; one prepared platform session is claimed by only one analysis; each later analysis creates its own self-hosted session; the application reuses the verified saved agent. Secret creation is SDK-managed so secret values never enter Terraform state. The recovered Oracle SDK wheel is checksum locked; required lifecycle CLI operations use this SDK directly.
