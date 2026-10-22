/** Approval policy as code. Rules live in spec/policy.json; this module interprets them. */
import { fmtNum } from "./canonical.js";
import type { PolicyDoc } from "./spec.js";

export type Risk = "low" | "medium" | "high";
export type ErrorCode = "NOT_FOUND" | "INVALID_ARGUMENT" | "CONFLICT" | "POLICY_DENIED";

const AUTOMATED_PREFIXES = ["agent:", "policy:"];

/** Domain error with a code shared with the Python twin. The message format is "CODE: detail". */
export class WorkspaceError extends Error {
  constructor(
    readonly code: ErrorCode,
    readonly detail: string,
  ) {
    super(`${code}: ${detail}`);
    this.name = "WorkspaceError";
  }
}

export interface ProposalVerdict {
  risk: Risk;
  allowed: boolean;
  autoApprove: boolean;
  explanation: string;
}

export class Policy {
  readonly version: string;

  constructor(readonly doc: PolicyDoc) {
    this.version = doc.version;
  }

  riskOf(actionType: string): Risk {
    const entry = this.doc.action_types[actionType];
    if (entry === undefined) throw new WorkspaceError("INVALID_ARGUMENT", `unknown action type ${actionType}`);
    return entry.risk;
  }

  evaluateProposal(actionType: string, confidence: number): ProposalVerdict {
    const risk = this.riskOf(actionType);
    const tier = this.doc.risk_tiers[risk];
    const c = fmtNum(confidence);
    const floor = tier.min_confidence_to_propose;
    if (confidence < floor) {
      return {
        risk,
        allowed: false,
        autoApprove: false,
        explanation: `${risk} risk requires confidence >= ${fmtNum(floor)} to propose, assessment has ${c}`,
      };
    }
    const threshold = tier.auto_approve_min_confidence;
    if (threshold === null) {
      return { risk, allowed: true, autoApprove: false, explanation: `${risk} risk: human approval always required` };
    }
    if (confidence >= threshold) {
      return {
        risk,
        allowed: true,
        autoApprove: true,
        explanation: `${risk} risk and confidence ${c} >= ${fmtNum(threshold)}: auto-approved`,
      };
    }
    return {
      risk,
      allowed: true,
      autoApprove: false,
      explanation: `${risk} risk but confidence ${c} < ${fmtNum(threshold)}: human approval required`,
    };
  }

  checkDecision(args: { risk: Risk; reviewer: string; reason: string; proposedBy: string; approving: boolean }): void {
    const { risk, reviewer, reason, proposedBy, approving } = args;
    if (AUTOMATED_PREFIXES.some((p) => reviewer.startsWith(p))) {
      throw new WorkspaceError(
        "POLICY_DENIED",
        `reviewer ${reviewer} is an automated identity; decisions need a named human`,
      );
    }
    if (!approving) return;
    const tier = this.doc.risk_tiers[risk];
    // Python len() counts code points; spread does the same for strings.
    if ([...reason].length < tier.min_reason_length) {
      throw new WorkspaceError(
        "POLICY_DENIED",
        `${risk} risk approval needs a reason of at least ${tier.min_reason_length} characters`,
      );
    }
    if (tier.separation_of_duties && reviewer === proposedBy) {
      throw new WorkspaceError("POLICY_DENIED", `${risk} risk: the proposer cannot approve their own action`);
    }
  }
}
