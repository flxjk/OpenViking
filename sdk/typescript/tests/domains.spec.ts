import { describe, expect, it } from "vitest";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";

import { OpenViking, layer as openVikingLayer } from "../src/effect/client.js";
import { Domains, layer as domainsLayer } from "../src/effect/domains.js";
import { Feedback, layer as feedbackLayer } from "../src/effect/self-improve.js";

const BASE_URL = process.env["OV_URL"] ?? "http://127.0.0.1:1933";

const reachable = async (): Promise<boolean> => {
  try {
    const response = await fetch(`${BASE_URL}/health`, {
      signal: AbortSignal.timeout(2_000),
    });
    return response.ok;
  } catch {
    return false;
  }
};

const base = openVikingLayer({ baseUrl: BASE_URL });
const domains = domainsLayer.pipe(Layer.provide(base));
const feedback = feedbackLayer().pipe(Layer.provide(domains));
const stack = Layer.mergeAll(base, domains, feedback);

const run = <A, E>(effect: Effect.Effect<A, E, OpenViking | Domains | Feedback>) =>
  Effect.runPromise(Effect.provide(effect, stack));

describe("domains", () => {
  it("reads health", async () => {
    if (!(await reachable())) return;
    const health = await run(
      Effect.gen(function* () {
        const domains = yield* Domains;
        return yield* domains.system.health();
      }),
    );
    expect(health.healthy).toBe(true);
    expect(typeof health.version).toBe("string");
  });

  it("reads system status", async () => {
    if (!(await reachable())) return;
    const status = await run(
      Effect.gen(function* () {
        const domains = yield* Domains;
        return yield* domains.system.status();
      }),
    );
    expect(status.initialized).toBe(true);
  });

  it("lists the memory root", async () => {
    if (!(await reachable())) return;
    const entries = await run(
      Effect.gen(function* () {
        const domains = yield* Domains;
        return yield* domains.fs.ls({ uri: "viking://user/default/memories" });
      }),
    );
    expect(Array.isArray(entries)).toBe(true);
    expect(entries.length).toBeGreaterThan(0);
    expect(entries[0]?.uri.startsWith("viking://")).toBe(true);
  });

  it("lists skills", async () => {
    if (!(await reachable())) return;
    const skills = await run(
      Effect.gen(function* () {
        const domains = yield* Domains;
        return yield* domains.skills.list();
      }),
    );
    expect(Array.isArray(skills.skills)).toBe(true);
  });

  it("lists sessions", async () => {
    if (!(await reachable())) return;
    const sessions = await run(
      Effect.gen(function* () {
        const domains = yield* Domains;
        return yield* domains.sessions.list();
      }),
    );
    expect(Array.isArray(sessions)).toBe(true);
  });

  it("runs a semantic find", async () => {
    if (!(await reachable())) return;
    const result = await run(
      Effect.gen(function* () {
        const domains = yield* Domains;
        return yield* domains.search.find({ query: "local AI stack", limit: 3 });
      }),
    );
    expect(result.total).toBeGreaterThan(0);
    expect(result.memories.length + result.resources.length + result.skills.length).toBe(
      result.total,
    );
  });
});

describe("self-improving retrieval", () => {
  it("starts from a neutral policy", async () => {
    const policy = await run(
      Effect.gen(function* () {
        const feedback = yield* Feedback;
        return yield* feedback.policy();
      }),
    );
    expect(policy.samples).toBe(0);
    expect(policy.hitRate).toBeCloseTo(0.5, 5);
    expect(policy.limit).toBeGreaterThan(0);
  });

  it("adapts after a used result", async () => {
    if (!(await reachable())) return;
    const outcome = await run(
      Effect.gen(function* () {
        const feedback = yield* Feedback;
        const result = yield* feedback.find("local AI stack");
        const first = result.memories[0] ?? result.resources[0] ?? result.skills[0];
        if (!first) return { used: false, stats: yield* feedback.stats() };
        const used = yield* feedback.markUsed(first.uri);
        return { used, stats: yield* feedback.stats() };
      }),
    );
    expect(outcome.used).toBe(true);
    expect(outcome.stats.samples).toBe(1);
    expect(outcome.stats.used).toBe(1);
    expect(outcome.stats.hitRate).toBeGreaterThan(0.5);
  });

  it("raises the limit when nothing is used", async () => {
    const policy = await run(
      Effect.gen(function* () {
        const feedback = yield* Feedback;
        yield* feedback.record({ query: "q", uri: "viking://x", score: 0.1, used: false, at: 0 });
        yield* feedback.record({ query: "q", uri: "viking://y", score: 0.1, used: false, at: 0 });
        return yield* feedback.policy();
      }),
    );
    expect(policy.hitRate).toBeLessThan(0.5);
    expect(policy.limit).toBeGreaterThan(10);
  });

  it("resets to the neutral policy", async () => {
    const policy = await run(
      Effect.gen(function* () {
        const feedback = yield* Feedback;
        yield* feedback.record({ query: "q", uri: "viking://x", score: 0.9, used: true, at: 0 });
        yield* feedback.reset();
        return yield* feedback.policy();
      }),
    );
    expect(policy.samples).toBe(0);
  });
});
