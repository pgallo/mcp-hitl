/** MCP surface for the workspace. Translation only: no business rules live here. */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import * as z from "zod";

import { inputShapes, outputSchemas } from "./schemas.js";
import { clockFromEnv, loadToolsSpec, triageTemplate, type Clock } from "./spec.js";
import { Workspace } from "./workspace.js";

export const SERVER_NAME = "mcp-hitl";
export const SERVER_VERSION = "0.1.0";

function structured(value: object): CallToolResult {
  return {
    content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
    structuredContent: value as Record<string, unknown>,
  };
}

export function buildServer(workspace?: Workspace, clock?: Clock): McpServer {
  const auditPath = process.env["HITL_AUDIT_PATH"];
  const ws =
    workspace ?? new Workspace(clock ?? clockFromEnv(), auditPath ? { auditPath } : {});
  const spec = loadToolsSpec();
  const describe = new Map(spec.tools.map((t) => [t.name, t.description]));
  const desc = (name: string): string => {
    const d = describe.get(name);
    if (d === undefined) throw new Error(`tool ${name} missing from spec/tools.json`);
    return d;
  };

  const server = new McpServer(
    { name: SERVER_NAME, version: SERVER_VERSION },
    { instructions: "Evidence workspace with human approval for consequential actions." },
  );

  server.registerTool(
    "search_signals",
    { description: desc("search_signals"), inputSchema: inputShapes.search_signals, outputSchema: outputSchemas.search_signals },
    async ({ query, filters, limit }) => structured(ws.search(query ?? "", filters ?? null, limit ?? 10)),
  );

  server.registerTool(
    "get_signal",
    { description: desc("get_signal"), inputSchema: inputShapes.get_signal, outputSchema: outputSchemas.get_signal },
    async ({ id }) => structured(ws.getSignal(id)),
  );

  server.registerTool(
    "assess",
    { description: desc("assess"), inputSchema: inputShapes.assess, outputSchema: outputSchemas.assess },
    async ({ signal_ids }) => structured(ws.assess(signal_ids)),
  );

  server.registerTool(
    "propose_action",
    { description: desc("propose_action"), inputSchema: inputShapes.propose_action, outputSchema: outputSchemas.propose_action },
    async ({ assessment_id, action, proposed_by }) =>
      structured(ws.proposeAction(assessment_id, action, proposed_by ?? "agent:unknown")),
  );

  server.registerTool(
    "approve_action",
    { description: desc("approve_action"), inputSchema: inputShapes.approve_action, outputSchema: outputSchemas.approve_action },
    async ({ action_id, reviewer, reason }) => structured(ws.approve(action_id, reviewer, reason)),
  );

  server.registerTool(
    "reject_action",
    { description: desc("reject_action"), inputSchema: inputShapes.reject_action, outputSchema: outputSchemas.reject_action },
    async ({ action_id, reviewer, reason }) => structured(ws.reject(action_id, reviewer, reason)),
  );

  server.registerTool(
    "list_actions",
    { description: desc("list_actions"), inputSchema: inputShapes.list_actions, outputSchema: outputSchemas.list_actions },
    async ({ status }) => structured(ws.listActions(status ?? null)),
  );

  server.registerTool(
    "list_audit",
    { description: desc("list_audit"), inputSchema: inputShapes.list_audit, outputSchema: outputSchemas.list_audit },
    async ({ since_seq, limit }) => structured(ws.listAudit(since_seq ?? 0, limit ?? 100)),
  );

  const resourceText: Record<string, () => string> = {
    "audit://log": () => JSON.stringify(ws.audit.entries(), null, 2),
    "audit://verify": () => JSON.stringify(ws.audit.verify(), null, 2),
    "policy://current": () => JSON.stringify(ws.policy.doc, null, 2),
  };
  for (const r of spec.resources) {
    const read = resourceText[r.uri];
    if (read === undefined) throw new Error(`no handler for resource ${r.uri}`);
    server.registerResource(r.name, r.uri, { description: r.description, mimeType: r.mimeType }, async (uri) => ({
      contents: [{ uri: uri.href, mimeType: r.mimeType, text: read() }],
    }));
  }

  const promptSpec = spec.prompts[0]!;
  server.registerPrompt(
    "triage",
    { description: promptSpec.description, argsSchema: { site: z.string().describe(promptSpec.arguments[0]!.description).optional() } },
    ({ site }) => ({
      messages: [
        {
          role: "user",
          content: { type: "text", text: triageTemplate().replace("{{scope}}", site ? `site ${site}` : "all sites") },
        },
      ],
    }),
  );

  return server;
}
