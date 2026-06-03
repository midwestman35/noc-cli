# Runbook — No ALI (caller location not displaying)

**Symptom tag:** `[No ALI]`
**Maps to fork-rubric:** Symptom Class 2 (call routing / data) — ALI is the Automatic Location Information looked up from the ANI/pANI.

## What "No ALI" means
The station shows the call (and often ANI) but the **location** is blank, "ALI timeout", or stale. ALI is fetched from an external ALI/LIS database keyed on the (p)ANI; Carbyne issues the query and renders the response. A failure can be upstream (ALI provider/DBMS), in the bid (wrong key), or in rendering.

## Decisive evidence to gather
- ALI query/response log for the Call-ID: did Carbyne send a bid? Did the provider answer? Timeout vs. NAK vs. empty?
- The ANI used as the ALI key — was it the correct (p)ANI (cross-check the No-ANI runbook)?
- Provider/region: which ALI DB serves this PSAP (steering vs. provider outage).
- A control call to the same ALI DB in the window.

## Fork decision
- **Bid sent, provider timed out / NAK'd / returned empty** → **Fork B (Vendor)** — ALI provider/DBMS degradation; hand off with the query log. (Rubric Class 2 vendor row.)
- **No bid sent, or bid used a wrong/empty key** → **Fork A (Engineering)** — our bid logic or ANI→key mapping; capture the request log.
- **Provider answered correctly but station shows blank/stale** → **Fork A (Engineering)** — rendering/caching defect.
- **No ALI logs cover the incident** → **Fork D** — request the ALI transaction log for the Call-ID; pause.

## Stop conditions
- Multiple PSAPs on the same ALI provider failing in one window → likely **Fork B cluster**; consolidate.
- Recurring for one site only → check for a site master ticket and link.
