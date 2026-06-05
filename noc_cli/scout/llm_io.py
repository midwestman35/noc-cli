from __future__ import annotations

import re

_JSON_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(raw: str) -> str:
    """Best-effort pull of a JSON object from a chatty agent reply.

    In order: the first fenced ```json block, else the first '{' … last '}'
    span, else the stripped text as-is.
    """
    raw = raw.strip()
    fenced = _JSON_FENCE_RE.search(raw)
    if fenced:
        return fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        return raw[start : end + 1].strip()
    return raw


async def final_result(query_gen) -> str:
    """Drain a query stream, keeping only the terminal ResultMessage text.

    Scout does not need the rich transcript stash that ``agent/runner`` keeps —
    only the final JSON — so this is intentionally lighter than ``_drain`` there.
    """
    raw = ""
    async for message in query_gen:
        text = getattr(message, "result", None)
        if text is not None:
            raw = text
    return raw
