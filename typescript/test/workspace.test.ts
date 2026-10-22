import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { WorkspaceError } from "../src/policy.js";
import { assessSignals, bandFor, tokens } from "../src/scoring.js";
import { FixedClock, loadSignals, specDir } from "../src/spec.js";
import { Workspace, type ActionInput } from "../src/workspace.js";

const START = "2026-03-02T09:00:00Z";
const byId = new Map(loadSignals().map((s) => [s.id, s]));
const group = (...ids: string[]) => ids.map((id) => byId.get(id)!);

describe("scoring", () => {
  it("tokenizes like the Python twin", () => {
    expect(tokens("Reefer R-17: +9C, bay K3")).toEqual(["reefer", "r", "17", "9c", "bay", "k3"]);
  });

  it.each([
    [0, "low"],
    [0.35, "medium"],
    [0.55, "high"],
    [0.75, "critical"],
  ] as const)("band edge %d -> %s", (score, band) => expect(bandFor(score)).toBe(band));

  it("keeps impact and belief separate", () => {
    const rumour = assessSignals(group("SIG-013"));
    const corroborated = assessSignals(group("SIG-010", "SIG-011", "SIG-012"));
    expect(rumour.score).toBeGreaterThan(corroborated.score);
    expect(rumour.confidence).toBeLessThan(corroborated.confidence);
  });

  it("produces the same four-decimal values as Python", () => {
    const a = assessSignals(group("SIG-014", "SIG-015", "SIG-016"));
    expect(a.confidence).toBe(0.8983);
    expect(a.factors.mean_reliability).toBe(79.6667);
  });
});

describe("workspace", () => {
  it("rejects duplicates and unknown ids without auditing", () => {
    const ws = new Workspace(new FixedClock(START));
    expect(() => ws.assess(["SIG-001", "SIG-001"])).toThrow(WorkspaceError);
    expect(() => ws.assess(["SIG-999"])).toThrow(/NOT_FOUND/);
    expect(ws.audit.length).toBe(1);
  });

  it("audits denied proposals without creating an action", () => {
    const ws = new Workspace(new FixedClock(START));
    const asm = ws.assess(["SIG-013"]);
    expect(() => ws.proposeAction(asm.assessment_id, { type: "hold_shipment", target: "apron" }, "agent:t")).toThrow(
      /POLICY_DENIED/,
    );
    expect(ws.listActions().actions).toEqual([]);
    expect(ws.listAudit().entries.at(-1)?.event).toBe("action.denied");
  });

  it("replays the reference scenario to the same head hash as the Python twin", () => {
    const fixture = JSON.parse(readFileSync(path.join(specDir(), "fixtures", "reference_scenario.json"), "utf8")) as {
      clock: string;
      steps: [string, Record<string, unknown>][];
      outcomes: string[];
      audit_length: number;
      head_hash: string;
    };
    const ws = new Workspace(new FixedClock(fixture.clock));
    const outcomes: string[] = [];
    for (const [tool, args] of fixture.steps) {
      try {
        switch (tool) {
          case "assess":
            ws.assess(args["signal_ids"] as string[]);
            break;
          case "propose_action":
            ws.proposeAction(args["assessment_id"] as string, args["action"] as ActionInput, args["proposed_by"] as string);
            break;
          case "approve_action":
            ws.approve(args["action_id"] as string, args["reviewer"] as string, args["reason"] as string);
            break;
          case "reject_action":
            ws.reject(args["action_id"] as string, args["reviewer"] as string, args["reason"] as string);
            break;
          default:
            throw new Error(`unknown step ${tool}`);
        }
        outcomes.push("ok");
      } catch (err) {
        if (!(err instanceof WorkspaceError)) throw err;
        outcomes.push(err.code);
      }
    }
    expect(outcomes).toEqual(fixture.outcomes);
    expect(ws.audit.length).toBe(fixture.audit_length);
    expect(ws.audit.headHash).toBe(fixture.head_hash);
  });
});
