"""S3-compatible object storage access (MinIO in the chart)."""

import tempfile
from pathlib import Path

import boto3
from botocore.config import Config

from .config import settings

_client = None


def client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )
    return _client


def head_bucket(bucket: str) -> None:
    client().head_bucket(Bucket=bucket)


def download(bucket: str, key: str) -> Path:
    """Download an object into a fresh temporary directory and return the file path."""
    directory = Path(tempfile.mkdtemp(prefix="ingest-"))
    path = directory / (Path(key).name or "document")
    client().download_file(bucket, key, str(path))
    return path


def delete_object(bucket: str, key: str) -> None:
    client().delete_object(Bucket=bucket, Key=key)


def put_bytes(bucket: str, key: str, data: bytes, content_type: str | None = None) -> None:
    extra = {"ContentType": content_type} if content_type else {}
    client().put_object(Bucket=bucket, Key=key, Body=data, **extra)
