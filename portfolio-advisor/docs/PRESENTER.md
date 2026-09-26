# Presenter walkthrough

## Five-minute story

**Use case:** an internal portfolio reviewer needs to see whether a concentrated stock portfolio differs from an illustrative stock/bond/cash model, and inspect the arithmetic behind a possible rebalance. The assistant coordinates work and explains verified calculations; it does not place trades or assess personal suitability.

1. Open the app, identify whether the banner says **local rehearsal** or live. Say explicitly that rehearsal prices and assistant text are synthetic and no cloud/model call occurs. Live mode uses Oracle SSO.
2. Import `examples/holdings.csv` and preview six positions, or load the fictional example. Explain that the CSV uses `symbol,quantity,currency`; `CASH` is USD amount. A negative quantity or duplicate ticker is rejected before saving or starting cloud work.
3. Select **60% equity**, expand targets and inspect VTI 60/BND 35/cash 5. Original individual stocks are assigned 0% unless edited. Change a weight without balancing the total to demonstrate the disabled Analyze button, then restore 100%.
4. Acknowledge processing and start the review. Point to the durable progress stages. In live mode these represent an OpenAI session coordinating a verified Python helper in OCI Sandbox and reading Yahoo quotes there.
5. In the synthetic fixture, show **$86,000** total value, **27.9%** largest individual position, **5.8%** cash, and **61.3%** drift for 60/35/5. Explain drift as half the sum of absolute current-versus-target weight differences.
6. Open Rebalance proposal. VTI increases by $31,600; BND increases by approximately $21,100; residual fractional-share rounding remains in cash. These indicative changes exclude fees, taxes and execution slippage.
7. Open Run evidence. A live result must contain the completed root turn, OCI sandbox/executor, matching command, and helper/input/result hashes. Synthetic results explicitly say command verification is false. Quote timestamps distinguish source time from retrieval time.
8. Ask “Why is cash retained after rounding?” Follow-ups reuse the associated session and saved prices. An expired sandbox does not remove saved downloadable reports; a new analysis needs a new session.
9. Download the printable HTML, rebalance CSV and evidence JSON. Show a saved review after refreshing. Editing targets does not alter the prior report; it requires a new analysis.
10. Use a disposable test portfolio for deletion. Access disappears immediately while remote cleanup may remain pending/retrying. Do not imply that database backups vanish immediately.

## Custom target allocation

In **Explore a model → Edit target allocation**, enter a US-listed stock or ETF ticker and select **Add** (or press Enter). The ticker is normalized to uppercase and added at 0%; focus moves to its target percentage. Set its desired percentage and reduce other targets so the total remains 100%. New symbols are marked **Target only** and do not change the uploaded holdings.

For a synthetic demo, add **AMZN**, set it to 10%, and reduce VTI from 60% to 50%. Keep BND at 35% and cash at 5%, then acknowledge rehearsal processing and analyze. The proposal shows AMZN at 0% currently and 10% proposed; the holdings table still contains only the original six positions. In live mode, positive target symbols require verified quotes before a report can complete.

The remove control deletes an unowned target, or sets an existing holding's target to 0% while keeping the holding visible. Cash remains available. A 0% target represents an illustrative full exit from a held position. Selecting a preset replaces custom targets; switching portfolios starts from the default model for that portfolio. Edits are a draft until submitted for analysis; saved reports retain the submitted targets, and later edits require a new analysis. The UI marks customized weights and rejects duplicate/malformed symbols, invalid percentages, totals other than 100%, and more than 100 positive security targets.

## Questions to anticipate

- **Why Agents API?** Reusable agent definitions, durable isolated sessions, streamed progress and session follow-ups, backed by an actual remote execution environment.
- **Why OCI Sandbox?** Isolates file work, market-data retrieval and calculations from the app; expires after 30 minutes. Only the executor key enters that environment.
- **How are numbers trusted?** Fixed helper source, readback hashes, exact successful command, completed root turn, request-bound artifact, and independent arithmetic reconciliation.
- **Is this advice?** An illustrative comparison against explicitly edited targets. No trades, suitability assessment, tax advice or return forecast.
- **What if Yahoo or OpenAI fails?** The run retains evidence and shows failure/needs-review. It never replaces missing live quotes with fixture values.
- **What is demonstrated today?** Refer to VERIFICATION.md: local/simulated evidence and actual platform resource creation are separate from hosted execution.
