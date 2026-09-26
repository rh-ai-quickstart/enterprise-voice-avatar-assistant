"""Document classification and structured field extraction with the LLM."""

import json
import logging
import re
import uuid
from typing import Any

import httpx

from . import auth, clients, memory
from .config import settings
from .schemas import ClassifyRequest, ClassifyResponse

log = logging.getLogger("rag.classify")

DOC_TYPES = ["invoice", "contract", "purchase_order", "policy", "receipt", "other"]
FIELDS = {
    "invoice": "vendor, vendor_address, customer, invoice_number, invoice_date, due_date, currency, subtotal, tax, "
    "total_amount, line_items (list of {description, quantity, unit_price, amount}), payment_terms",
    "contract": "title, parties (list), effective_date, expiry_date, term, total_value, currency, governing_law, "
    "auto_renewal (bool), notice_period, signatories (list)",
    "purchase_order": "po_number, vendor, buyer, order_date, delivery_date, currency, total_amount, "
    "items (list of {description, quantity, unit_price, amount})",
    "policy": "title, owner, version, effective_date, scope, key_rules (list)",
    "receipt": "merchant, date, currency, total_amount, payment_method, items (list)",
    "other": "title, date, author, key_points (list)",
}
MAX_TEXT = 24000


def make_doc_id(bucket: str, key: str) -> str:
    """Same formula as the ingestion service so classification results attach to the ingested record."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"s3://{bucket}/{key}"))


def extract_text(bucket: str, key: str) -> dict[str, Any]:
    url = settings.ingestion_url.rstrip("/") + "/v1/extract"
    with httpx.Client(timeout=180) as http:
        response = http.post(
            url, json={"bucket": bucket, "key": key, "max_chars": MAX_TEXT}, headers=auth.internal_headers()
        )
        response.raise_for_status()
        return response.json()


def _prompt(text: str, filename: str | None) -> list[dict[str, str]]:
    hints = "\n".join(f"- {t}: {FIELDS[t]}" for t in DOC_TYPES)
    system = (
        "You classify business documents and extract structured fields. Respond with a single JSON object and nothing else, "
        'with keys: "doc_type" (one of '
        + ", ".join(DOC_TYPES)
        + '), "confidence" (0 to 1), "summary" (one sentence), '
        '"fields" (object). Use these field names per document type; omit fields that are not present, never invent values:\n'
        + hints
        + "\nDates use ISO format (YYYY-MM-DD). Amounts are numbers without currency symbols."
    )
    user = (f"File name: {filename}\n\n" if filename else "") + "Document:\n" + text[:MAX_TEXT]
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_json(content: str) -> dict[str, Any]:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def classify_text(text: str, filename: str | None = None) -> dict[str, Any]:
    messages = _prompt(text, filename)
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 1200,
    }
    try:
        completion = clients.llm().chat.completions.create(response_format={"type": "json_object"}, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.info("json_object response format unsupported (%s); retrying without it", type(exc).__name__)
        completion = clients.llm().chat.completions.create(**kwargs)
    data = _parse_json(completion.choices[0].message.content or "{}")
    doc_type = str(data.get("doc_type", "other")).lower().replace(" ", "_")
    if doc_type not in DOC_TYPES:
        doc_type = "other"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    return {
        "doc_type": doc_type,
        "confidence": confidence,
        "summary": str(data.get("summary", "")),
        "fields": fields,
    }


def classify_document(request: ClassifyRequest) -> ClassifyResponse:
    doc_id = source = None
    text = request.text
    filename = request.filename
    if not text:
        if not request.key:
            raise ValueError("either text or a bucket/key is required")
        bucket = request.bucket or "inbox"
        extracted = extract_text(bucket, request.key)
        text, doc_id, source = extracted["text"], extracted["doc_id"], request.key
        filename = filename or request.key
        source_uri = f"s3://{bucket}/{request.key}"
    result = classify_text(text, filename)
    if doc_id and source:
        memory.record_extraction(doc_id, source, source_uri, result["doc_type"], result)
    return ClassifyResponse(doc_id=doc_id, source=source, model=settings.llm_model, **result)
