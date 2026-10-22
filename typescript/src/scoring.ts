/**
 * Search ranking and situation assessment, mirrored operation-for-operation from
 * python/src/hitl_server/scoring.py. Floating point results are bit-identical as long
 * as both sides evaluate the same expressions in the same order.
 */
import { round4 } from "./canonical.js";
import type { Signal, SignalKind } from "./spec.js";

export function tokens(text: string): string[] {
  return text.toLowerCase().match(/[a-z0-9]+/g) ?? [];
}

export function matchScore(signal: Signal, queryTokens: readonly string[]): number {
  const title = new Set(tokens(signal.title));
  const tags = new Set(tokens(signal.tags.join(" ")));
  const summary = new Set(tokens(signal.summary));
  let score = 0;
  for (const tok of queryTokens) {
    if (title.has(tok)) score += 3;
    if (tags.has(tok)) score += 2;
    if (summary.has(tok)) score += 1;
  }
  return score;
}

export interface SignalHit {
  id: string;
  title: string;
  kind: SignalKind;
  severity: number;
  site: string;
  observed_at: string;
  score: number;
}

export interface SearchFilters {
  kind?: SignalKind[] | null | undefined;
  site?: string | null | undefined;
  min_severity?: number | null | undefined;
  since?: string | null | undefined;
}

const cmpStr = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

export function search(
  signals: readonly Signal[],
  query: string,
  filters: SearchFilters,
  limit: number,
): { total: number; results: SignalHit[] } {
  const queryTokens = [...new Set(tokens(query))];
  const { kind, site, min_severity: minSeverity, since } = filters;
  const hits: SignalHit[] = [];
  for (const s of signals) {
    if (kind != null && !kind.includes(s.kind)) continue;
    if (site != null && s.site !== site) continue;
    if (minSeverity != null && s.severity < minSeverity) continue;
    if (since != null && s.observed_at < since) continue;
    const score = queryTokens.length > 0 ? matchScore(s, queryTokens) : 0;
    if (queryTokens.length > 0 && score === 0) continue;
    hits.push({
      id: s.id,
      title: s.title,
      kind: s.kind,
      severity: s.severity,
      site: s.site,
      observed_at: s.observed_at,
      score,
    });
  }
  hits.sort((a, b) => b.score - a.score || b.severity - a.severity || cmpStr(a.id, b.id));
  return { total: hits.length, results: hits.slice(0, limit) };
}

function related(a: Signal, b: Signal): boolean {
  if (a.site === b.site) return true;
  if (a.lane !== null && a.lane === b.lane) return true;
  const tagsB = new Set(b.tags);
  return a.tags.some((t) => tagsB.has(t));
}

export const SINGLE_SOURCE_CAP = 0.6;

export type Band = "low" | "medium" | "high" | "critical";

export function bandFor(score: number): Band {
  if (score < 0.35) return "low";
  if (score < 0.55) return "medium";
  if (score < 0.75) return "high";
  return "critical";
}

export interface AssessmentCore {
  score: number;
  confidence: number;
  band: Band;
  factors: {
    max_severity: number;
    mean_severity: number;
    distinct_sources: number;
    mean_reliability: number;
    coherence: number;
  };
  rationale: string[];
}

/** Score is "how bad if true", confidence is "how much should we believe it". */
export function assessSignals(group: readonly Signal[]): AssessmentCore {
  const n = group.length;
  const severities = group.map((s) => s.severity);
  const maxSev = Math.max(...severities);
  const sumSev = severities.reduce((a, b) => a + b, 0);
  const sumRel = group.reduce((a, s) => a + s.source_reliability, 0);
  const sources = [...new Set(group.map((s) => s.source))].sort();
  const distinctSources = sources.length;

  const pairs = (n * (n - 1)) / 2;
  let relatedPairs = 0;
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (related(group[i]!, group[j]!)) relatedPairs += 1;
    }
  }
  const coherence = n === 1 ? 1.0 : relatedPairs / pairs;

  const meanSev = sumSev / n;
  const meanRel = sumRel / n;
  const scoreRaw = 0.5 * (maxSev / 5) + 0.3 * (meanSev / 5) + 0.2 * (Math.min(n, 5) / 5);
  let confRaw = 0.5 * (meanRel / 100) + 0.3 * (Math.min(distinctSources, 3) / 3) + 0.2 * coherence;
  const capped = distinctSources === 1 && confRaw > SINGLE_SOURCE_CAP;
  if (capped) confRaw = SINGLE_SOURCE_CAP;

  const score = round4(scoreRaw);
  const confidence = round4(confRaw);
  const rationale = [
    `Severity peaks at ${maxSev}/5 across ${n} signal(s)`,
    `Sources: ${distinctSources} distinct (${sources.join(", ")}), mean reliability ${Math.floor(meanRel + 0.5)}/100`,
    n === 1 ? "Coherence: single signal" : `Coherence: ${relatedPairs}/${pairs} signal pairs share a site, lane or tag`,
  ];
  if (capped) rationale.push("Single-source cap: confidence limited to 0.6 until corroborated");
  if (meanRel < 60) rationale.push("Low source reliability: treat as unverified");

  return {
    score,
    confidence,
    band: bandFor(score),
    factors: {
      max_severity: maxSev,
      mean_severity: round4(meanSev),
      distinct_sources: distinctSources,
      mean_reliability: round4(meanRel),
      coherence: round4(coherence),
    },
    rationale,
  };
}
