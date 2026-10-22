/**
 * Runtime schemas are hand-written; this test pins them to spec/tools.json. The comparison
 * mirrors conformance/schema_norm.py (fields that do not change validation are dropped).
 */
import { describe, expect, it } from "vitest";
import * as z from "zod";

import { inputShapes, outputSchemas, type ToolName } from "../src/schemas.js";
import { loadToolsSpec } from "../src/spec.js";

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
const DROP = new Set(["title", "default", "$schema", "description", "additionalProperties", "propertyNames", "$comment", "$defs"]);
const SAFE = Number.MAX_SAFE_INTEGER;

const key = (v: Json): string => JSON.stringify(sortKeys(v));
function sortKeys(v: Json): Json {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v !== null && typeof v === "object") {
    return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortKeys(v[k]!)]));
  }
  return v;
}

function uniqueSorted(members: Json[]): Json[] {
  const m = new Map(members.map((x) => [key(x), x]));
  return [...m.keys()].sort().map((k) => m.get(k)!);
}

function normalize(schema: Json, defs: Record<string, Json> = {}): Json {
  if (Array.isArray(schema)) return schema.map((s) => normalize(s, defs));
  if (schema === null || typeof schema !== "object") return schema;
  const localDefs = { ...defs, ...((schema["$defs"] as Record<string, Json> | undefined) ?? {}) };
  if (typeof schema["$ref"] === "string") {
    const target = localDefs[(schema["$ref"] as string).replace("#/$defs/", "")] as Record<string, Json>;
    const { $ref: _r, ...rest } = schema;
    return normalize({ ...target, ...rest }, localDefs);
  }
  const out: Record<string, Json> = {};
  for (const [k, v] of Object.entries(schema)) {
    if (DROP.has(k)) continue;
    if (k === "properties") {
      out[k] = Object.fromEntries(Object.entries(v as Record<string, Json>).map(([p, s]) => [p, normalize(s, localDefs)]));
    } else if (k === "items") {
      out[k] = normalize(v, localDefs);
    } else if (k === "anyOf" || k === "oneOf") {
      const members: Json[] = [];
      for (const m of v as Json[]) {
        const nm = normalize(m, localDefs) as Record<string, Json>;
        if (Object.keys(nm).length === 1 && Array.isArray(nm["anyOf"])) members.push(...(nm["anyOf"] as Json[]));
        else members.push(nm);
      }
      out["anyOf"] = uniqueSorted(members);
    } else if (k === "required") {
      if ((v as string[]).length > 0) out[k] = [...(v as string[])].sort();
    } else if ((k === "minimum" || k === "maximum") && Math.abs(v as number) === SAFE) {
      continue;
    } else {
      out[k] = v;
    }
  }
  if (Array.isArray(out["type"])) {
    const { type, ...rest } = out;
    return { anyOf: uniqueSorted((type as string[]).map((t) => normalize(t === "null" ? { type: t } : { type: t, ...rest }))) };
  }
  if (Array.isArray(out["anyOf"]) && (out["anyOf"] as Json[]).length === 1 && Object.keys(out).length === 1) {
    return (out["anyOf"] as Json[])[0]!;
  }
  return out;
}

const spec = loadToolsSpec();
const defs = spec.$defs as Record<string, Json>;
const toJson = (schema: z.ZodType): Json => z.toJSONSchema(schema, { target: "draft-7", io: "input" }) as Json;

describe("runtime schemas match spec/tools.json", () => {
  it("covers exactly the spec's tools", () => {
    expect(Object.keys(inputShapes).sort()).toEqual(spec.tools.map((t) => t.name).sort());
  });

  it.each(spec.tools.map((t) => [t.name, t] as const))("%s", (name, tool) => {
    const input = toJson(z.object(inputShapes[name as ToolName]));
    const output = toJson(outputSchemas[name as ToolName]);
    expect(normalize(input)).toEqual(normalize({ ...(tool.inputSchema as Record<string, Json>), $defs: defs }));
    expect(normalize(output)).toEqual(normalize({ ...(tool.outputSchema as Record<string, Json>), $defs: defs }));
  });
});
