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
        if re.search(r"https?://|www\.|[/\\]|`|\d\.\d", last, re.IGNORECASE):
            return text
        return body[:-1] + text[len(body):]
    return text


_LITERAL = re.compile(r'```[\s\S]*?```|`[^`\n]*`|“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"|https?://[^\s<>]+')
_FORMAT_LITERAL = re.compile(_LITERAL.pattern + r"|(?<!\w)(?:[A-Za-z0-9_.:/\\-]+[\\/])?\*{1,2}(?:[\\/][A-Za-z0-9_.*?\\/:-]+|\.[A-Za-z0-9_?*-]+)")


def without_formatting_stars(text: str) -> str:
    """Render star emphasis/list markers as plain QQ text, preserving literal stars."""
    literals, pieces = {}, []
    start = 0
    for match in _FORMAT_LITERAL.finditer(text):
        pieces.append(text[start:match.start()])
        value, tail = match[0], ""
        if value.startswith(("https://", "http://")):
            # URL scanning may consume the closing emphasis marker, e.g. **label URL**.
            opening = re.search(r"(?<![A-Za-z0-9*\\])(\*{1,3})[^*\n]*$", "".join(pieces))
            ending = re.search(re.escape(opening[1]) + r"([。，！？,.!?]*)$", value) if opening else None
            if ending:
                value, tail = value[:ending.start()], value[ending.start():]
        token = f"\ue000{len(literals)}\ue001"
        literals[token] = value
        pieces.append(token + tail)
        start = match.end()
    pieces.append(text[start:])
    result = "".join(pieces)
    result = re.sub(r"(?m)^[ \t]{0,3}(?:\*[ \t]*){3,}$", "", result)
    result = re.sub(r"(?m)^([ \t]{0,3})\*(?=[ \t]+\S)", r"\1•", result)
    for _ in range(3):
        before = result
        for width in (3, 2, 1):
            # ASCII arithmetic, masks and file globs are not Markdown decorations.
            marker = r"\*" * width
            pattern = rf"(?<![A-Za-z0-9*\\]){marker}(?=\S)([^*]+?\S|[^*\s]){marker}(?![A-Za-z0-9*])"
            result = re.sub(pattern, r"\1", result)
        if result == before:
            break
    for token, value in literals.items():
        result = result.replace(token, value)
    return result


def natural_chat_text(text: str) -> str:
    """Apply the QQ owner's punctuation preference outside literal content."""
    text = without_formatting_stars(text)
    parts=[]
    start=0
    for match in _LITERAL.finditer(text):
        parts.append(text[start:match.start()].replace("；","，").replace(";","，"))
        parts.append(match[0])
        start=match.end()
    parts.append(text[start:].replace("；","，").replace(";","，"))
    result="".join(parts)
    if not any(m.end()==len(result.rstrip()) for m in _LITERAL.finditer(result)):
        result=re.sub(r"(?:】[ \t]*【)+[。.]*\s*$","",result)
    return without_final_period(result)
