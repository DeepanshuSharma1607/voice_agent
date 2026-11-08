"""Detect an explicit caller request to end the call (pure functions, easy to test)."""

from __future__ import annotations

import re

# Only explicit phrases should end the call. Broad phrases like "I'm done" are
# avoided because a caller may just mean they finished giving details.
HANGUP_PATTERNS = [
    re.compile(r"\bhang\s*up\b", re.IGNORECASE),
    re.compile(r"\bend\s+(?:the\s+|this\s+|our\s+)?call\b", re.IGNORECASE),
    re.compile(r"\bdisconnect\s+(?:the\s+|this\s+)?call\b", re.IGNORECASE),
    re.compile(r"\bdisconnect\s+me\b", re.IGNORECASE),
    re.compile(r"\bcut\s+(?:the\s+|this\s+)?call\b", re.IGNORECASE),
    re.compile(r"\bstop\s+(?:the\s+|this\s+)?call\b", re.IGNORECASE),
    re.compile(r"\b(?:can|could|will)\s+you\s+cut\s+it\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+(?:the\s+|this\s+)?call\b", re.IGNORECASE),
    re.compile(r"\bterminate\s+(?:the\s+|this\s+)?call\b", re.IGNORECASE),
    re.compile(r"\b(?:goodbye|good\s+bye|bye\s+bye)\b", re.IGNORECASE),
    re.compile(r"\bbye\b\s*[.!?]*$", re.IGNORECASE),
]

NEGATED_HANGUP_PATTERNS = [
    re.compile(r"\b(?:don't|do not|never|not)\s+(?:hang\s*up|end|disconnect|cut|drop)\b", re.IGNORECASE),
    re.compile(r"\bwait\b", re.IGNORECASE),
    re.compile(r"\bhold on\b", re.IGNORECASE),
    re.compile(r"\bnot yet\b", re.IGNORECASE),
]


def is_explicit_hangup_request(text: str) -> bool:
    """Return True only for a clear request to end the phone call."""
    if not text:
        return False

    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return False

    if any(pattern.search(cleaned) for pattern in NEGATED_HANGUP_PATTERNS):
        return False

    return any(pattern.search(cleaned) for pattern in HANGUP_PATTERNS)
