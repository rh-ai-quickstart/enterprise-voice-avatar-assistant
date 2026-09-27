// Stand-in for the RAG API and the ingestion service, for the workflow tests (compose.yaml).
// Every request is recorded; GET /_log returns them, POST /_reset {tickets, jobFails} starts over.
import http from "node:http";

let tickets = {};
let log = [];
let nextEvent = 1;
let jobPolls = 0;
let jobFails = false;

const send = (res, code, body) => {
  res.writeHead(code, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
};

http
  .createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const type = req.headers["content-type"] || "";
    let body = null;
    if (type.includes("json") && raw) body = JSON.parse(raw);
    else if (type.includes("multipart")) {
      const filename = (raw.match(/filename="([^"]+)"/) || [])[1];
      const bucket = (raw.match(/name="bucket"\r\n\r\n([^\r]+)/) || [])[1];
      body = { filename, bucket, has_transcript: raw.includes("# Assistant transcript") };
    }
    const url = new URL(req.url, "http://stub");
    if (url.pathname === "/_log") return send(res, 200, log);
    if (url.pathname === "/_reset") {
      tickets = body.tickets || {};
      jobFails = Boolean(body.jobFails);
      jobPolls = 0;
      log = [];
      return send(res, 200, {});
    }
    log.push({ method: req.method, path: url.pathname, query: url.search, auth: req.headers.authorization || null, body });

    let m = url.pathname.match(/^\/v1\/tickets\/(REQ-\d+)$/);
    if (m) {
      const ticket = tickets[m[1]];
      if (!ticket) return send(res, 404, { detail: "ticket not found" });
      if (req.method === "PATCH") {
        if (body.status) {
          ticket.status = body.status;
          if (["approved", "rejected"].includes(body.status)) {
            ticket.approver = body.actor;
            ticket.decision_note = body.note;
          }
          if (["approved", "rejected", "cancelled"].includes(body.status)) {
            ticket.payload.decision = { status: body.status, via: body.via || "api", by: body.actor };
          }
        }
        Object.assign(ticket.payload, body.payload || {});
      }
      return send(res, 200, ticket);
    }
    if ((m = url.pathname.match(/^\/v1\/sessions\/([^/]+)\/transcript$/))) {
      res.writeHead(200, { "content-type": "text/plain" });
      return res.end(`[2026-09-25 10:00] user: hello from ${m[1]}`);
    }
    if (url.pathname === "/v1/ingest") return send(res, 202, { job_id: "job-1", doc_id: "doc-1", status: "queued" });
    if (url.pathname === "/v1/ingest/upload") return send(res, 202, { job_id: "job-1", doc_id: "doc-transcript", status: "queued" });
    if (url.pathname === "/v1/jobs/job-1") {
      jobPolls += 1;
      const status = jobPolls < 2 ? "running" : jobFails ? "failed" : "done";
      return send(res, 200, {
        job_id: "job-1",
        doc_id: "doc-transcript",
        bucket: "documents",
        key: "handbook.pdf",
        status,
        chunks: status === "done" ? 12 : null,
        pages: status === "done" ? 3 : null,
        error: status === "failed" ? "ConversionError: empty file" : null,
      });
    }
    if (url.pathname === "/v1/classify") {
      return send(res, 200, { doc_type: "invoice", confidence: 0.9, summary: "An invoice", fields: { total: "12.00" }, doc_id: "doc-2", source: body.key });
    }
    if ((m = url.pathname.match(/^\/v1\/internal\/archives\/(\d+)$/))) {
      return send(res, 200, { id: Number(m[1]), session_id: "s1", status: body.status });
    }
    if (url.pathname === "/v1/internal/events") return send(res, 201, { id: nextEvent++ });
    if (url.pathname === "/v1/tickets/stale") {
      return send(res, 200, {
        remind: [{ ticket_ref: "REQ-000002", title: "Laptop for the new hire", priority: "normal", requester: "Alex", pending_minutes: 75.4 }],
        escalate: [{ ticket_ref: "REQ-000003", title: "VPN access", priority: "normal", requester: "Sam", pending_minutes: 250.2 }],
      });
    }
    if (url.pathname === "/v1/tickets/stale/escalate") {
      return send(res, 200, { ticket_ref: url.searchParams.get("ticket_ref"), priority: "high" });
    }
    if (url.pathname === "/v1/knowledge-gaps/digest") {
      return send(res, 200, {
        period_hours: 24,
        total_gaps: 5,
        by_reason: { low_score: 4, no_hits: 1 },
        top_questions: [{ question: "How do I reset my VPN token?", times_asked: 2, avg_score: 0.41 }],
        groups: [
          { question: "How do I reset my VPN token?", times_asked: 3, wordings: 2, best_score: 0.41 },
          { question: "What is the parental leave policy?", times_asked: 2, wordings: 1, best_score: 0.2 },
        ],
      });
    }
    send(res, 200, {});
  })
  .listen(8080, () => console.log("stub RAG API and ingestion service on 8080"));
