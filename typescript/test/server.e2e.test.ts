/** End-to-end through the real MCP protocol, using the SDK's in-memory transport pair. */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { describe, expect, it } from "vitest";

import { buildServer } from "../src/server.js";
import { FixedClock } from "../src/spec.js";
import { Workspace } from "../src/workspace.js";

async function connect() {
  const server = buildServer(new Workspace(new FixedClock("2026-03-02T09:00:00Z")));
  const client = new Client({ name: "unit-test", version: "0.0.0" });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await Promise.all([server.connect(a), client.connect(b)]);
  return client;
}

type Structured = Record<string, unknown>;
const textOf = (r: Record<string, unknown>): string => (r["content"] as { text: string }[])[0]!.text;

describe("MCP server end to end", () => {
  it("lists the spec surface", async () => {
    const client = await connect();
    const tools = await client.listTools();
    expect(tools.tools.map((t) => t.name).sort()).toEqual([
      "approve_action",
      "assess",
      "get_signal",
      "list_actions",
      "list_audit",
      "propose_action",
      "reject_action",
      "search_signals",
    ]);
    const prompt = await client.getPrompt({ name: "triage", arguments: { site: "DC-TAMSK" } });
    expect(JSON.stringify(prompt.messages[0])).toContain("site DC-TAMSK");
  });

  it("runs propose, deny, approve and verifies the chain", async () => {
    const client = await connect();
    const asm = (await client.callTool({ name: "assess", arguments: { signal_ids: ["SIG-001", "SIG-002", "SIG-003"] } }))
      .structuredContent as Structured;
    const act = (
      await client.callTool({
        name: "propose_action",
        arguments: { assessment_id: asm["assessment_id"], action: { type: "quarantine_stock", target: "bay K3" }, proposed_by: "agent:e2e" },
      })
    ).structuredContent as Structured;
    expect(act["status"]).toBe("pending_approval");

    const denied = await client.callTool({
      name: "approve_action",
      arguments: { action_id: act["action_id"], reviewer: "agent:e2e", reason: "x".repeat(30) },
    });
    expect(denied.isError).toBe(true);
    expect(textOf(denied)).toMatch(/^POLICY_DENIED:/);

    const ok = await client.callTool({
      name: "approve_action",
      arguments: { action_id: act["action_id"], reviewer: "lee", reason: "Excursion confirmed by two sources." },
    });
    expect((ok.structuredContent as Structured)["status"]).toBe("approved");

    const verify = await client.readResource({ uri: "audit://verify" });
    const body = JSON.parse((verify.contents[0] as { text: string }).text) as Structured;
    expect(body).toMatchObject({ valid: true, length: 5 });
  });

  it("rejects ill-typed input before it reaches the workspace", async () => {
    const client = await connect();
    const bad = await client.callTool({ name: "search_signals", arguments: { limit: "5" } });
    expect(bad.isError).toBe(true);
    expect(textOf(bad)).toContain("Input validation error");
  });
});
