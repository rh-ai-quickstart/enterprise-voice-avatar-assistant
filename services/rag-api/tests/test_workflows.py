"""Static checks of the n8n workflows the chart ships (chart/files/n8n-workflows)."""

import json
import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parents[3] / "chart" / "files" / "n8n-workflows").glob("*.json"))
TRIGGERS = {"n8n-nodes-base.webhook", "n8n-nodes-base.scheduleTrigger"}


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def talks_to_slack(node: dict) -> bool:
    if node["type"] == "n8n-nodes-base.slack":
        return True
    url = node.get("parameters", {}).get("url", "") if node["type"] == "n8n-nodes-base.httpRequest" else ""
    return "slack.com" in url or "response_url" in url


def is_slack_guard(node: dict) -> bool:
    conditions = node.get("parameters", {}).get("conditions", {}).get("conditions", [])
    return node["type"] == "n8n-nodes-base.if" and any(
        "$env.SLACK_ENABLED" in c.get("leftValue", "") for c in conditions
    )


def test_workflows_are_found():
    assert len(WORKFLOWS) == 7


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_slack_is_only_reached_through_a_slack_guard(path):
    """Every path from a trigger to a node that talks to Slack passes the true output of an IF on
    $env.SLACK_ENABLED, so a deployment without Slack never calls it."""
    wf = load(path)
    nodes = {n["name"]: n for n in wf["nodes"]}
    seen: set[tuple[str, bool]] = set()
    stack = [(n["name"], False) for n in wf["nodes"] if n["type"] in TRIGGERS]
    while stack:
        name, allowed = stack.pop()
        if (name, allowed) in seen:
            continue
        seen.add((name, allowed))
        node = nodes[name]
        assert allowed or not talks_to_slack(node), f"{path.name}: {name!r} is reachable with Slack off"
        for output, branch in enumerate(wf["connections"].get(name, {}).get("main", [])):
            for connection in branch or []:
                opened = allowed or (is_slack_guard(node) and output == 0)
                stack.append((connection["node"], opened))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_calls_to_the_rag_api_and_ingestion_send_the_internal_token(path):
    for node in load(path)["nodes"]:
        params = node.get("parameters", {})
        url = params.get("url", "")
        if node["type"] != "n8n-nodes-base.httpRequest" or not (
            "$env.RAG_API_URL" in url or "$env.INGESTION_URL" in url
        ):
            continue
        headers = {h["name"]: h["value"] for h in params.get("headerParameters", {}).get("parameters", [])}
        assert params.get("sendHeaders") is True, f"{path.name}: {node['name']!r} sends no headers"
        assert headers.get("Authorization") == "=Bearer {{ $env.INTERNAL_API_TOKEN }}", node["name"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_connections_name_existing_nodes(path):
    wf = load(path)
    names = [n["name"] for n in wf["nodes"]]
    assert len(names) == len(set(names))
    assert len({n["id"] for n in wf["nodes"]}) == len(names)
    for source, outputs in wf["connections"].items():
        assert source in names
        for branch in outputs.get("main", []):
            for connection in branch or []:
                assert connection["node"] in names, f"{path.name}: {source} -> {connection['node']}"


def _webhooks() -> dict[str, tuple[dict, dict]]:
    """Webhook path -> (workflow, webhook node) across all the workflows."""
    found = {}
    for path in WORKFLOWS:
        wf = load(path)
        for node in wf["nodes"]:
            if node["type"] == "n8n-nodes-base.webhook":
                found[node["parameters"]["path"]] = (wf, node)
    return found


def _next_nodes(wf: dict, name: str) -> list[dict]:
    nodes = {n["name"]: n for n in wf["nodes"]}
    outputs = wf["connections"].get(name, {}).get("main", [])
    return [nodes[c["node"]] for c in (outputs[0] if outputs else [])]


def test_the_webhooks_the_rag_api_calls_exist():
    from app.config import settings

    webhooks = _webhooks()
    for path in (
        settings.n8n_request_webhook_path,
        settings.n8n_archive_webhook_path,
        settings.n8n_decision_webhook_path,
    ):
        assert path.removeprefix("/webhook/") in webhooks, path


@pytest.mark.parametrize("path", ["request-intake", "archive-transcript", "ticket-decided", "classify"])
def test_the_webhooks_the_rag_api_calls_accept_only_the_internal_token(path):
    wf, hook = _webhooks()[path]
    (check,) = _next_nodes(wf, hook["name"])
    (condition,) = check["parameters"]["conditions"]["conditions"]
    assert check["type"] == "n8n-nodes-base.if"
    # One condition that is false while the token is empty (an empty header must not match it)
    assert condition["leftValue"].startswith("={{ Boolean($env.INTERNAL_API_TOKEN) && ")
    assert "$json.headers.authorization" in condition["leftValue"]
    assert condition["operator"] == {"type": "boolean", "operation": "true", "singleValue": True}
    # Nothing runs on the false branch
    assert wf["connections"][check["name"]]["main"][1] == []


def test_object_notifications_need_the_token_in_the_url():
    """The object store cannot send headers: its webhook URL carries the token, and WF2 checks it
    before anything else."""
    wf, hook = _webhooks()["object-created"]
    (parse,) = _next_nodes(wf, hook["name"])
    code = parse["parameters"]["jsCode"]
    assert "if (!token || String(($input.first().json.query || {}).token || '') !== token) return [];" in code
    assert code.index("return [];") < code.index("body.Records")
    classify = next(n for n in wf["nodes"] if n["name"] == "Classify via WF3")
    headers = {h["name"]: h["value"] for h in classify["parameters"]["headerParameters"]["parameters"]}
    assert headers["Authorization"] == "=Bearer {{ $env.INTERNAL_API_TOKEN }}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_slack_text_escapes_what_users_and_the_model_wrote(path):
    """Titles, questions, names, file names and the model's output are escaped in every Slack
    message, so <https://evil|text> cannot become a link or <!channel> a mention."""
    wf = load(path)
    for node in wf["nodes"]:
        params = node.get("parameters", {})
        text = params.get("jsCode", "") + params.get("text", "")
        for field in (
            "t.title",
            ".json.title",
            "g.question",
            "t.requester",
            ".json.requester",
            "$json.key",
            "r.summary",
        ):
            for use in re.findall(
                r"\$\{[^}]*" + re.escape(field) + r"[^}]*\}|\{\{[^}]*" + re.escape(field) + r"[^}]*\}\}", text
            ):
                if "ticket_ref" in use or "JSON.stringify" in use or "title:" in use:
                    continue
                assert "esc(" in use or "&amp;" in use, (
                    f"{path.name}: {node['name']!r} puts {use!r} in Slack unescaped"
                )


def test_slack_clicks_are_verified_before_anything_else():
    wf, hook = _webhooks()["slack-interactions"]
    assert hook["parameters"]["options"].get("rawBody") is True
    (verify,) = _next_nodes(wf, hook["name"])
    code = verify["parameters"]["jsCode"]
    assert "SLACK_SIGNING_SECRET" in code and "createHmac('sha256'" in code and "timingSafeEqual" in code
    (valid,) = _next_nodes(wf, verify["name"])
    assert valid["type"] == "n8n-nodes-base.if" and "signature_ok" in str(valid["parameters"])


def _event_posts(wf: dict) -> list[dict]:
    return [
        n
        for n in wf["nodes"]
        if n["type"] == "n8n-nodes-base.httpRequest"
        and n["parameters"].get("url", "").endswith("/v1/internal/events")
    ]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_every_slack_failure_reaches_the_activity_feed(path):
    """A node that talks to Slack continues on failure, and its output goes through
    'Slack failed? (<node>)' to an integration.error event for Slack."""
    wf = load(path)
    for node in filter(talks_to_slack, wf["nodes"]):
        assert node.get("continueOnFail") is True, f"{path.name}: {node['name']!r} stops the workflow"
        checks = [n for n in _next_nodes(wf, node["name"]) if n["name"] == f"Slack failed? ({node['name']})"]
        assert checks, f"{path.name}: {node['name']!r} failures are not reported"
        condition = checks[0]["parameters"]["conditions"]["conditions"][0]
        assert "$json.error" in condition["leftValue"] and "$json.ok === false" in condition["leftValue"]
        (report,) = _next_nodes(wf, checks[0]["name"])
        body = report["parameters"]["jsonBody"]
        assert report in _event_posts(wf)
        assert "kind: 'integration.error'" in body and "ref_type: 'integration'" in body
        assert "ref_id: 'slack'" in body


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_events_the_workflows_post_are_valid(path):
    """Kinds and reference types the RAG API accepts; a failed post never stops the workflow."""
    import re
    import typing

    from app.schemas import ActivityEventIn

    pattern = re.compile(ActivityEventIn.model_fields["kind"].metadata[1].pattern)
    ref_types = typing.get_args(typing.get_args(ActivityEventIn.model_fields["ref_type"].annotation)[0])
    for node in _event_posts(load(path)):
        body = node["parameters"]["jsonBody"]
        assert node.get("continueOnFail") is True, node["name"]
        assert body.startswith("={{ JSON.stringify({") and body.endswith("}) }}"), node["name"]
        kinds = re.findall(r"'([a-z_]+\.[a-z_.]+)'", body.split("title:")[0])
        assert kinds and all(pattern.match(k) for k in kinds), (node["name"], kinds)
        (ref_type,) = re.findall(r"ref_type: '([a-z]+)'", body)
        assert ref_type in ref_types, (node["name"], ref_type)


@pytest.mark.parametrize(
    "stem, kinds",
    [
        ("wf2-document-ingestion", {"document.ingested", "document.ingest_failed"}),
        ("wf6-sla-escalation", {"ticket.sla_reminder"}),
        ("wf7-knowledge-gap-digest", {"gaps.digest"}),
    ],
)
def test_workflow_results_are_in_the_feed(stem, kinds):
    (path,) = [p for p in WORKFLOWS if p.stem == stem]
    posted = " ".join(n["parameters"]["jsonBody"] for n in _event_posts(load(path)))
    assert all(f"'{k}'" in posted for k in kinds)
