# Runbook — APEX platform / station client (general)

**Symptom tag:** `[apex]`
**Maps to fork-rubric:** Symptom Class 3 (network error banner / WebSocket disconnect / station drops) and general APEX station-client issues that do not fit a more specific symptom.

## What "[apex]" means
A general APEX station / control-center symptom: "Network Error" banner, station status flips to ERROR, brief unavailability, login/registration issues, or UI faults — when a more specific tag ([No ANI], [No ALI], [low audio], [dropped calls], [event history]) does not apply. Use this as the catch-all for platform-level APEX behavior.

## Decisive evidence to gather
- Station logs for `RECONNECT_ON_DRAINING`, WebSocket close code `1006`, and status transitions around the timestamp.
- Kamailio drain logs around the incident (`X-Web-Socket-Draining: true`).
- Whether **one** station or **multiple stations at the same site** flipped within seconds.
- Customer network state (switch/SDWAN) and any open site master ticket.

## Fork decision (from the rubric Class 3 table)
- **Isolated to a single station, no customer-network correlation, Kamailio drain present** → **Fork A** — egress-node drain anomaly.
- **Multiple stations at one site flip ERROR within seconds** → **Fork B** — customer LAN, switch, or SDWAN; link to the site master ticket.
- **Recurring at the same site with an open master ticket** → **Fork B** — link to master; do not re-investigate.
- **Drain coincides with a planned rolling restart** → **Fork C** — customer note: expected maintenance.

## Stop conditions
- Open master ticket for the same site/window → **Fork B, link only.**
- Drain from a known-flapping egress node already under engineering investigation → **Fork A, add evidence.**
- No station/drain logs cover the incident → **Fork D**, request them, pause.
