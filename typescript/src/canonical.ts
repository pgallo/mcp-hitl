/**
 * Deterministic serialization, byte-compatible with python/src/hitl_server/canonical.py.
 * JSON.stringify already renders integral numbers without a fraction and uses the same
 * shortest round-trip digits as Python's repr, so the only work here is key ordering.
 */

export function round4(x: number): number {
  // Same operations as the Python twin: floor(x * 10000 + 0.5) / 10000.
  return Math.floor(x * 10000 + 0.5) / 10000;
}

export function fmtNum(x: number): string {
  if (!Number.isFinite(x)) {
    throw new Error("non-finite numbers cannot be canonicalized");
  }
  return String(x);
}

export function canonicalJson(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return fmtNum(value);
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>).sort();
    const parts = keys.map(
      (k) => `${JSON.stringify(k)}:${canonicalJson((value as Record<string, unknown>)[k])}`,
    );
    return `{${parts.join(",")}}`;
  }
  throw new Error(`cannot canonicalize ${typeof value}`);
}
