#!/usr/bin/env python3
"""Generates the values reference table for chart/README.md from chart/values.yaml.

Every scalar or list value becomes a row: dotted key, default, and the comment written
directly above it (or above its parent). Run after editing values.yaml:
    python3 scripts/gen-values-reference.py > /tmp/values.md   (or --write to update chart/README.md in place)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALUES = ROOT / "chart" / "values.yaml"
README = ROOT / "chart" / "README.md"
BEGIN, END = "<!-- values-reference:begin -->", "<!-- values-reference:end -->"


def parse(lines):
    rows, comment, stack, pending_list = [], [], [], None
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip():
            comment = []
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if stripped.startswith("#"):
            text = stripped.lstrip("#").strip()
            if set(text) <= {"-"}:  # separator lines
                continue
            comment.append(text)
            continue
        if stripped.startswith("- "):
            if pending_list is not None:
                pending_list["default"].append(stripped[2:].strip())
            continue
        m = re.match(r"([A-Za-z0-9_./-]+):(?:\s*(.*))?$", stripped)
        if not m:
            continue
        key, value = m.group(1), (m.group(2) or "").strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([s[1] for s in stack] + [key])
        desc = " ".join(comment)
        comment = []
        if value == "":
            # mapping or list follows
            stack.append((indent, key))
            pending_list = {"key": path, "default": [], "desc": desc}
            rows.append(pending_list)
        else:
            pending_list = None
            rows.append({"key": path, "default": [value], "desc": desc})
    # drop pure mapping rows (a row with an empty list whose children exist)
    keys = [r["key"] for r in rows]
    out = []
    for r in rows:
        is_parent = any(k.startswith(r["key"] + ".") for k in keys)
        if is_parent:
            continue
        out.append(r)
    return out, {r["key"]: r["desc"] for r in rows}


GENERIC = {
    "enabled": "Deploy this component.",
    "image": "Container image (full reference).",
    "replicas": "Number of pods.",
    "storage": "Size of the persistent volume.",
    "route": "Expose through an OpenShift Route.",
    "publicHost": "Public hostname; computed from `global.domain` when empty.",
    "resources.requests.cpu": "CPU request.",
    "resources.requests.memory": "Memory request.",
    "resources.limits.cpu": "CPU limit.",
    "resources.limits.memory": "Memory limit.",
    "resources.requests.nvidia.com/gpu": "GPUs requested (per model pod).",
    "resources.limits.nvidia.com/gpu": "GPU limit (per model pod).",
    "deploy": "true: serve the model on OpenShift AI from this chart; false: use `endpoint`.",
    "name": "InferenceService name (also the in-cluster service name).",
    "displayName": "Name shown in the OpenShift AI dashboard.",
    "servedModelName": "Model name clients send in requests.",
    "storageUri": "Where the weights come from (oci:// modelcar or hf:// repository).",
    "args": "Extra vLLM arguments.",
    "endpoint": "OpenAI-compatible base URL including /v1, used when `deploy` is false.",
    "collection": "Qdrant collection for document chunks.",
    "topK": "Number of chunks retrieved per question.",
    "timezone": "Time zone for schedules and logs.",
    "voice": "Kokoro voice (af_*/bf_* female, am_*/bm_* male).",
    "buckets": "Buckets created after install.",
    "eventBuckets": "Buckets whose uploads notify n8n.",
    "logLevel": "Log level.",
    "extraEnv": "Extra environment variables (map of string values).",
}


def generic(key):
    if key.startswith("secrets."):
        return "Name of the pre-created Secret holding the keys listed above."
    parts = key.split(".")
    for n in (4, 3, 2, 1):
        tail = ".".join(parts[-n:])
        if tail in GENERIC:
            return GENERIC[tail]
    return ""


def fmt_default(values):
    if not values:
        return "`{}`"
    if len(values) == 1:
        v = values[0]
        return "`" + (v if v not in ('""', "''") else '""') + "`"
    return "`[" + ", ".join(values) + "]`"


def render(rows, descs):
    sections, order = {}, []
    for r in rows:
        top = r["key"].split(".")[0]
        if top not in sections:
            sections[top] = []
            order.append(top)
        sections[top].append(r)
    parts = []
    for top in order:
        parts.append(f"\n### `{top}`\n")
        if descs.get(top):
            parts.append(descs[top] + "\n")
        parts.append("| Key | Default | Description |\n|---|---|---|")
        for r in sections[top]:
            desc = r["desc"] or generic(r["key"])
            desc = desc.replace("|", "\\|")
            parts.append(f"| `{r['key']}` | {fmt_default(r['default'])} | {desc} |")
    return "\n".join(parts) + "\n"


def main():
    rows, descs = parse(VALUES.read_text().splitlines())
    table = render(rows, descs)
    if "--write" in sys.argv:
        text = README.read_text()
        assert BEGIN in text and END in text, "markers missing in chart/README.md"
        pre, rest = text.split(BEGIN, 1)
        _, post = rest.split(END, 1)
        README.write_text(pre + BEGIN + "\n" + table + END + post)
        print(f"updated {README} with {len(rows)} rows")
    else:
        sys.stdout.write(table)


if __name__ == "__main__":
    main()
