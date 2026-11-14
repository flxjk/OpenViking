import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";

import { endpoints } from "../src/generated/endpoints.js";

const here = dirname(fileURLToPath(import.meta.url));
const domainsSource = readFileSync(join(here, "..", "src", "effect", "domains.ts"), "utf8");
const readOnlySource = readFileSync(join(here, "..", "src", "generated", "responses.ts"), "utf8");
const mutationSource = readFileSync(join(here, "..", "src", "generated", "mutations.ts"), "utf8");
const specPath = process.env["OV_OPENAPI_SPEC"];

const interfaceKeys = (source: string, name: string): string[] => {
  const block = source.split(`export interface ${name} {`)[1] ?? "";
  return [...block.split("}")[0]!.matchAll(/^  (\w+):/gm)].map((match) => match[1] as string);
};

const usedKeys = new Set(
  [...domainsSource.matchAll(/(?:call|callPath|streamCall)\("([A-Za-z0-9]+)"/g)].map(
    (match) => match[1] as string,
  ),
);

describe("domain coverage", () => {
  it.skipIf(!specPath)("matches every advertised HTTP method in the supplied server OpenAPI surface", () => {
    const spec = JSON.parse(readFileSync(specPath!, "utf8")) as {
      paths: Record<string, Record<string, unknown>>;
    };
    const expected = Object.entries(spec.paths).flatMap(([path, operations]) =>
      Object.entries(operations)
        .filter(([, operation]) => operation !== null && typeof operation === "object"
          && ("operationId" in operation || "responses" in operation))
        .map(([method]) => `${method.toUpperCase()} ${path}`),
    );
    const actual = Object.values(endpoints).map(({ method, path }) => `${method} ${path}`);
    expect(actual.sort()).toEqual(expected.sort());
  });

  it("reaches every generated endpoint", () => {
    const all = Object.keys(endpoints);
    const missing = all.filter((key) => !usedKeys.has(key));
    expect(missing).toEqual([]);
  });

  it("does not reference an endpoint that does not exist", () => {
    const all = new Set(Object.keys(endpoints));
    const unknown = [...usedKeys].filter((key) => !all.has(key));
    expect(unknown).toEqual([]);
  });

  it("covers the whole surface", () => {
    expect(usedKeys.size).toBe(Object.keys(endpoints).length);
  });
});

describe("response typing", () => {
  it("types the read-only surface", () => {
    const typed = interfaceKeys(readOnlySource, "ResponseTypes");
    expect(typed.length).toBeGreaterThanOrEqual(38);
  });

  it("types the mutation surface", () => {
    const typed = interfaceKeys(mutationSource, "MutationResponseTypes");
    expect(typed.length).toBeGreaterThanOrEqual(31);
  });

  it("only names endpoints that exist", () => {
    const all = new Set(Object.keys(endpoints));
    const typed = [
      ...interfaceKeys(readOnlySource, "ResponseTypes"),
      ...interfaceKeys(mutationSource, "MutationResponseTypes"),
    ];
    expect(typed.filter((key) => !all.has(key))).toEqual([]);
  });
});
