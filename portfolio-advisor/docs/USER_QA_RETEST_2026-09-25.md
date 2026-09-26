# OCI hosted user retest — September 25, 2026

**Verdict: DO NOT SHIP as a complete demo.** Retested the actual hosted application at approximately **13:17–13:22 UTC**. A newly saved synthetic portfolio again failed live analysis; reconciliation reports **“Session creation is unconfirmed; reconcile the saved request before retrying.”** No successful analysis, follow-up or report download was verified.

[Hosted application](https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/20251112/hostedApplications/ocid1.generativeaihostedapplication.oc1.us-chicago-1.amaaaaaafigrwqyacofmt5zjaripoblrh47ydu7shpjoljjlgkmb5enhybta/actions/invoke/) · [Previous test and findings](USER_QA_2026-09-25.md)

## Fresh observations

| Scenario | Result | Evidence |
|---|---|---|
| Open deployed workspace | PASS | Dashboard and original example loaded after reload. |
| Resume original saved review | FAIL | One reconciliation attempt returned the service-could-not-finish error; no report appeared. |
| Reject malformed upload | PASS | Negative quantity rejected with the row-specific quantity error; Add portfolio stayed disabled. |
| Preview and save valid upload | PASS | Actual file chooser uploaded VTI 10, BND 20 and CASH 500; three positions validated and saved using the keyboard. |
| New live analysis | FAIL | Exactly one fresh valid analysis, saved at **13:20:07 UTC**, entered the worker flow and changed to Needs Review. |
| Reconcile new run | FAIL | One reconciliation attempt returned the unconfirmed-session message. No further analysis or recovery attempt was made afterward. |
| Follow-up and report downloads | BLOCKED | No question input exists for this incomplete run. Printable report, Rebalance CSV and Evidence JSON each have `aria-disabled=true` and no download URL. |
| Invalid individual target bounds | FAIL, UI | VTI 101 / BND -6 / CASH 5 enables Analyze because the sum is 100. These invalid targets were not submitted this time; restored 60/35/5 before the live test. |
| Failed-run evidence | FAIL, UX | Run evidence still says session, command and quote evidence appears only after a completed run. |
| Upload dialog dismissal | FAIL, keyboard | Enter opened the dialog; Escape closed it but focus went to BODY instead of the opener. |
| Responsive widths | PASS, limited | Document and body widths equaled the viewport at 375, 768 and 1440 pixels. Temporary viewport overrides were reset. |
| Sign out / Oracle sign-in | PASS | Sign out returned to the public landing page; Oracle sign-in returned to the authenticated workspace. |
| Saved data after sign-in | PASS, limited | Original example and new synthetic portfolio remained; new failed review retained its timestamp and reconciliation error. This does not prove container-replacement durability. |
| Earlier deletion cleanup | STILL PENDING | Pending-cleanup banner remains. No deletion or native deletion-confirmation test was attempted during this retest. |

The new portfolio **QA retest synthetic 2026-09-25 13:19 UTC** and its failed review were retained for diagnosis. The original example remains intact. One new analysis plus one recovery attempt on that new run, and one recovery attempt on the original saved run, were initiated through the UI. These observations do not independently prove the provider-side submission count.

## Independent live HTTP checks

At **13:19:22 UTC**, public UI, health and configuration returned 200. Health reported API `ok`, worker `ok`, and `cloudVerified=false`; configuration reported `live`. Anonymous identity, portfolios, templates and deletions requests all returned 401. Browser warning/error checks returned no entries; this does not certify all network requests succeeded.

The separate OCI control-plane image-identity check returned **401 NotAuthenticated** under the local Sandbox operator profile. Consequently the active image digest and provider-side cause were **not reverified** in this retest. This operator authentication failure is separate from the user-facing analysis failure and is not asserted as its cause. No fresh operator authentication or infrastructure change was attempted.

Credential-free HTTP evidence is saved privately at `deploy/private/qa-retest-20260925/http-boundary.json` with mode 0600. No credentials, cookies or identity tokens were exported.

## Acceptance limits

No application code or infrastructure was changed. Local regression tests were not rerun for this hosted-only retest; the prior 158-passing result is historical. No visual baseline comparison, complete accessibility audit, two-user isolation test, container replacement, successful calculation, follow-up, report download or confirmed physical deletion is claimed.

Next required work is to inspect and reconcile the exact saved OpenAI session-creation request and provider response, repair the demonstrated failure, and then complete the same user journey with verified OCI execution and artifacts. Preserve the saved uncertainty markers until the provider outcome is known.
