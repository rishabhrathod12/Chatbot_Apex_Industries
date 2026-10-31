"""Keyword search (BM25) over a set of chunks.

Meaning-based search is good at paraphrases and weak at exact terms (document ids, acronyms, part
numbers). Keyword search is the opposite. Hybrid search uses both.

Security note: the statistics BM25 uses (how rare a word is) are computed only from the chunks it
is given, which are the chunks the user is allowed to see. Statistics over the whole library
could reveal that a word occurs in a document the user cannot access.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.rag.vectorstore import StoredChunk

_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_STOP = frozenset(
    "a an and are as at be been being by can could do does for from had has have how i if in into "
    "is it its me my of on or our should so than that the their them then there these they this "
    "to up us was we were what when where which who whom why will with would you your".split()
)
_K1 = 1.5
_B = 0.75


def _stem(word: str) -> str:
    """A very light stemmer so that 'reporting', 'reported' and 'reports' meet 'report'."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    """Lower-case words, light stemming, no filler words. A hyphenated id such as
    'APM-QLT-SOP-006' is kept whole AND split into its parts, so it matches either way."""
    tokens: list[str] = []
    for match in _TOKEN.findall(text.lower()):
        parts = match.split("-")
        pieces = [match, *parts] if len(parts) > 1 else parts
        for piece in pieces:
            if piece not in _STOP:
                tokens.append(_stem(piece))
    return tokens


def searchable_text(chunk: StoredChunk) -> str:
    """The text keyword search looks at: the passage plus its document and section titles."""
    meta = chunk.metadata
    return " ".join(
        str(part)
        for part in (
            meta.get("doc_code", ""),
            meta.get("doc_title", ""),
            meta.get("section_title", ""),
            chunk.text,
        )
        if part
    )


@dataclass(frozen=True)
class LexicalHit:
    chunk: StoredChunk
    score: float


def bm25_search(query: str, chunks: Sequence[StoredChunk], top_k: int) -> list[LexicalHit]:
    """Chunks that share words with the query, best first. Chunks sharing none are left out."""
    query_terms = set(tokenize(query))
    if not query_terms or not chunks or top_k <= 0:
        return []

    documents = [Counter(tokenize(searchable_text(c))) for c in chunks]
    lengths = [sum(d.values()) for d in documents]
    average = (sum(lengths) / len(lengths)) or 1.0
    count = len(chunks)
    frequency = {t: sum(1 for d in documents if t in d) for t in query_terms}

    scored: list[LexicalHit] = []
    for chunk, terms, length in zip(chunks, documents, lengths, strict=True):
        score = 0.0
        for term in query_terms:
            tf = terms.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (count - frequency[term] + 0.5) / (frequency[term] + 0.5))
            score += idf * tf * (_K1 + 1) / (tf + _K1 * (1 - _B + _B * length / average))
        if score > 0:
            scored.append(LexicalHit(chunk, score))
    scored.sort(key=lambda hit: (-hit.score, hit.chunk.chunk_id))
    return scored[:top_k]
