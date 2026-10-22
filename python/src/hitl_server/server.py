"""MCP surface for the workspace: tools, resources and the triage prompt.

This layer only translates between MCP and the Workspace. It holds no business rules,
which keeps the twin implementations comparable at the level that matters.

Deliberately no `from __future__ import annotations`: the SDK evaluates the prompt's
annotations at registration time, and one of them refers to a local variable.
"""

import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any

import jsonschema
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ContentBlock
from pydantic import Field

from . import models
from .policy import WorkspaceError
from .spec import Clock, clock_from_env, load_tools_spec, triage_template
from .workspace import Workspace

SERVER_NAME = "mcp-hitl"
SERVER_VERSION = "0.1.0"


class ContractServer(FastMCP):
    """The SDK's high-level server with two adjustments that keep it aligned with the TypeScript twin.

    1. Arguments are validated against the advertised JSON Schema before dispatch.
       The SDK otherwise coerces through its model layer in lax mode ("5" becomes 5), while
       the TypeScript side rejects. A contract both sides enforce strictly is the only one worth testing.
    2. Domain errors surface as "CODE: detail", without the SDK's "Error executing tool"
       wrapper, so clients can branch on the code regardless of implementation.
    """

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Sequence[ContentBlock] | dict[str, Any]:
        tool = self._tool_manager.get_tool(name)
        if tool is not None:
            try:
                jsonschema.validate(instance=arguments, schema=tool.parameters)
            except jsonschema.ValidationError as err:
                where = "/".join(str(p) for p in err.absolute_path) or "arguments"
                raise ToolError(f"Input validation error: {err.message} at {where}") from err
        try:
            return await super().call_tool(name, arguments)
        except ToolError as err:
            if isinstance(err.__cause__, WorkspaceError):
                raise err.__cause__ from None
            raise


def build_server(workspace: Workspace | None = None, clock: Clock | None = None) -> ContractServer:
    if workspace is None:
        audit_path = os.environ.get("HITL_AUDIT_PATH")
        workspace = Workspace(clock or clock_from_env(), audit_path=Path(audit_path) if audit_path else None)
    ws = workspace
    spec = load_tools_spec()
    describe = {t["name"]: t["description"] for t in spec["tools"]}
    resources = {r["uri"]: r for r in spec["resources"]}

    mcp = ContractServer(
        SERVER_NAME,
        instructions="Evidence workspace with human approval for consequential actions.",
        log_level="WARNING",
    )
    # The high-level constructor takes no version; set it on the low-level server so both twins report the same.
    mcp._mcp_server.version = SERVER_VERSION

    @mcp.tool(description=describe["search_signals"])
    def search_signals(
        query: str = "",
        filters: models.SearchFilters | None = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> models.SearchResult:
        return models.SearchResult.model_validate(ws.search(query, filters.model_dump() if filters else None, limit))

    @mcp.tool(description=describe["get_signal"])
    def get_signal(id: str) -> models.Signal:  # noqa: A002 - name fixed by the shared spec
        return models.Signal.model_validate(ws.get_signal(id))

    @mcp.tool(description=describe["assess"])
    def assess(signal_ids: Annotated[list[str], Field(min_length=1, max_length=10)]) -> models.Assessment:
        return models.Assessment.model_validate(ws.assess(signal_ids))

    @mcp.tool(description=describe["propose_action"])
    def propose_action(
        assessment_id: str,
        action: models.ActionInput,
        proposed_by: Annotated[str, Field(min_length=1)] = "agent:unknown",
    ) -> models.Action:
        return models.Action.model_validate(ws.propose_action(assessment_id, action.model_dump(), proposed_by))

    @mcp.tool(description=describe["approve_action"])
    def approve_action(
        action_id: str,
        reviewer: Annotated[str, Field(min_length=1)],
        reason: Annotated[str, Field(min_length=1)],
    ) -> models.Action:
        return models.Action.model_validate(ws.approve(action_id, reviewer, reason))

    @mcp.tool(description=describe["reject_action"])
    def reject_action(
        action_id: str,
        reviewer: Annotated[str, Field(min_length=1)],
        reason: Annotated[str, Field(min_length=1)],
    ) -> models.Action:
        return models.Action.model_validate(ws.reject(action_id, reviewer, reason))

    @mcp.tool(description=describe["list_actions"])
    def list_actions(status: models.ActionStatus | None = None) -> models.ActionList:
        return models.ActionList.model_validate(ws.list_actions(status))

    @mcp.tool(description=describe["list_audit"])
    def list_audit(
        since_seq: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> models.AuditPage:
        return models.AuditPage.model_validate(ws.list_audit(since_seq, limit))

    def _resource(uri: str) -> Callable[[Callable[[], str]], Callable[[], str]]:
        meta = resources[uri]
        return mcp.resource(uri, name=meta["name"], description=meta["description"], mime_type=meta["mimeType"])

    @_resource("audit://log")
    def audit_log() -> str:
        return json.dumps(ws.audit.entries(), indent=2, ensure_ascii=False)

    @_resource("audit://verify")
    def audit_verify() -> str:
        return json.dumps(ws.audit.verify().to_dict(), indent=2)

    @_resource("policy://current")
    def policy_current() -> str:
        return json.dumps(ws.policy.doc, indent=2)

    prompt_spec = spec["prompts"][0]

    site_doc = prompt_spec["arguments"][0]["description"]

    @mcp.prompt(name="triage", description=prompt_spec["description"])
    def triage(site: Annotated[str | None, Field(description=site_doc)] = None) -> str:
        scope = f"site {site}" if site else "all sites"
        return triage_template().replace("{{scope}}", scope)

    return mcp
