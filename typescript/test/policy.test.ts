import { describe, expect, it } from "vitest";

import { Policy, WorkspaceError, type Risk } from "../src/policy.js";
import { loadPolicy } from "../src/spec.js";

const policy = new Policy(loadPolicy());

describe("approval policy", () => {
  it.each([
    ["notify_team", 0.75, true, true],
    ["notify_team", 0.7499, true, false],
    ["open_ticket", 0, true, false],
    ["expedite_order", 0.39, false, false],
    ["expedite_order", 0.99, true, false],
    ["reroute_shipment", 0.49, false, false],
    ["reroute_shipment", 1, true, false],
  ] as const)("%s at %d: allowed=%s auto=%s", (action, confidence, allowed, auto) => {
    const v = policy.evaluateProposal(action, confidence);
    expect([v.allowed, v.autoApprove]).toEqual([allowed, auto]);
  });

  it("formats explanations exactly like the Python twin", () => {
    expect(policy.evaluateProposal("notify_team", 0.9).explanation).toBe("low risk and confidence 0.9 >= 0.75: auto-approved");
    expect(policy.evaluateProposal("hold_shipment", 0.475).explanation).toBe(
      "high risk requires confidence >= 0.5 to propose, assessment has 0.475",
    );
  });

  it.each([
    ["low", "agent:bot", "fine", "x", true, "automated identity"],
    ["medium", "ana", "too short", "x", true, "at least 10"],
    ["high", "ana", "a".repeat(19), "x", true, "at least 20"],
    ["high", "ana", "a".repeat(20), "ana", true, "proposer cannot approve"],
  ] as const)("denies %s decision by %s", (risk, reviewer, reason, proposedBy, approving, fragment) => {
    let caught: unknown;
    try {
      policy.checkDecision({ risk: risk as Risk, reviewer, reason, proposedBy, approving });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(WorkspaceError);
    expect((caught as WorkspaceError).code).toBe("POLICY_DENIED");
    expect((caught as WorkspaceError).detail).toContain(fragment);
  });

  it("counts code points, not UTF-16 units, for reason length", () => {
    // 10 astral characters are 20 UTF-16 units but only 10 code points, as in Python's len().
    expect(() =>
      policy.checkDecision({ risk: "high", reviewer: "ana", reason: "\u{1F6A2}".repeat(10), proposedBy: "x", approving: true }),
    ).toThrow(/at least 20/);
  });
});
