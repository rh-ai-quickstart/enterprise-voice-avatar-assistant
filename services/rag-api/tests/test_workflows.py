"""Static checks of the n8n workflows the chart ships (chart/files/n8n-workflows)."""

import json
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


def test_portal_decisions_are_accepted_only_with_the_internal_token():
    wf, hook = _webhooks()["ticket-decided"]
    (check,) = _next_nodes(wf, hook["name"])
    condition = check["parameters"]["conditions"]["conditions"][0]
    assert check["type"] == "n8n-nodes-base.if"
    assert "authorization" in condition["leftValue"] and "$env.INTERNAL_API_TOKEN" in condition["rightValue"]


def test_slack_clicks_are_verified_before_anything_else():
    wf, hook = _webhooks()["slack-interactions"]
    assert hook["parameters"]["options"].get("rawBody") is True
    (verify,) = _next_nodes(wf, hook["name"])
    code = verify["parameters"]["jsCode"]
    assert "SLACK_SIGNING_SECRET" in code and "createHmac('sha256'" in code and "timingSafeEqual" in code
    (valid,) = _next_nodes(wf, verify["name"])
    assert valid["type"] == "n8n-nodes-base.if" and "signature_ok" in str(valid["parameters"])
