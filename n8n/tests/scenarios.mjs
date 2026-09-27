// Workflow scenarios against n8n and the stub (compose.yaml). Each one resets the stub, calls a
// webhook as the RAG API, Slack or the object store would, and compares what the workflow asked of
// the RAG API and the ingestion service. With SLACK_ON, Slack is unreachable, so each scenario also
// names the Slack nodes that must report their failure to the activity feed.
import crypto from "node:crypto";

const N8N = "http://n8n:5678/webhook";
const STUB = "http://stub:8080";
const { TOKEN, SIGNING_SECRET } = process.env;
const SLACK_ON = process.env.SLACK_ON === "true";
const auth = { authorization: `Bearer ${TOKEN}` };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const post = (url, body, headers = {}) =>
  fetch(url, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body) });
const reset = (extra = {}) => post(`${STUB}/_reset`, { tickets: {}, ...extra });
const log = async () => (await fetch(`${STUB}/_log`)).json();

const isSlackFailure = (c) => c.path === "/v1/internal/events" && c.body?.kind === "integration.error" && c.body?.title?.startsWith("Slack: ");

/** What the workflow did once `done(calls)` holds or `ms` passed: the calls to the stub, and the
 * Slack nodes that reported a failure. */
async function outcome({ ms = 4000, done = null, limit = 40000 } = {}) {
  const start = Date.now();
  let calls = [];
  for (;;) {
    await sleep(done ? 1000 : ms);
    calls = await log();
    if (!done || done(calls) || Date.now() - start > limit) break;
  }
  if (done) calls = await (sleep(1500).then(log));
  if (calls.some((c) => c.auth !== `Bearer ${TOKEN}` && !c.path.startsWith("/v1/sessions/") && c.path !== "/v1/chat")) {
    return { calls: ["A CALL WITHOUT THE INTERNAL TOKEN"], slack: [] };
  }
  return {
    calls: calls.filter((c) => !isSlackFailure(c)),
    slack: calls.filter(isSlackFailure).map((c) => c.body.title.replace(/^Slack: (.*) failed$/, "$1")).sort(),
  };
}

// n8n registers the webhooks a moment after its health check passes: wait until one answers as
// registered ("not registered for GET requests" means the POST webhook exists)
for (let i = 0; ; i++) {
  const answer = await fetch(`${N8N}/request-intake`).then((r) => r.text()).catch(() => "");
  if (answer.includes("GET requests")) break;
  if (i === 60) throw new Error(`the workflows' webhooks never registered: ${answer}`);
  await sleep(1000);
}

let failed = 0;
function expect(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failed++;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : `\n     got  ${JSON.stringify(got, null, 1)}\n     want ${JSON.stringify(want, null, 1)}`}`);
}
/** The Slack nodes expected to fail: none with Slack off. */
const slack = (...nodes) => (SLACK_ON ? nodes.sort() : []);

// ---------------------------------------------------------------- WF4 approvals ------------

const ticket = (status, extra = {}) => ({
  id: 1, ticket_ref: "REQ-000001", title: "Install VS Code", category: "software", priority: "normal", requester: "Alex",
  status, session_id: "s1", approver: null, decision_note: null,
  payload: { channel: "chat", summary: "wants VS Code", slack: { channel: "C1", ts: "111.222" }, ...extra }, events: [],
});
const tickets = (t) => reset({ tickets: { "REQ-000001": t } });
// A ticket call as a line: "PATCH /v1/tickets/REQ-000001 approved via slack"
const line = (c) => `${c.method} ${c.path}${c.body?.status ? ` ${c.body.status}` : ""}${c.body?.via ? ` via ${c.body.via}` : ""}${c.body?.title ? ` ${c.body.title}` : ""}`;
const lines = (o) => ({ calls: o.calls.map(line), slack: o.slack });

async function click(action, secret = SIGNING_SECRET, ts = Math.floor(Date.now() / 1000)) {
  const payload = {
    type: "block_actions", user: { id: "U1", username: "mo" }, channel: { id: "C1" },
    container: { channel_id: "C1", message_ts: "111.222" }, actions: [{ action_id: action, value: "REQ-000001" }],
  };
  const raw = "payload=" + encodeURIComponent(JSON.stringify(payload));
  const signature = "v0=" + crypto.createHmac("sha256", secret).update(`v0:${ts}:${raw}`).digest("hex");
  return fetch(`${N8N}/slack-interactions`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", "x-slack-signature": signature, "x-slack-request-timestamp": String(ts) },
    body: raw,
  });
}
const decide = (decision, headers, t = ticket(decision)) =>
  post(`${N8N}/ticket-decided`, { via: "portal", decision, actor: "Dana", note: "", ticket: t }, headers);

await tickets(ticket("pending_approval"));
await decide("approved", { authorization: "Bearer wrong" }, ticket("approved"));
expect("WF4: a portal decision with a wrong token is ignored", lines(await outcome()), { calls: [], slack: [] });

await tickets(ticket("approved"));
await decide("approved", auth);
expect("WF4: a portal approval is fulfilled and shown on the card", lines(await outcome()), {
  calls: ["PATCH /v1/tickets/REQ-000001 fulfilled"],
  slack: slack("Update Slack card", "Notify fulfilment"),
});

await tickets(ticket("cancelled"));
await decide("cancelled", auth);
expect("WF4: a portal cancellation is not fulfilled", lines(await outcome()), {
  calls: [],
  slack: slack("Update Slack card", "Notify rejection"),
});

await tickets(ticket("pending_approval"));
await click("approve_request");
expect("WF4: a signed Slack approval is checked, recorded and fulfilled", lines(await outcome()), {
  calls: ["GET /v1/tickets/REQ-000001", "PATCH /v1/tickets/REQ-000001 approved via slack", "PATCH /v1/tickets/REQ-000001 fulfilled"],
  slack: slack("Update Slack card", "Notify fulfilment"),
});

await tickets(ticket("pending_approval"));
await click("reject_request");
expect("WF4: a signed Slack rejection is recorded", lines(await outcome()), {
  calls: ["GET /v1/tickets/REQ-000001", "PATCH /v1/tickets/REQ-000001 rejected via slack"],
  slack: slack("Update Slack card", "Notify rejection"),
});

await tickets(ticket("approved", { decision: { status: "approved", via: "portal", by: "Dana" } }));
await click("reject_request");
expect("WF4: a late click on a ticket decided in the portal changes nothing", lines(await outcome()), {
  calls: ["GET /v1/tickets/REQ-000001"],
  slack: slack("Show the outcome on the card", "Reply to late click"),
});

await tickets(ticket("pending_approval"));
await click("approve_request", "not-the-secret");
expect("WF4: a forged click is refused and reported", lines(await outcome()), {
  calls: ["POST /v1/internal/events Slack click refused: the Slack signature does not match"],
  slack: [],
});

await tickets(ticket("pending_approval"));
await click("approve_request", SIGNING_SECRET, Math.floor(Date.now() / 1000) - 3600);
expect("WF4: a replayed click is refused", lines(await outcome()), {
  calls: ["POST /v1/internal/events Slack click refused: the Slack timestamp is more than five minutes off"],
  slack: [],
});

await tickets(ticket("pending_approval"));
await fetch(`${N8N}/slack-interactions`, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: "payload=%7B%7D" });
expect("WF4: an unsigned click is refused", lines(await outcome()), {
  calls: ["POST /v1/internal/events Slack click refused: the request carries no Slack signature"],
  slack: [],
});

await tickets(ticket("classified"));
await post(`${N8N}/request-intake`, { ticket: ticket("classified"), classification: { needs_approval: false, summary: "s" }, channel: "chat" });
expect("WF4: request intake without the token is ignored", lines(await outcome()), { calls: [], slack: [] });

await tickets(ticket("classified"));
await post(`${N8N}/request-intake`, { ticket: ticket("classified"), classification: { needs_approval: false, summary: "s" }, channel: "chat" }, auth);
expect("WF4: no approval needed, approved by the workflow", lines(await outcome()), {
  calls: ["PATCH /v1/tickets/REQ-000001 approved via workflow"],
  slack: [],
});

await tickets(ticket("pending_approval"));
await post(`${N8N}/request-intake`, { ticket: ticket("pending_approval"), classification: { needs_approval: true, summary: "s" }, channel: "chat" }, auth);
// With Slack on the card cannot be posted (no internet), so no card reference is stored either
expect("WF4: approval needed, the card is asked for", lines(await outcome()), { calls: [], slack: slack("Ask for approval") });

// ---------------------------------------------------------------- WF5 archival -------------

const reported = (calls) => calls.some((c) => c.path.startsWith("/v1/internal/archives/"));
const polledTwice = (calls) => calls.filter((c) => c.path.startsWith("/v1/jobs/")).length >= 2;
const archive = { session_id: "s1", title: "Assistant transcript s1", doc_url: "", archive_id: 7 };
const detailed = (o) => ({
  calls: o.calls.map((c) => `${c.method} ${c.path}${c.body && c.method !== "GET" ? ` ${JSON.stringify(c.body)}` : ""}`),
  slack: o.slack,
});

await reset();
await post(`${N8N}/archive-transcript`, archive);
expect("WF5: an archive request without the token is ignored", detailed(await outcome({ ms: 3000 })), { calls: [], slack: [] });

await reset();
await post(`${N8N}/archive-transcript`, archive, auth);
expect("WF5: the transcript is fetched, uploaded, its job polled and the result reported", detailed(await outcome({ done: reported })), {
  calls: [
    "GET /v1/sessions/s1/transcript",
    'POST /v1/ingest/upload {"filename":"transcript-s1.md","bucket":"transcripts","has_transcript":true}',
    "GET /v1/jobs/job-1",
    "GET /v1/jobs/job-1",
    'PATCH /v1/internal/archives/7 {"status":"indexed","object_key":"transcript-s1.md","doc_id":"doc-transcript","job_id":"job-1","error":null}',
  ],
  slack: slack("Notify archival"),
});

await reset({ jobFails: true });
await post(`${N8N}/archive-transcript`, archive, auth);
const failedRun = detailed(await outcome({ done: reported }));
expect("WF5: a failed re-ingestion is reported as failed", failedRun.calls.at(-1),
  'PATCH /v1/internal/archives/7 {"status":"failed","object_key":"transcript-s1.md","doc_id":"doc-transcript","job_id":"job-1","error":"ConversionError: empty file"}');

await reset();
await post(`${N8N}/archive-transcript`, { session_id: "s1", title: "t", doc_url: "" }, auth);
const unrecorded = await outcome({ done: polledTwice });
expect("WF5: without an archive record nothing is reported", unrecorded.calls.filter((c) => c.path.startsWith("/v1/internal/archives/")), []);

// ---------------------------------------------------------------- WF2, WF3: documents ------

const event = (c) => {
  const b = c.body;
  return `${b.kind} ${b.severity} ${b.ref_type}:${b.ref_id ?? "-"} ${b.title}${b.detail ? ` | ${b.detail}` : ""}${Object.keys(b.data || {}).length ? ` ${JSON.stringify(b.data)}` : ""}`;
};
const events = (o) => ({ events: o.calls.filter((c) => c.path === "/v1/internal/events").map(event).sort(), slack: o.slack });
const created = (bucket, key) => ({ Records: [{ eventName: "s3:ObjectCreated:Put", s3: { bucket: { name: bucket }, object: { key, size: 100 } } }] });
const fed = (count) => (calls) => calls.filter((c) => c.path === "/v1/internal/events").length >= count;

await reset();
await post(`${N8N}/object-created`, created("documents", "handbook.pdf"));
expect("WF2: an indexed document reaches the feed", events(await outcome({ done: fed(SLACK_ON ? 2 : 1) })), {
  events: ['document.ingested success document:doc-transcript handbook.pdf indexed (12 chunks) {"bucket":"documents","key":"handbook.pdf","job_id":"job-1","chunks":12,"pages":3}'],
  slack: slack("Notify ingestion"),
});

await reset({ jobFails: true });
await post(`${N8N}/object-created`, created("documents", "handbook.pdf"));
expect("WF2: a document that could not be indexed reaches the feed", events(await outcome({ done: fed(SLACK_ON ? 2 : 1) })), {
  events: ['document.ingest_failed error document:doc-transcript handbook.pdf could not be indexed | ConversionError: empty file {"bucket":"documents","key":"handbook.pdf","job_id":"job-1","chunks":null,"pages":null}'],
  slack: slack("Notify ingestion"),
});

await reset();
await post(`${N8N}/object-created`, created("transcripts", "transcript-s1.md"));
expect("WF2: objects outside EVENT_BUCKETS are left alone", (await outcome({ ms: 3000 })).calls.map(line), []);

await reset();
await post(`${N8N}/object-created`, created("inbox", "invoice-0042.pdf"));
const classified = await outcome({ done: (calls) => calls.some((c) => c.path === "/v1/classify"), limit: 20000 });
expect("WF2 and WF3: an inbox file is classified with the token", classified.calls.filter((c) => c.path === "/v1/classify").map((c) => `${c.method} ${c.path} ${JSON.stringify(c.body)}`), [
  'POST /v1/classify {"bucket":"inbox","key":"invoice-0042.pdf"}',
]);

// ---------------------------------------------------------------- WF6, WF7: schedules --------

await reset();
await post(`${N8N}/run-wf6`, {});
const wf6 = await outcome({ done: fed(SLACK_ON ? 3 : 1) });
expect("WF6: one reminder per ticket in the feed, the escalation through the RAG API", {
  ...events(wf6),
  escalated: wf6.calls.filter((c) => c.path.endsWith("/escalate")).map((c) => c.query),
}, {
  events: ['ticket.sla_reminder warning ticket:REQ-000002 REQ-000002 waits for approval for 75 minutes: Laptop for the new hire {"minutes":75,"priority":"normal"}'],
  slack: slack("Notify escalation", "Post reminder"),
  escalated: ["?ticket_ref=REQ-000003&current_priority=normal"],
});

await reset();
await post(`${N8N}/run-wf7`, {});
expect("WF7: the digest by groups reaches the feed", events(await outcome({ done: fed(SLACK_ON ? 2 : 1) })), {
  events: ['gaps.digest info gap:- Knowledge gap digest: 5 open questions in 2 groups over 24 hours {"total":5,"groups":2}'],
  slack: slack("Post digest"),
});

console.log(failed ? `${failed} scenario(s) failed (Slack ${SLACK_ON ? "on" : "off"})` : `all scenarios passed (Slack ${SLACK_ON ? "on" : "off"})`);
process.exit(failed ? 1 : 0);
