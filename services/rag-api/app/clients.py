"""Lazily constructed clients for the LLM, embeddings, guardrails, Qdrant, and PostgreSQL."""

import threading
from functools import lru_cache

import httpx
from openai import OpenAI
from qdrant_client import QdrantClient

from .config import settings
from .tls import tls_context


def _http() -> httpx.Client:
    # Voice turns come seconds apart: with httpx's default 5 s keep-alive each one paid a new TCP
    # and TLS handshake to the model gateway
    return httpx.Client(
        verify=tls_context(),
        timeout=httpx.Timeout(120.0, connect=10.0),
        limits=httpx.Limits(keepalive_expiry=300.0),
    )


@lru_cache(maxsize=1)
def llm() -> OpenAI:
    return OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "none",
        http_client=_http(),
        max_retries=2,
    )


@lru_cache(maxsize=1)
def embeddings() -> OpenAI:
    return OpenAI(
        base_url=settings.embeddings_base_url,
        api_key=settings.embeddings_api_key or "none",
        http_client=_http(),
        max_retries=2,
    )


@lru_cache(maxsize=1)
def guardrails() -> OpenAI:
    return OpenAI(
        base_url=settings.guardrails_base_url,
        api_key=settings.guardrails_api_key or "none",
        http_client=_http(),
        max_retries=1,
    )


@lru_cache(maxsize=1)
def qdrant() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=30)


_pools: dict = {}  # DATABASE_URL -> its connection pool; one at a time
_pools_lock = threading.Lock()


def _pool(url: str):
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    with _pools_lock:
        pool = _pools.get(url)
        if pool is None:
            # a changed URL (tests give each test its own schema) closes the previous pool and
            # its connections instead of leaving them open
            for old in _pools.values():
                old.close(timeout=1)
            _pools.clear()
            # Connections are reused (a chat turn runs several queries, each paid a new
            # connection), and each is checked before it is handed out, so a restarted
            # PostgreSQL costs no failed request
            pool = _pools[url] = ConnectionPool(
                url,
                min_size=1,
                max_size=8,
                timeout=5,
                kwargs={"connect_timeout": 5, "row_factory": dict_row},
                check=ConnectionPool.check_connection,
                open=True,
            )
        return pool


def db():
    """A pooled PostgreSQL connection with dict rows. Callers use it as a context manager:
    the block's work is committed at the end (rolled back on an error) and the connection reused."""
    return _pool(settings.database_url).connection()
