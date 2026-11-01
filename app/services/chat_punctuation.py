"""Keep a conversational ending without damaging punctuation inside an answer."""

from __future__ import annotations

import re


def without_final_period(text: str) -> str:
    """Remove a sentence's final full stop, keeping ellipses and literal code/URLs."""
    body = text.rstrip()
    if not body or body.endswith(("...", "。。", "```")):
        return text
    if body.endswith("。"):
        return body[:-1] + text[len(body):]
    if body.endswith("."):
        # Avoid changing literal URLs, paths, dotted versions and decimal examples.
        last = body.rsplit(None, 1)[-1]
        if re.search(r"https?://|www\.|[/\\]|`|\d\.\d", last, re.I):
            return text
        return body[:-1] + text[len(body):]
    return text
