import { createHash } from "node:crypto";
import { appendFileSync, existsSync, readFileSync } from "node:fs";

import { canonicalJson } from "./canonical.js";

export const GENESIS_HASH = "0".repeat(64);

export interface AuditEntry {
  seq: number;
  at: string;
  event: string;
  actor: string;
  subject: string;
  data: Record<string, unknown>;
  prev_hash: string;
  hash: string;
}

export interface VerifyResult {
  valid: boolean;
  length: number;
  head_hash: string;
  first_bad_seq: number | null;
  problem: string | null;
}

export function entryHash(entry: Omit<AuditEntry, "hash"> & { hash?: string }): string {
  const { hash: _ignored, ...body } = entry;
  return createHash("sha256").update(canonicalJson(body), "utf8").digest("hex");
}

export function verifyEntries(entries: readonly AuditEntry[]): VerifyResult {
  let prev = GENESIS_HASH;
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i]!;
    const expectedSeq = i + 1;
    const fail = (problem: string): VerifyResult => ({
      valid: false,
      length: entries.length,
      head_hash: prev,
      first_bad_seq: expectedSeq,
      problem,
    });
    if (entry.seq !== expectedSeq) return fail(`expected seq ${expectedSeq}, found ${entry.seq}`);
    if (entry.prev_hash !== prev) return fail("prev_hash does not match previous entry");
    if (entryHash(entry) !== entry.hash) return fail("entry content does not match its hash");
    prev = entry.hash;
  }
  return { valid: true, length: entries.length, head_hash: prev, first_bad_seq: null, problem: null };
}

/** Append-only hash chain with optional JSON Lines persistence. */
export class AuditLog {
  private readonly items: AuditEntry[] = [];

  constructor(private readonly path?: string) {
    if (path !== undefined && existsSync(path)) {
      const loaded = readFileSync(path, "utf8")
        .split("\n")
        .filter((line) => line.trim() !== "")
        .map((line) => JSON.parse(line) as AuditEntry);
      const result = verifyEntries(loaded);
      if (!result.valid) {
        throw new Error(
          `refusing to extend a broken audit chain at seq ${result.first_bad_seq}: ${result.problem}`,
        );
      }
      this.items.push(...loaded);
    }
  }

  get length(): number {
    return this.items.length;
  }

  get headHash(): string {
    return this.items.at(-1)?.hash ?? GENESIS_HASH;
  }

  append(fields: { at: string; event: string; actor: string; subject: string; data: Record<string, unknown> }): AuditEntry {
    const body = { seq: this.items.length + 1, ...fields, prev_hash: this.headHash };
    const entry: AuditEntry = { ...body, hash: entryHash(body) };
    this.items.push(entry);
    if (this.path !== undefined) {
      appendFileSync(this.path, `${canonicalJson(entry)}\n`, "utf8");
    }
    return entry;
  }

  entries(sinceSeq = 0, limit?: number): AuditEntry[] {
    const selected = this.items.filter((e) => e.seq > sinceSeq).map((e) => ({ ...e }));
    return limit === undefined ? selected : selected.slice(0, limit);
  }

  verify(): VerifyResult {
    return verifyEntries(this.items);
  }
}
