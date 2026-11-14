import { describe, expect, it } from "vitest";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";

import { OpenViking, layer as openVikingLayer } from "../src/effect/client.js";
import { TRUNCATION_MARKER, assembleContext } from "../src/effect/context.js";
import { Domains, layer as domainsLayer, type DomainsShape } from "../src/effect/domains.js";

const BASE_URL = process.env["OV_URL"] ?? "http://127.0.0.1:1933";

const reachable = async (): Promise<boolean> => {
  if (!process.env["OV_URL"]) return false;
  const response = await fetch(`${BASE_URL}/health`, {
    signal: AbortSignal.timeout(2_000),
  });
  if (!response.ok) {
    throw new Error(`Configured OV_URL health returned HTTP ${response.status}`);
  }
  return true;
};

interface FakeEntry {
  readonly category: string;
  readonly detail: string;
  readonly origin: string;
  readonly score: number;
  readonly text: string;
  readonly uri: string;
}

const fakeDomains = (rendered: string, entries: readonly FakeEntry[]): DomainsShape =>
  ({
    search: {
      recall: () => Effect.succeed({ rendered, digest: "digest-1", entries, stats: {} }),
    },
  }) as unknown as DomainsShape;

describe("context assembly", () => {
  it("orders unique citations by score", async () => {
    const domains = fakeDomains("short rendered block", [
      { category: "memories", detail: "full", origin: "self", score: 0.4, text: "b", uri: "viking://b" },
      { category: "memories", detail: "full", origin: "self", score: 0.9, text: "a", uri: "viking://a" },
      { category: "memories", detail: "full", origin: "self", score: 0.7, text: "b2", uri: "viking://b" },
    ]);

    const block = await Effect.runPromise(assembleContext(domains, { query: "q" }));

    expect(block.citations).toEqual(["viking://a", "viking://b"]);
    expect(block.entries.map((entry) => entry.uri)).toEqual(["viking://a", "viking://b", "viking://b"]);
    expect(block.digest).toBe("digest-1");
    expect(block.text).toBe("short rendered block");
    expect(block.tokensUsed).toBe(Math.ceil("short rendered block".length / 4));
    expect(block.truncated).toBe(false);
  });

  it("truncates oversized renders at a line boundary", async () => {
    const line = "x".repeat(120);
    const rendered = Array.from({ length: 20 }, () => line).join("\n");
    const domains = fakeDomains(rendered, [
      { category: "memories", detail: "full", origin: "self", score: 0.5, text: "a", uri: "viking://a" },
    ]);

    const block = await Effect.runPromise(assembleContext(domains, { query: "q", maxTokens: 100 }));

    expect(rendered.length).toBeGreaterThan(400);
    expect(block.truncated).toBe(true);
    expect(block.text.endsWith(TRUNCATION_MARKER)).toBe(true);
    expect(block.text.length).toBeLessThan(400 + TRUNCATION_MARKER.length);
    expect(block.text.slice(0, -TRUNCATION_MARKER.length).endsWith("\n")).toBe(false);
    expect(block.tokensUsed).toBe(Math.ceil(block.text.length / 4));
  });
});

const base = openVikingLayer({ baseUrl: BASE_URL });
const domains = domainsLayer.pipe(Layer.provide(base));
const stack = Layer.mergeAll(base, domains);

const run = <A, E>(effect: Effect.Effect<A, E, OpenViking | Domains>) =>
  Effect.runPromise(Effect.provide(effect, stack));

describe("context assembly live", () => {
  it("assembles a recall block", async (testContext) => {
    if (!(await reachable())) testContext.skip();
    const block = await run(
      Effect.gen(function* () {
        const domains = yield* Domains;
        return yield* assembleContext(domains, { query: "local AI stack" });
      }),
    );
    expect(block.citations.length).toBeGreaterThan(0);
    expect(block.text.length).toBeGreaterThan(0);
  });
});
