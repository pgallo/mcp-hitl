import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

export type SignalKind = "delay" | "sensor_anomaly" | "supplier_notice" | "customs_hold" | "weather";

export interface Signal {
  id: string;
  kind: SignalKind;
  severity: number;
  site: string;
  lane: string | null;
  source: string;
  source_reliability: number;
  observed_at: string;
  title: string;
  summary: string;
  tags: string[];
}

export interface RiskTier {
  auto_approve_min_confidence: number | null;
  min_confidence_to_propose: number;
  min_reason_length: number;
  separation_of_duties: boolean;
}

export interface PolicyDoc {
  version: string;
  action_types: Record<string, { risk: "low" | "medium" | "high" }>;
  risk_tiers: Record<"low" | "medium" | "high", RiskTier>;
}

export interface ToolSpec {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  outputSchema: Record<string, unknown>;
}

export interface ToolsSpec {
  version: string;
  $defs: Record<string, unknown>;
  tools: ToolSpec[];
  resources: { uri: string; name: string; mimeType: string; description: string }[];
  prompts: { name: string; description: string; arguments: { name: string; description: string; required: boolean }[] }[];
}

export function specDir(): string {
  const override = process.env["HITL_SPEC_DIR"];
  if (override) return override;
  // Both src/ (under the test runner) and dist/ (compiled) sit one level below typescript/.
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "spec");
}

function loadJson<T>(name: string): T {
  return JSON.parse(readFileSync(path.join(specDir(), name), "utf8")) as T;
}

export const loadSignals = (): Signal[] => loadJson<{ signals: Signal[] }>("signals.json").signals;
export const loadPolicy = (): PolicyDoc => loadJson<PolicyDoc>("policy.json");
export const loadToolsSpec = (): ToolsSpec => loadJson<ToolsSpec>("tools.json");
export const triageTemplate = (): string => readFileSync(path.join(specDir(), "prompts", "triage.md"), "utf8");

export interface Clock {
  now(): string;
}

const iso = (d: Date): string => d.toISOString().replace(/\.\d{3}Z$/, "Z");

export class SystemClock implements Clock {
  now(): string {
    return iso(new Date());
  }
}

/** Advances one second per call, matching the Python FixedClock. */
export class FixedClock implements Clock {
  private ticks = 0;
  private readonly start: number;

  constructor(start: string) {
    this.start = Date.parse(start);
    if (Number.isNaN(this.start)) throw new Error(`invalid clock start ${start}`);
  }

  now(): string {
    const value = iso(new Date(this.start + this.ticks * 1000));
    this.ticks += 1;
    return value;
  }
}

export function clockFromEnv(): Clock {
  const raw = process.env["HITL_CLOCK"] ?? "";
  return raw.startsWith("fixed:") ? new FixedClock(raw.slice("fixed:".length)) : new SystemClock();
}
