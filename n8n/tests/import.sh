#!/bin/sh
# Runs once in the n8n image before n8n starts (compose.yaml): imports the chart's workflows into
# the shared n8n folder and publishes them, as the chart's init container does. Schedule triggers
# (WF6, WF7) become webhooks named run-<file> so a test can start them.
set -eu
mkdir -p /tmp/workflows
node -e '
const fs = require("fs");
for (const file of fs.readdirSync("/workflows").filter((f) => f.endsWith(".json"))) {
  const wf = JSON.parse(fs.readFileSync(`/workflows/${file}`, "utf8"));
  const hook = `run-${file.split("-")[0]}`;
  for (const node of wf.nodes) {
    if (node.type !== "n8n-nodes-base.scheduleTrigger") continue;
    Object.assign(node, { type: "n8n-nodes-base.webhook", typeVersion: 2, webhookId: hook,
      parameters: { httpMethod: "POST", path: hook, responseMode: "onReceived", options: {} } });
  }
  fs.writeFileSync(`/tmp/workflows/${file}`, JSON.stringify(wf));
}
fs.writeFileSync("/tmp/credentials.json", JSON.stringify([
  { id: "AssistantSlack01", name: "Slack account", type: "slackApi", data: { accessToken: "not-configured" } },
]));
'
n8n import:credentials --input=/tmp/credentials.json
n8n import:workflow --separate --input=/tmp/workflows
for f in /tmp/workflows/*.json; do
  n8n publish:workflow --id="$(node -e "console.log(require('$f').id)")"
done
