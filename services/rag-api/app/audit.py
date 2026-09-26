"""The audit log: who did what in the admin portal, with the values before and after."""

import json
from datetime import datetime
from typing import Any

from fastapi import Request

from . import auth, memory

COLUMNS = "id, actor, action, target_type, target_id, before, after, client_ip, user_agent, created_at"


def record(
    actor: str,
    action: str,
    request: Request | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    memory.run(
        """INSERT INTO admin_audit (actor, action, target_type, target_id, before, after, client_ip, user_agent)
           VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)""",
        (
            actor,
            action,
            target_type,
            target_id,
            None if before is None else json.dumps(before, default=str),
            None if after is None else json.dumps(after, default=str),
            auth.client_address(request) if request else None,
            (request.headers.get("user-agent") or "")[:300] if request else None,
        ),
    )


def entries(
    actor: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return memory.select_page(
        "admin_audit", COLUMNS, {"actor": actor, "action": action}, since, until, before_id, limit
    )
