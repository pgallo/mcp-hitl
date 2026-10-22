import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { AuditLog, GENESIS_HASH, entryHash, verifyEntries, type AuditEntry } from "../src/audit.js";
import { specDir } from "../src/spec.js";

function makeLog(n = 4, file?: string): AuditLog {
  const log = new AuditLog(file);
  for (let i = 0; i < n; i++) {
    log.append({ at: `2026-03-02T09:00:0${i}Z`, event: "test.event", actor: "tester", subject: `S-${i}`, data: { i } });
  }
  return log;
}

describe("hash-chained audit log", () => {
  it("links each entry to its predecessor", () => {
    const entries = makeLog().entries();
    expect(entries[0]!.prev_hash).toBe(GENESIS_HASH);
    for (let i = 1; i < entries.length; i++) expect(entries[i]!.prev_hash).toBe(entries[i - 1]!.hash);
    expect(verifyEntries(entries).valid).toBe(true);
  });

  const tamperings: [string, (es: AuditEntry[]) => void, number, string][] = [
    ["edit", (es) => (es[1]!.data = { i: 99 }), 2, "content"],
    ["delete", (es) => es.splice(1, 1), 2, "seq"],
    ["reorder", (es) => es.unshift(es.splice(2, 1)[0]!), 1, "seq"],
    ["relink", (es) => (es[2]!.prev_hash = GENESIS_HASH), 3, "prev_hash"],
  ];
  it.each(tamperings)("locates %s tampering", (_name, mutate, badSeq, problem) => {
    const entries = makeLog().entries();
    mutate(entries);
    const result = verifyEntries(entries);
    expect(result.valid).toBe(false);
    expect(result.first_bad_seq).toBe(badSeq);
    expect(result.problem).toContain(problem);
  });

  it("re-hashing an edited entry still breaks the next link", () => {
    const entries = makeLog().entries();
    entries[1]!.data = { i: 99 };
    entries[1]!.hash = entryHash(entries[1]!);
    expect(verifyEntries(entries)).toMatchObject({ first_bad_seq: 3, problem: "prev_hash does not match previous entry" });
  });

  it("persists as JSON Lines and refuses to extend a broken chain", () => {
    const file = path.join(mkdtempSync(path.join(tmpdir(), "hitl-")), "audit.jsonl");
    const log = makeLog(3, file);
    const reopened = new AuditLog(file);
    expect(reopened.length).toBe(3);
    expect(reopened.headHash).toBe(log.headHash);
    const lines = readFileSync(file, "utf8").trim().split("\n");
    const first = JSON.parse(lines[0]!) as AuditEntry;
    first.actor = "mallory";
    lines[0] = JSON.stringify(first);
    writeFileSync(file, `${lines.join("\n")}\n`);
    expect(() => new AuditLog(file)).toThrow(/broken audit chain at seq 1/);
  });

  it("verifies the audit chain written by the Python twin", () => {
    const entries = readFileSync(path.join(specDir(), "fixtures", "reference_audit.jsonl"), "utf8")
      .trim()
      .split("\n")
      .map((l) => JSON.parse(l) as AuditEntry);
    const scenario = JSON.parse(readFileSync(path.join(specDir(), "fixtures", "reference_scenario.json"), "utf8")) as {
      head_hash: string;
    };
    const result = verifyEntries(entries);
    expect(result.valid).toBe(true);
    expect(result.head_hash).toBe(scenario.head_hash);
  });
});
