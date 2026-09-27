// Stand-ins for the model server and n8n in the end-to-end stack (frontend/e2e/compose.yaml).
//
// :8000  OpenAI-compatible: /v1/models, /v1/embeddings, /v1/chat/completions. It tells the RAG API's
//        prompts apart by their system message: intent detection, request triage, or an answer.
// :5678  n8n: the webhooks the RAG API calls, answering like WF4 and WF5 would, and recording every
//        call. /_calls lists them, /_reset clears them and sets whether Slack is on.
import http from "node:http";

const RAG_API_URL = process.env.RAG_API_URL ?? "http://rag-api:8080";
const TOKEN = process.env.INTERNAL_API_TOKEN ?? "";
const DIMENSIONS = 16;

const read = async (req) => {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  return raw ? JSON.parse(raw) : {};
};
const send = (res, code, body) => {
  res.writeHead(code, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
};

// ------------------------------------------------------------------ model server ----------

/** A stable unit vector per text, so the same question always embeds the same way. */
function embed(text) {
  const vector = new Array(DIMENSIONS).fill(0);
  for (const [i, ch] of [...text.toLowerCase()].entries()) vector[(ch.charCodeAt(0) + i) % DIMENSIONS] += 1;
  const norm = Math.hypot(...vector) || 1;
  return vector.map((v) => v / norm);
}

const REQUEST = /\b(i need|i'd like|please install|install|give me|access to|new laptop|replace)\b/i;
const TRIAGE = [
  [/visual studio code/i, { title: "Install Visual Studio Code", category: "software", details: { software: "Visual Studio Code" } }],
  [/finance share/i, { title: "Grant access to the finance share", category: "access_request", details: { resource: "finance share" } }],
  [/laptop/i, { title: "Replace a laptop that no longer boots", category: "hardware", priority: "high", details: { equipment: "laptop" } }],
];

function reply(messages) {
  const system = messages.find((m) => m.role === "system")?.content ?? "";
  const user = messages.filter((m) => m.role === "user").at(-1)?.content ?? "";
  if (system.startsWith("You classify the latest message")) {
    const latest = user.split("Employee's latest message:").at(-1);
    return REQUEST.test(latest) ? "REQUEST" : "QUESTION";
  }
  if (system.startsWith("You triage IT")) {
    const [, triage] = TRIAGE.find(([pattern]) => pattern.test(user)) ?? [null, { title: user.slice(0, 60), category: "other", details: {} }];
    return JSON.stringify({ priority: "normal", summary: `The employee asks: ${user.slice(0, 120)}`, needs_approval: true, ...triage });
  }
  return "I could not find that in the company documents, so I have noted the question for the team.";
}

http
  .createServer(async (req, res) => {
    const url = new URL(req.url, "http://models");
    if (url.pathname === "/v1/models") return send(res, 200, { object: "list", data: [{ id: "e2e-llm" }, { id: "e2e-embeddings" }] });
    const body = await read(req);
    if (url.pathname === "/v1/embeddings") {
      const inputs = Array.isArray(body.input) ? body.input : [body.input];
      return send(res, 200, {
        object: "list",
        model: body.model,
        data: inputs.map((text, index) => ({ object: "embedding", index, embedding: embed(String(text)) })),
        usage: { prompt_tokens: 0, total_tokens: 0 },
      });
    }
    if (url.pathname === "/v1/chat/completions") {
      const content = reply(body.messages ?? []);
      const base = { id: "chatcmpl-e2e", created: Math.floor(Date.now() / 1000), model: body.model };
      if (!body.stream) {
        return send(res, 200, {
          ...base,
          object: "chat.completion",
          choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
          usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
        });
      }
      res.writeHead(200, { "content-type": "text/event-stream" });
      for (const piece of content.match(/.{1,24}/g) ?? []) {
        res.write(`data: ${JSON.stringify({ ...base, object: "chat.completion.chunk", choices: [{ index: 0, delta: { content: piece }, finish_reason: null }] })}\n\n`);
      }
      res.write(`data: ${JSON.stringify({ ...base, object: "chat.completion.chunk", choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\n`);
      return res.end("data: [DONE]\n\n");
    }
    send(res, 404, { error: { message: `no fake for ${url.pathname}` } });
  })
  .listen(8000, () => console.log("fake model server on 8000"));

// ------------------------------------------------------------------ n8n --------------------

let calls = [];
let slack = false;

async function rag(method, path, body) {
  const response = await fetch(`${RAG_API_URL}${path}`, {
    method,
    headers: { "content-type": "application/json", authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify(body),
  });
  if (!response.ok) console.log(`${method} ${path} answered ${response.status}: ${await response.text()}`);
}

http
  .createServer(async (req, res) => {
    const url = new URL(req.url, "http://n8n");
    if (url.pathname === "/_calls") return send(res, 200, calls);
    const body = await read(req);
    if (url.pathname === "/_reset") {
      calls = [];
      slack = Boolean(body.slack);
      return send(res, 200, { slack });
    }
    const token = req.headers.authorization === `Bearer ${TOKEN}`;
    calls.push({ path: url.pathname, token, body });
    if (!token) return send(res, 200, { ignored: "not from the RAG API" }); // like WF4's token check
    const ticket = body.ticket;
    if (url.pathname === "/webhook/request-intake" && slack) {
      // WF4 posts the approval card and keeps its reference on the ticket
      const ts = `${Math.floor(Date.now() / 1000)}.000100`;
      await rag("PATCH", `/v1/tickets/${ticket.ticket_ref}`, {
        payload: { slack: { channel: "C0APPROVALS", ts, permalink: `https://example-corp.slack.com/archives/C0APPROVALS/p${ts.replace(".", "")}` } },
      });
    }
    if (url.pathname === "/webhook/ticket-decided") {
      // WF4: the card shows the outcome, then an approval is fulfilled
      if (ticket.payload?.slack) {
        calls.push({ path: "slack:chat.update", token: true, body: { ticket_ref: ticket.ticket_ref, text: `${body.decision} by ${body.actor} in the admin portal` } });
      }
      if (body.decision === "approved") {
        await rag("PATCH", `/v1/tickets/${ticket.ticket_ref}`, { status: "fulfilled", actor: "n8n", note: "fulfilled by the workflow" });
      }
    }
    send(res, 200, { received: true });
  })
  .listen(5678, () => console.log("fake n8n on 5678"));
