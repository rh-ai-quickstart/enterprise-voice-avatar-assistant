"""Qdrant collection management and document-level upsert/delete."""

import uuid
from typing import Any

from qdrant_client import QdrantClient, models

from .config import settings

_client: QdrantClient | None = None


def client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=60)
    return _client


def ping() -> None:
    client().get_collections()


def ensure_collection(dimension: int) -> None:
    name = settings.qdrant_collection
    if not client().collection_exists(name):
        client().create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
        )
        client().create_payload_index(
            collection_name=name, field_name="doc_id", field_schema=models.PayloadSchemaType.KEYWORD
        )


def _doc_filter(doc_id: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))])


def delete_document(doc_id: str) -> None:
    name = settings.qdrant_collection
    if client().collection_exists(name):
        client().delete(
            collection_name=name, points_selector=models.FilterSelector(filter=_doc_filter(doc_id)), wait=True
        )


def count_document(doc_id: str) -> int:
    name = settings.qdrant_collection
    if not client().collection_exists(name):
        return 0
    return client().count(collection_name=name, count_filter=_doc_filter(doc_id), exact=True).count


def replace_document(
    doc_id: str,
    source: str,
    source_uri: str,
    chunks: list[Any],
    vectors: list[list[float]],
    metadata: dict[str, Any],
) -> int:
    """Delete every point of the document, then upsert the new chunks. Point ids are stable per chunk index."""
    if not vectors:
        return 0
    ensure_collection(len(vectors[0]))
    delete_document(doc_id)
    points = [
        models.PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{chunk.index}")),
            vector=vector,
            payload={
                "doc_id": doc_id,
                "source": source,
                "source_uri": source_uri,
                "page": chunk.page,
                "chunk_index": chunk.index,
                "headings": chunk.headings,
                "text": chunk.text,
                "metadata": metadata,
            },
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    for start in range(0, len(points), 64):
        client().upsert(
            collection_name=settings.qdrant_collection, points=points[start : start + 64], wait=True
        )
    return len(points)
