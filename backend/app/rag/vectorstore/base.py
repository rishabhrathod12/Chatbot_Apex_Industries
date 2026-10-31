from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.schemas.chunk import Chunk


class VectorStoreError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SearchFilter:
    """Restricts a search. `None` means "don't restrict on this". An empty list means "nothing
    is allowed", so it returns no results. That fails closed if a caller builds the list wrongly.

    `departments` with `include_company_wide` allows chunks from those departments OR any chunk
    marked company-wide. `narrow_department` then keeps only one department's chunks. It can
    shrink what is allowed, never widen it.
    """

    departments: Sequence[str] | None = None
    include_company_wide: bool = False
    narrow_department: str | None = None
    owner_id: str | None = None
    document_ids: Sequence[str] | None = None


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    text: str
    score: float  # cosine similarity, higher is closer
    metadata: dict[str, str | int]


@dataclass(frozen=True)
class StoredChunk:
    """A chunk as stored, without any similarity score (used by keyword search)."""

    chunk_id: str
    text: str
    metadata: dict[str, str | int]


class VectorStore(Protocol):
    """One collection of chunk vectors. Implementations must be thread-safe."""

    def upsert(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        embedding_model: str,
    ) -> None: ...

    def query(
        self,
        embedding: Sequence[float],
        *,
        top_k: int,
        search_filter: SearchFilter | None = None,
    ) -> list[SearchHit]: ...

    def scan(self, search_filter: SearchFilter | None, limit: int) -> list[StoredChunk]:
        """Every chunk the filter allows (up to `limit`). Keyword search runs over exactly this
        set, so it can never see or learn from chunks the user may not access."""
        ...

    def vectors_for(self, chunk_ids: Sequence[str]) -> dict[str, list[float]]: ...

    def delete_document(self, document_id: str) -> None: ...

    def delete_stale(self, document_id: str, keep_chunk_ids: Sequence[str]) -> None:
        """Remove this document's chunks whose ids are not in `keep_chunk_ids`."""
        ...

    def count(self, document_id: str | None = None) -> int: ...
