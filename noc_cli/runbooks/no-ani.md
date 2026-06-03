# Runbook — No ANI (caller number not displaying)

**Symptom tag:** `[No ANI]`
**Maps to fork-rubric:** Symptom Class 2 (call routing / data) — ANI is the calling-party number that should populate on the station.

## What "No ANI" means
The station shows a 911 call but the caller's phone number (ANI / pANI / ESRD/ESRK) is blank, `Unknown`, or the trunk default. ANI arrives in the inbound SIP `From`/`P-Asserted-Identity` headers from the carrier and is surfaced by the translation pipeline to the station client.

## Decisive evidence to gather
- Inbound SIP `INVITE` for the call (carrier leg): inspect `From`, `P-Asserted-Identity`, `Remote-Party-ID`.
- Translation-pipeline log for that Call-ID: did ANI parse, or was it dropped/normalized to empty?
- The Carbyne Event PDF for the call (shows what the station actually rendered).
- One known-good call from the same trunk in the same window (control).

## Fork decision
- **Carrier INVITE already has no/empty ANI** → the number never arrived. **Fork B (Vendor)** — carrier/OSP issue; forward the PCAP. (Rubric Class 2: "Carrier INVITE has wrong destination data (bad ANI/ALI from carrier)".)
- **Carrier INVITE has ANI but the station renders blank** → ours. **Fork A (Engineering)** — translation pipeline / station client dropped a present value; capture Call-ID + pipeline log.
- **ANI present and correct on a control call, absent only on the complained call, no log coverage** → **Fork D** — request the SIP capture for the specific Call-ID; pause.

## Stop conditions
- Patient-safety modifier applies if 911 calls are routed/answered without ANI at scale — escalate Fork A same-day.
- If a master ticket already tracks ANI loss for this trunk/site, link to it; do not re-investigate.
