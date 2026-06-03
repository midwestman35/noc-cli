# Runbook — Low / poor audio (media quality)

**Symptom tag:** `[low audio]`
**Maps to fork-rubric:** Symptom Class 1 (media loss / audio quality).

## What "low audio" means
One-way audio, choppy/garbled audio, low volume, missing greeting, or audio that degrades through the call. Audio rides RTP; signaling is SIP. The split is whether RTP is present and healthy at our SBC vs. degraded before it reaches us.

## Decisive evidence to gather
- PCAP covering the **full call lifecycle** (SIP + RTP) for the Call-ID.
- Station logs around the incident timestamp (renderer hang, heap spike).
- Jitter/loss profile per RTP stream; which leg (caller vs. station) shows the gaps.
- Recent translation-pipeline Jiras (REP-class) for known regressions.

## Fork decision (from the rubric Class 1 table)
- **RTP present, timestamps healthy, progressive latency increase** → **Fork A** — translation pipeline / buffering upstream of SBC.
- **RTP absent though 200 OK + ACK succeeded** → **Fork A** — media not reaching SBC; SDP/relay issue.
- **RTP present + clean but station-side renderer hang/heap spike** → **Fork A** — station client team.
- **RTP gaps correlate to caller-side jitter (caller IP only)** → **Fork B** — carrier / caller signal.
- **Audio capture stops but call continues** → **Fork A** — recording channel / call-leg attribution bug.

## Exclusions & stop conditions
- `STUN keepalive` warnings on **every** call are a chronic baseline — exclude as a signal; keep digging.
- Log/PCAP window does not cover the incident → request server-side FreeSWITCH/Kamailio logs; **Fork D**, pause.
- Pattern matches an open REP-class Jira → add evidence, stop.
