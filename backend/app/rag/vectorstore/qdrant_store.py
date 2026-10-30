import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from qdrant_client import QdrantClient, models

from app.rag.vectorstore.base import SearchFilter, SearchHit, VectorStoreError
from app.schemas.chunk import Chunk

_NAMESPACE = uuid.UUID("5f0c1c0e-6b0e-4d6e-9a53-2a7d1d6a0a11")
COLLECTIONS = {"enterprise": "enterprise_chunks", "personal": "personal_chunks"}
_TEXT_KEY = "text"


def collection_name(scope: Literal["personal", "enterprise"]) -> str:
    return COLLECTIONS[scope]


def point_id(chunk_id: str) -> str:
    """Qdrant ids must be UUIDs or integers, so derive a stable UUID from the chunk id."""
    return str(uuid.uuid5(_NAMESPACE, chunk_id))


@dataclass
class _Handle:
    client: QdrantClient
    lock: threading.RLock


_handles: dict[str, _Handle] = {}
_handles_lock = threading.Lock()


def open_client(path: str) -> _Handle:
    """Embedded Qdrant allows one client per folder per process, so everyone shares this one."""
    with _handles_lock:
        handle = _handles.get(path)
        if handle is None:
            Path(path).mkdir(parents=True, exist_ok=True)
            handle = _Handle(QdrantClient(path=path), threading.RLock())
            _handles[path] = handle
        return handle


def close_all() -> None:
    """Close every open embedded client (used by tests and on shutdown)."""
    with _handles_lock:
        for handle in _handles.values():
            handle.client.close()
        _handles.clear()


def _to_qdrant_filter(search_filter: SearchFilter | None) -> models.Filter | None:
    if search_filter is None:
        return None
    must: list[models.Condition] = []
    if search_filter.departments is not None:
        must.append(
            models.FieldCondition(
                key="department", match=models.MatchAny(any=list(search_filter.departments))
            )
        )
    if search_filter.owner_id is not None:
        must.append(
            models.FieldCondition(
                key="owner_id", match=models.MatchValue(value=search_filter.owner_id)
            )
        )
    if search_filter.document_ids is not None:
        must.append(
            models.FieldCondition(
                key="document_id", match=models.MatchAny(any=list(search_filter.document_ids))
            )
        )
    return models.Filter(must=must) if must else None


def _blocks_everything(search_filter: SearchFilter | None) -> bool:
    return search_filter is not None and any(
        value is not None and len(value) == 0
        for value in (search_filter.departments, search_filter.document_ids)
    )


class QdrantVectorStore:
    def __init__(self, path: str, name: str, dimensions: int) -> None:
        self._handle = open_client(path)
        self._name = name
        self._dimensions = dimensions
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        client = self._handle.client
        with self._handle.lock:
            if client.collection_exists(self._name):
                params = client.get_collection(self._name).config.params.vectors
                size = params.size if isinstance(params, models.VectorParams) else None
                if size != self._dimensions:
                    raise VectorStoreError("embedding_dimension_mismatch")
                return
            client.create_collection(
                self._name,
                vectors_config=models.VectorParams(
                    size=self._dimensions, distance=models.Distance.COSINE
                ),
            )

    def upsert(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        embedding_model: str,
    ) -> None:
        if len(chunks) != len(embeddings):
            raise VectorStoreError("chunk_embedding_count_mismatch")
        points = []
        for chunk, vector in zip(chunks, embeddings, strict=True):
            if len(vector) != self._dimensions:
                raise VectorStoreError("embedding_dimension_mismatch")
            payload: dict[str, str | int] = {
                **chunk.to_metadata(),
                _TEXT_KEY: chunk.text,
                "embedding_model": embedding_model,
            }
            points.append(
                models.PointStruct(
                    id=point_id(chunk.chunk_id), vector=list(vector), payload=payload
                )
            )
        if not points:
            return
        with self._handle.lock:
            self._handle.client.upsert(self._name, points=points)

    def query(
        self,
        embedding: Sequence[float],
        *,
        top_k: int,
        search_filter: SearchFilter | None = None,
    ) -> list[SearchHit]:
        if top_k <= 0 or _blocks_everything(search_filter):
            return []
        with self._handle.lock:
            result = self._handle.client.query_points(
                self._name,
                query=list(embedding),
                query_filter=_to_qdrant_filter(search_filter),
                limit=top_k,
                with_payload=True,
            )
        hits: list[SearchHit] = []
        for point in result.points:
            payload = dict(point.payload or {})
            text = str(payload.pop(_TEXT_KEY, ""))
            hits.append(
                SearchHit(
                    chunk_id=str(payload.get("chunk_id", "")),
                    text=text,
                    score=float(point.score),
                    metadata=payload,
                )
            )
        return hits

    def _document_condition(self, document_id: str) -> models.FieldCondition:
        return models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))

    def delete_document(self, document_id: str) -> None:
        selector = models.FilterSelector(
            filter=models.Filter(must=[self._document_condition(document_id)])
        )
        with self._handle.lock:
            self._handle.client.delete(self._name, points_selector=selector)

    def delete_stale(self, document_id: str, keep_chunk_ids: Sequence[str]) -> None:
        keep = models.FieldCondition(
            key="chunk_id", match=models.MatchAny(any=list(keep_chunk_ids))
        )
        selector = models.FilterSelector(
            filter=models.Filter(
                must=[self._document_condition(document_id)],
                must_not=[keep] if keep_chunk_ids else None,
            )
        )
        with self._handle.lock:
            self._handle.client.delete(self._name, points_selector=selector)

    def count(self, document_id: str | None = None) -> int:
        flt = models.Filter(must=[self._document_condition(document_id)]) if document_id else None
        with self._handle.lock:
            return self._handle.client.count(self._name, count_filter=flt, exact=True).count
