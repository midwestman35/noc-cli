# Runbook — Event history / analytics gaps

**Symptom tag:** `[event history]`
**Maps to fork-rubric:** Symptom Class 6 (data / analytics / event-stream gaps), with Class 2 overlap for *missing inbound 911 events*.

## What "event history" means
Calls or events are missing from the event history / analytics dashboard, counts mismatch, or an inbound 911 event is absent from the record entirely. The split is **scope** (one customer vs. many) and **pipeline stage** (intake vs. enrichment vs. dashboard).

## Decisive evidence to gather
- Exact date/time range of the missing data and the affected customer(s).
- Cluster check: is the same gap visible for 2+ customers in the same window?
- Which pipeline component: did intake receive the event, did enrichment process it, is only the dashboard view filtering it?
- For a missing **inbound 911** event specifically: is there an outbound callback recorded with no matching inbound (orphan)?

## Fork decision
- **Same gap across 2+ customers in one window** → **Fork A** — pipeline-wide; open/append a cluster ticket. (Rubric Class 6.)
- **Gap isolated to one customer + correlates with their LAN/VPN issue** → **Fork B** — customer connectivity dropped events at source.
- **Customer's filters / dashboard config exclude records** → **Fork C** — config training; the data exists.
- **Inbound 911 event missing entirely** → **Fork A — patient-safety priority**; escalate same-day (Rubric Class 2).

## Stop conditions
- ≥2 customers affected in the same window → **Fork A as a cluster**; consolidate tickets onto one Jira.
- No data-pipeline logs cover the range → **Fork D**, request them, pause.
