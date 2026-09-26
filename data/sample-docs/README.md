# Sample documents

A synthetic document set for the demo: **Example Corp**, a mid-sized company with a head office, regional offices and about 800 employees. The documents deliberately name no industry or country, so the assistant can stand in for any organization. Nothing here is real company data; every company, person, bank account, and number is fictional.

The Markdown sources live in `src/`. `scripts/build-sample-docs.sh` renders the
demo set from them as a mix of Markdown, DOCX (pandoc), and PDF (LibreOffice), so
the ingestion pipeline exercises Docling on office formats, not only on text.

## Policies and procedures (bucket `documents`, indexed for questions)

| File | Try asking |
|---|---|
| `password-policy.md` | How often must administrator passwords be rotated? What happens after ten failed sign-in attempts? |
| `remote-work-policy.docx` | How many days a week can I work remotely? Can I work from another country? |
| `expense-reimbursement-policy.pdf` | What is the hotel rate cap outside the head office city? Who approves an expense claim of 800 euros? |
| `it-equipment-procedure.docx` | What is the laptop replacement cycle? What should I do if my laptop is stolen? |
| `new-hire-onboarding-procedure.pdf` | When is the laptop shipped to a new hire? Which trainings are mandatory in the first week? |
| `incident-response-procedure.pdf` | What is the response time for a Sev 1 incident? How often are Sev 1 status updates sent? |
| `leave-policy.docx` | How many days of annual leave do I get? How many days can I carry over? |
| `data-classification-policy.pdf` | What are the four classification levels? Can I print a confidential document at home? |
| `software-request-procedure.docx` | Who approves a licence that costs 500 euros per user per year? Is GPL software allowed? |
| `it-service-catalog.pdf` | How long does a standard laptop take to deliver? What are the service desk hours? |

## Invoices and contracts (bucket `inbox`, classified and extracted, not indexed)

| File | Expected classification |
|---|---|
| `invoice-INV-2026-0042.pdf` | invoice: Northwind Office Supplies, EUR 7,639.94, due 2026-09-27 |
| `invoice-SKY-2026-0817.pdf` | invoice: Skyline Cloud Hosting, EUR 4,320.00, reverse-charge tax |
| `invoice-MT-88213.md` | invoice: Meridian Travel, EUR 931.75 |
| `contract-northwind-supply-agreement.pdf` | contract: master supply agreement, two-year term, net 30 |
| `contract-managed-services-blue-harbor.docx` | contract: managed IT services bought by Example Corp, EUR 18,500 per month, 12-month term |

## Loading the set into a deployment

```bash
NS=voice-avatar-assistant scripts/load-sample-docs.sh
```

The script uploads through the ingestion pod: policies to `documents`, invoices
and contracts to `inbox`. The object store notifies n8n, which runs the
ingestion workflow (WF2) for `documents` and hands `inbox` objects to the
classification workflow (WF3). Pass file names to upload a subset, or paths to
files of your own (`BUCKET=inbox` sends them to classification).

To bypass n8n during development, call the ingestion service directly:

```bash
curl -X POST http://ingestion:8080/v1/ingest -H 'Content-Type: application/json' \
  -d '{"bucket": "documents", "key": "leave-policy.docx"}'
```

## Editing or adding documents

Edit or add a Markdown file in `src/`, add it to the format list in
`scripts/build-sample-docs.sh`, run the script, and commit the sources together
with the rendered files. Keep the facts consistent across documents: the
assistant is judged on citing them correctly.
