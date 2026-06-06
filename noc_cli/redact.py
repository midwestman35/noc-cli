from __future__ import annotations

import re
from dataclasses import dataclass

# Strict 10/11-digit phone, bounded by zero-width assertions so it won't match
# inside an opaque token AND so two space-separated phones each match
# independently (a captured-and-reinserted boundary would consume the shared
# separator and miss the second number).
_PHONE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
    r"(?![A-Za-z0-9])"
)

_STREET_SUFFIXES = (
    r"Ave(?:nue)?|Blvd|Boulevard|Cir(?:cle)?|Ct|Court|Dr(?:ive)?|"
    r"Expy|Expressway|Fwy|Freeway|Hwy|Highway|Ln|Lane|Loop|Pkwy|Parkway|"
    r"Pl(?:ace)?|Rd|Road|Route|Rte|Sq|Square|St(?:reet)?|Ter(?:race)?|"
    r"Trl|Trail|Way"
)
_ADDRESS = re.compile(
    r"\b\d+\s+(?:[A-Z][A-Za-z'\-]*\s+)+(?:" + _STREET_SUFFIXES + r")\b"
)

# Strict GPS pair: 4+ fractional digits each.
_COORD = re.compile(r"-?\d{1,2}\.\d{4,}\s*[,;\s]\s*-?\d{1,3}\.\d{4,}")

# Loose residual shapes (run on already-redacted text for a soft-warn only).
_RESIDUAL_COORD = re.compile(
    r"(?:^|[^\d.])(-?\d{1,3}\.\d{2,3}\s*[,;\s]\s*-?\d{1,3}\.\d{2,3})(?:$|[^\d.])"
)
_RESIDUAL_LOCAL_PHONE = re.compile(
    r"(?:^|[^A-Za-z0-9_\-])(\d{3}[-.\s]\d{4})(?:$|[^A-Za-z0-9_\-])"
)

# A single loose match is more likely an operational ID/version than PII;
# require this density before a (non-blocking) soft-warn fires.
RESIDUAL_PII_WARN_THRESHOLD = 3


@dataclass
class RedactionCounts:
    phones: int = 0
    addresses: int = 0
    coords: int = 0
    enabled: bool = True


def _is_pre_redacted(s: str) -> bool:
    lower = s.lower()
    return "***" in lower or "xxx" in lower or "[redacted]" in lower


def redact(text: str) -> tuple[str, RedactionCounts]:
    """Redact caller PII (phones, addresses, coords) from `text`.

    Returns (redacted_text, counts). Operational identifiers are preserved by
    the bounded patterns. Pre-redacted spans (***, xxx, [redacted]) are left
    untouched.
    """
    counts = RedactionCounts(enabled=True)

    def phone_sub(m: re.Match[str]) -> str:
        if _is_pre_redacted(m.group(0)):
            return m.group(0)
        counts.phones += 1
        return "<PHONE>"

    out = _PHONE.sub(phone_sub, text)

    def addr_sub(m: re.Match[str]) -> str:
        if _is_pre_redacted(m.group(0)):
            return m.group(0)
        counts.addresses += 1
        return "<ADDR>"

    out = _ADDRESS.sub(addr_sub, out)

    def coord_sub(m: re.Match[str]) -> str:
        if _is_pre_redacted(m.group(0)):
            return m.group(0)
        counts.coords += 1
        return "<COORDS>"

    out = _COORD.sub(coord_sub, out)
    return out, counts


def _count_non_overlapping(pattern: re.Pattern[str], text: str, group: int) -> int:
    """Count matches, advancing past the captured token so a shared delimiter
    can bound the next token (mirrors the Rust captures_at loop)."""
    count = 0
    pos = 0
    while pos <= len(text):
        m = pattern.search(text, pos)
        if not m:
            break
        count += 1
        end = m.end(group)
        pos = end if end > pos else pos + 1
    return count


def residual_pii_warning(redacted: str, counts: RedactionCounts) -> str | None:
    """Best-effort density check on already-redacted text. Returns a soft-warn
    string at/above the threshold, else None. Never mutates; never blocks.
    The <PHONE>/<ADDR>/<COORDS> sentinels carry no digits, so they can't
    self-trigger this scan."""
    if not counts.enabled:
        return None
    residual = _count_non_overlapping(
        _RESIDUAL_COORD, redacted, 1
    ) + _count_non_overlapping(_RESIDUAL_LOCAL_PHONE, redacted, 1)
    if residual >= RESIDUAL_PII_WARN_THRESHOLD:
        return (
            f"redaction: {residual} residual caller-PII-shaped token(s) survived "
            f"scrub (soft-warn; payload not blocked)"
        )
    return None


def redact_value(value):
    """Recursively redact string leaves in a JSON-like structure.

    Returns ``(redacted_value, total_redactions)``. Dicts and lists are walked;
    non-string leaves pass through untouched. ``total_redactions`` is the sum of
    phone/address/coordinate substitutions across all string leaves.
    """
    if isinstance(value, str):
        red, counts = redact(value)
        return red, counts.phones + counts.addresses + counts.coords
    if isinstance(value, dict):
        out: dict = {}
        total = 0
        for key, val in value.items():
            out[key], n = redact_value(val)
            total += n
        return out, total
    if isinstance(value, list):
        out_list = []
        total = 0
        for item in value:
            red_item, n = redact_value(item)
            out_list.append(red_item)
            total += n
        return out_list, total
    return value, 0
