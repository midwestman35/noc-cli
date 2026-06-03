# Runbook — Dropped calls / dial failures

**Symptom tag:** `[dropped calls]`
**Maps to fork-rubric:** Symptom Class 4 (dial failures / outbound) and the BYE-direction analysis.

## What "dropped calls" means
Calls drop mid-call at ~N seconds, "Destination not reachable", or Nth-attempt-succeeds. The decisive question is **who tore the call down** (who sent BYE first) and **what SIP response** the far end returned.

## Decisive evidence to gather
- PCAPs of the **failed** attempt and (if available) a **successful** attempt to the same number.
- BYE-direction analysis: did our SBC or the carrier send BYE first, and at what offset from 200 OK?
- The SIP final response on failure (4xx vs. 5xx).
- Speed-dial / number config for the dialed number; confirm it is the *complained-about* number.

## Fork decision (from the rubric Class 4 table)
- **SBC sends unsolicited BYE N seconds post-200 OK, consistently** → **Fork A** — SBC instability; capture the node IP.
- **SBC returns SIP 5xx (500/503)** → **Fork A** — SBC error path.
- **Carrier returns SIP 4xx (404/408/487) cleanly** → **Fork B** — carrier rejected; provide PCAP.
- **Number not in NANP (e.g., area code 875), or never appears in logs** → **Fork C** — speed-dial misconfig / wrong target; customer-facing fix.
- **Bridge contention from concurrent long calls on one fsconf node** → **Fork A** — bridge sizing.

## Stop conditions
- Misconfigured speed dial proven → **Fork C**, customer note with the corrected number; close.
- Investigating the wrong call (PDFs show success; complaint is about other numbers) → reset; pull the complaint-target call data first.
