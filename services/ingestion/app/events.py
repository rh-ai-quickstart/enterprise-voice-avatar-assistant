"""Parsing of S3 bucket notifications (AWS-style Records; MinIO's top-level Key as a fallback)."""

from urllib.parse import unquote_plus

CREATED = "created"
REMOVED = "removed"


def parse_s3_event(event: dict) -> list[tuple[str, str, str]]:
    """Return (kind, bucket, key) triples for every record in an S3 notification.

    Object keys in AWS-style Records are URL-encoded; a top-level Key ("bucket/key", as MinIO
    sends) is used as a fallback when Records is missing.
    """
    results: list[tuple[str, str, str]] = []
    name = str(event.get("EventName", ""))
    records = event.get("Records") or []
    for record in records:
        s3 = record.get("s3") or {}
        bucket = (s3.get("bucket") or {}).get("name")
        key = (s3.get("object") or {}).get("key")
        if not bucket or not key:
            continue
        kind = _kind(record.get("eventName") or name)
        if kind:
            results.append((kind, bucket, unquote_plus(key)))
    if not results and "Key" in event and "/" in event["Key"]:
        bucket, key = event["Key"].split("/", 1)
        kind = _kind(name)
        if kind:
            results.append((kind, bucket, key))
    return results


def _kind(event_name: str) -> str | None:
    if "ObjectCreated" in event_name:
        return CREATED
    if "ObjectRemoved" in event_name:
        return REMOVED
    return None
