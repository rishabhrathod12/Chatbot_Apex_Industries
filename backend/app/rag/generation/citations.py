import re

_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by do does for from how i if in is it of on or that the this to "
    "was what when where which who will with you your".split()
)
MAX_QUOTE_CHARS = 300


def _claim_before_marker(answer: str, number: int) -> str:
    """The part of the answer that the marker [number] supports: text from the previous sentence
    or line break up to the marker."""
    claims: list[str] = []
    for match in _MARKER.finditer(answer):
        if number not in {int(n) for n in re.findall(r"\d+", match.group(1))}:
            continue
        before = answer[: match.start()]
        start = max(before.rfind("\n"), before.rfind(". "), before.rfind("? "), before.rfind("! "))
        claims.append(_MARKER.sub("", before[start + 1 :]))
    return " ".join(claims)


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def select_quote(answer: str, number: int, chunk_text: str) -> str:
    """Pick the line of the cited chunk that best matches what the answer says about it.

    The result is always copied from the chunk (never generated), so it can be checked against
    the document. Lines are scored by shared words; if nothing matches, the first line is used.
    """
    lines = [line.strip() for line in chunk_text.split("\n") if line.strip()]
    if not lines:
        return ""
    claim = _words(_claim_before_marker(answer, number) or answer)

    best, best_score = lines[0], -1
    for line in lines:
        text = line[2:].lstrip() if line.startswith("- ") else line
        score = len(claim & _words(text))
        if score > best_score:
            best, best_score = line, score

    quote = best[2:].lstrip() if best.startswith("- ") else best
    if len(quote) > MAX_QUOTE_CHARS:
        quote = quote[:MAX_QUOTE_CHARS].rsplit(" ", 1)[0]
    return quote
