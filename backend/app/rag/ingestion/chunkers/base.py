import math
from typing import Protocol

from app.schemas.chunk import Chunk, DocumentContext
from app.schemas.extraction import ExtractedDocument

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Rough token count (about 4 characters per token for English). Good enough for sizing."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


class Chunker(Protocol):
    """A chunking strategy. Implementations must be deterministic."""

    def chunk(self, document: ExtractedDocument, context: DocumentContext) -> list[Chunk]: ...
