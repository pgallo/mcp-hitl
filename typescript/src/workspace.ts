/**
 * Evidence workspace state and transitions, independent of MCP. Mirrors
 * python/src/hitl_server/workspace.py, including the rule that each write operation
 * reads the clock once and shares that instant between the entity and its audit entries.
 */
import { AuditLog, type AuditEntry } from "./audit.js";
import { Policy, WorkspaceError, type Risk } from "./policy.js";
import { assessSignals, search, type AssessmentCore, type SearchFilters, type SignalHit } from "./scoring.js";
import { loadPolicy, loadSignals, type Clock, type PolicyDoc, type Signal } from "./spec.js";

export type ActionStatus = "pending_approval" | "approved" | "rejected";

export interface Assessment extends AssessmentCore {
  assessment_id: string;
  signal_ids: string[];
  created_at: string;
}

export interface Decision {
  by: string;
  reason: string;
  at: string;
}

export interface Action {
  action_id: string;
  assessment_id: string;
  type: string;
  target: string;
  note: string | null;
  risk: Risk;
  status: ActionStatus;
  confidence: number;
  proposed_by: string;
  created_at: string;
  policy: { auto_approve_eligible: boolean; explanation: string };
  decision: Decision | null;
}

export interface ActionInput {
  type: string;
  target: string;
  note?: string | null | undefined;
}

const pad4 = (n: number): string => String(n).padStart(4, "0");

export class Workspace {
  readonly signals: Signal[];
  readonly policy: Policy;
  readonly audit: AuditLog;
  private readonly byId: Map<string, Signal>;
  private readonly assessments = new Map<string, Assessment>();
  private readonly actions = new Map<string, Action>();

  constructor(
    private readonly clock: Clock,
    opts: { signals?: Signal[]; policy?: PolicyDoc; auditPath?: string } = {},
  ) {
    this.signals = opts.signals ?? loadSignals();
    this.byId = new Map(this.signals.map((s) => [s.id, s]));
    this.policy = new Policy(opts.policy ?? loadPolicy());
    this.audit = new AuditLog(opts.auditPath);
    this.record(this.clock.now(), "workspace.started", "system", "workspace", {
      policy_version: this.policy.version,
      signal_count: this.signals.length,
    });
  }

  private record(at: string, event: string, actor: string, subject: string, data: Record<string, unknown>): AuditEntry {
    return this.audit.append({ at, event, actor, subject, data });
  }

  // --- read side ---------------------------------------------------------

  search(query = "", filters: SearchFilters | null = null, limit = 10): { total: number; results: SignalHit[] } {
    return search(this.signals, query, filters ?? {}, limit);
  }

  getSignal(id: string): Signal {
    const s = this.byId.get(id);
    if (s === undefined) throw new WorkspaceError("NOT_FOUND", `signal ${id} does not exist`);
    return { ...s };
  }

  listActions(status: ActionStatus | null = null): { actions: Action[] } {
    return { actions: [...this.actions.values()].filter((a) => status === null || a.status === status) };
  }

  listAudit(sinceSeq = 0, limit = 100): { length: number; head_hash: string; chain_valid: boolean; entries: AuditEntry[] } {
    return {
      length: this.audit.length,
      head_hash: this.audit.headHash,
      chain_valid: this.audit.verify().valid,
      entries: this.audit.entries(sinceSeq, limit),
    };
  }

  // --- write side --------------------------------------------------------

  assess(signalIds: string[]): Assessment {
    if (signalIds.length === 0) throw new WorkspaceError("INVALID_ARGUMENT", "signal_ids must not be empty");
    if (new Set(signalIds).size !== signalIds.length) {
      throw new WorkspaceError("INVALID_ARGUMENT", "signal_ids contains duplicates");
    }
    const missing = signalIds.filter((id) => !this.byId.has(id));
    if (missing.length > 0) throw new WorkspaceError("NOT_FOUND", `unknown signal ids: ${missing.join(", ")}`);
    const at = this.clock.now();
    const core = assessSignals(signalIds.map((id) => this.byId.get(id)!));
    const assessmentId = `ASM-${pad4(this.assessments.size + 1)}`;
    const assessment: Assessment = {
      assessment_id: assessmentId,
      signal_ids: [...signalIds],
      ...core,
      created_at: at,
    };
    this.record(at, "assessment.created", "workspace", assessmentId, {
      signal_ids: [...signalIds],
      score: core.score,
      confidence: core.confidence,
      band: core.band,
    });
    this.assessments.set(assessmentId, assessment);
    return assessment;
  }

  proposeAction(assessmentId: string, action: ActionInput, proposedBy = "agent:unknown"): Action {
    const assessment = this.assessments.get(assessmentId);
    if (assessment === undefined) throw new WorkspaceError("NOT_FOUND", `assessment ${assessmentId} does not exist`);
    const target = action.target.trim();
    if (target === "") throw new WorkspaceError("INVALID_ARGUMENT", "action target must not be blank");
    const proposer = proposedBy.trim() || "agent:unknown";
    const confidence = assessment.confidence;
    const at = this.clock.now();
    const verdict = this.policy.evaluateProposal(action.type, confidence);
    if (!verdict.allowed) {
      this.record(at, "action.denied", proposer, assessmentId, {
        type: action.type,
        risk: verdict.risk,
        explanation: verdict.explanation,
      });
      throw new WorkspaceError("POLICY_DENIED", verdict.explanation);
    }
    const actionId = `ACT-${pad4(this.actions.size + 1)}`;
    const record: Action = {
      action_id: actionId,
      assessment_id: assessmentId,
      type: action.type,
      target,
      note: action.note ?? null,
      risk: verdict.risk,
      status: "pending_approval",
      confidence,
      proposed_by: proposer,
      created_at: at,
      policy: { auto_approve_eligible: verdict.autoApprove, explanation: verdict.explanation },
      decision: null,
    };
    this.record(at, "action.proposed", proposer, actionId, {
      assessment_id: assessmentId,
      type: action.type,
      target,
      risk: verdict.risk,
      confidence,
    });
    if (verdict.autoApprove) {
      this.record(at, "action.auto_approved", "policy:auto", actionId, { reason: verdict.explanation });
      record.status = "approved";
      record.decision = { by: "policy:auto", reason: verdict.explanation, at };
    }
    this.actions.set(actionId, record);
    return record;
  }

  private decide(actionId: string, reviewerRaw: string, reasonRaw: string, approving: boolean): Action {
    const record = this.actions.get(actionId);
    if (record === undefined) throw new WorkspaceError("NOT_FOUND", `action ${actionId} does not exist`);
    const reviewer = reviewerRaw.trim();
    const reason = reasonRaw.trim();
    if (reviewer === "") throw new WorkspaceError("INVALID_ARGUMENT", "reviewer must not be blank");
    if (reason === "") throw new WorkspaceError("INVALID_ARGUMENT", "reason must not be blank");
    if (record.status !== "pending_approval") {
      throw new WorkspaceError("CONFLICT", `action ${actionId} is already ${record.status}`);
    }
    const at = this.clock.now();
    try {
      this.policy.checkDecision({ risk: record.risk, reviewer, reason, proposedBy: record.proposed_by, approving });
    } catch (err) {
      if (err instanceof WorkspaceError) {
        this.record(at, "decision.denied", reviewer, actionId, {
          attempted: approving ? "approve" : "reject",
          explanation: err.detail,
        });
      }
      throw err;
    }
    const status: ActionStatus = approving ? "approved" : "rejected";
    this.record(at, `action.${status}`, reviewer, actionId, { reason, risk: record.risk });
    record.status = status;
    record.decision = { by: reviewer, reason, at };
    return record;
  }

  approve(actionId: string, reviewer: string, reason: string): Action {
    return this.decide(actionId, reviewer, reason, true);
  }

  reject(actionId: string, reviewer: string, reason: string): Action {
    return this.decide(actionId, reviewer, reason, false);
  }
}
