import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { canonicalJson, round4 } from "../src/canonical.js";
import { specDir } from "../src/spec.js";

interface Vector {
  name: string;
  value: unknown;
  canonical: string;
  sha256: string;
}

const vectors = (
  JSON.parse(readFileSync(path.join(specDir(), "fixtures", "canonical_vectors.json"), "utf8")) as { vectors: Vector[] }
).vectors;

describe("canonical JSON (shared vectors with the Python twin)", () => {
  it.each(vectors.map((v) => [v.name, v] as const))("%s", (_name, v) => {
    const text = canonicalJson(v.value);
    expect(text).toBe(v.canonical);
    expect(createHash("sha256").update(text, "utf8").digest("hex")).toBe(v.sha256);
  });

  it("rejects non-finite numbers", () => {
    expect(() => canonicalJson(Number.NaN)).toThrow();
  });
});

describe("round4", () => {
  it("rounds exact ties up, like the Python twin's floor(x + 0.5)", () => {
    expect(round4(0.00045)).toBe(0.0005);
    expect(round4(0.89833333)).toBe(0.8983);
  });
});
