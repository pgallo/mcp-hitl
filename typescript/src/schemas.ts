/**
 * Runtime schemas for every tool. Hand-written for readable types, then checked against
 * spec/tools.json by test/schemas.test.ts (and against the Python twin by the
 * conformance suite), so the spec stays the single source of truth.
 */
import * as z from "zod";

const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;

export const SignalKind = z.enum(["delay", "sensor_anomaly", "supplier_notice", "customs_hold", "weather"]);
export const ActionType = z.enum([
  "notify_team",
  "open_ticket",
  "increase_monitoring",
  "expedite_order",
  "reroute_shipment",
  "hold_shipment",
  "quarantine_stock",
]);
export const Risk = z.enum(["low", "medium", "high"]);
export const ActionStatus = z.enum(["pending_approval", "approved", "rejected"]);

export const SearchFilters = z.object({
  kind: z.array(SignalKind).nullable().optional(),
  site: z.string().nullable().optional(),
  min_severity: z.int().min(1).max(5).nullable().optional(),
  since: z.string().regex(TIMESTAMP).nullable().optional(),
});

export const ActionInput = z.object({
  type: ActionType,
  target: z.string().min(1),
  note: z.string().nullable().optional(),
});

export const Signal = z.object({
  id: z.string(),
  kind: SignalKind,
  severity: z.int().min(1).max(5),
  site: z.string(),
  lane: z.string().nullable(),
  source: z.string(),
  source_reliability: z.int().min(0).max(100),
  observed_at: z.string(),
  title: z.string(),
  summary: z.string(),
  tags: z.array(z.string()),
});

export const SignalHit = z.object({
  id: z.string(),
  title: z.string(),
  kind: SignalKind,
  severity: z.int(),
  site: z.string(),
  observed_at: z.string(),
  score: z.int(),
});

export const SearchResult = z.object({ total: z.int(), results: z.array(SignalHit) });

export const Assessment = z.object({
  assessment_id: z.string(),
  signal_ids: z.array(z.string()),
  score: z.number().min(0).max(1),
  confidence: z.number().min(0).max(1),
  band: z.enum(["low", "medium", "high", "critical"]),
  factors: z.object({
    max_severity: z.int(),
    mean_severity: z.number(),
    distinct_sources: z.int(),
    mean_reliability: z.number(),
    coherence: z.number(),
  }),
  rationale: z.array(z.string()),
  created_at: z.string(),
});

export const Decision = z.object({ by: z.string(), reason: z.string(), at: z.string() });

export const Action = z.object({
  action_id: z.string(),
  assessment_id: z.string(),
  type: ActionType,
  target: z.string(),
  note: z.string().nullable(),
  risk: Risk,
  status: ActionStatus,
  confidence: z.number(),
  proposed_by: z.string(),
  created_at: z.string(),
  policy: z.object({ auto_approve_eligible: z.boolean(), explanation: z.string() }),
  decision: Decision.nullable(),
});

export const ActionList = z.object({ actions: z.array(Action) });

export const AuditEntry = z.object({
  seq: z.int(),
  at: z.string(),
  event: z.string(),
  actor: z.string(),
  subject: z.string(),
  data: z.record(z.string(), z.unknown()),
  prev_hash: z.string(),
  hash: z.string(),
});

export const AuditPage = z.object({
  length: z.int(),
  head_hash: z.string(),
  chain_valid: z.boolean(),
  entries: z.array(AuditEntry),
});

/** Input shapes, keyed by tool name. */
export const inputShapes = {
  search_signals: {
    query: z.string().optional(),
    filters: SearchFilters.nullable().optional(),
    limit: z.int().min(1).max(50).optional(),
  },
  get_signal: { id: z.string() },
  assess: { signal_ids: z.array(z.string()).min(1).max(10) },
  propose_action: {
    assessment_id: z.string(),
    action: ActionInput,
    proposed_by: z.string().min(1).optional(),
  },
  approve_action: { action_id: z.string(), reviewer: z.string().min(1), reason: z.string().min(1) },
  reject_action: { action_id: z.string(), reviewer: z.string().min(1), reason: z.string().min(1) },
  list_actions: { status: ActionStatus.nullable().optional() },
  list_audit: { since_seq: z.int().min(0).optional(), limit: z.int().min(1).max(500).optional() },
} as const;

export const outputSchemas = {
  search_signals: SearchResult,
  get_signal: Signal,
  assess: Assessment,
  propose_action: Action,
  approve_action: Action,
  reject_action: Action,
  list_actions: ActionList,
  list_audit: AuditPage,
} as const;

export type ToolName = keyof typeof inputShapes;
