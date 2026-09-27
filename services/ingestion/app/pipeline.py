"""Document pipeline: download, Docling conversion, chunking, embeddings, Qdrant upsert.

Docling and its models are imported lazily so that the API starts fast and unit
tests do not need the models.
"""

import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import db, embeddings, storage, vectorstore
from .config import settings

log = logging.getLogger("ingestion.pipeline")

_converter = None
_chunker = None


def make_doc_id(bucket: str, key: str) -> str:
    """Stable id per object: the same bucket and key always map to the same document."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"s3://{bucket}/{key}"))


@dataclass
class Chunk:
    index: int
    text: str
    page: int | None = None
    headings: list[str] = field(default_factory=list)


def get_converter():
    global _converter
    if _converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions(
            do_ocr=settings.ocr_enabled,
            do_table_structure=True,
            artifacts_path=settings.docling_artifacts_path or None,
        )
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
    return _converter


def get_chunker():
    global _chunker
    if _chunker is None:
        from docling.chunking import HybridChunker

        try:
            from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
            from transformers import AutoTokenizer

            tokenizer = HuggingFaceTokenizer(
                tokenizer=AutoTokenizer.from_pretrained(settings.chunk_tokenizer),
                max_tokens=settings.chunk_max_tokens,
            )
            _chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("falling back to the default chunker tokenizer: %s", exc)
            _chunker = HybridChunker(max_tokens=settings.chunk_max_tokens, merge_peers=True)
    return _chunker


def convert(path: Path):
    result = get_converter().convert(str(path))
    return result.document


def chunk(document) -> list[Chunk]:
    chunker = get_chunker()
    chunks: list[Chunk] = []
    for index, item in enumerate(chunker.chunk(document)):
        text = (chunker.contextualize(chunk=item) or item.text or "").strip()
        if not text:
            continue
        page = None
        try:
            prov = item.meta.doc_items[0].prov
            if prov:
                page = int(prov[0].page_no)
        except (AttributeError, IndexError, TypeError, ValueError):
            page = None
        headings = list(getattr(item.meta, "headings", None) or [])
        chunks.append(Chunk(index=index, text=text, page=page, headings=headings))
    return chunks


def extract_text(bucket: str, key: str, max_chars: int = 60000) -> dict[str, Any]:
    """Convert one object and return its content as Markdown (used for classification and extraction)."""
    path = storage.download(bucket, key)
    try:
        document = convert(path)
        text = document.export_to_markdown()
        truncated = len(text) > max_chars
        pages = None
        try:
            pages = len(document.pages) if getattr(document, "pages", None) else None
        except TypeError:
            pages = None
        return {"text": text[:max_chars], "truncated": truncated, "pages": pages}
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def ingest_document(bucket: str, key: str, doc_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Run the full pipeline for one object. Returns chunk and page counts."""
    path = storage.download(bucket, key)
    try:
        document = convert(path)
        chunks = chunk(document)
        if not chunks:
            raise ValueError("no text could be extracted from the document")
        vectors = embeddings.embed([c.text for c in chunks])
        source_uri = f"s3://{bucket}/{key}"
        count = vectorstore.replace_document(doc_id, key, source_uri, chunks, vectors, metadata)
        pages = None
        try:
            pages = len(document.pages) if getattr(document, "pages", None) else None
        except TypeError:
            pages = None
        db.upsert_document(doc_id, key, source_uri, pages, count, metadata)
        log.info("ingested %s: %d chunks, %s pages", source_uri, count, pages)
        return {"chunks": count, "pages": pages}
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
