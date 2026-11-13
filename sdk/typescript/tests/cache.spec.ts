import { describe, expect, it } from "vitest";
import * as Effect from "effect/Effect";

import { makeCache } from "../src/effect/cache.js";

describe("cache", () => {
  it("serves a second read from memory", async () => {
    const cache = makeCache({ ttlMs: 1_000 });
    let calls = 0;
    const load = Effect.sync(() => {
      calls += 1;
      return calls;
    });

    const first = await Effect.runPromise(cache.get("k", load));
    const second = await Effect.runPromise(cache.get("k", load));

    expect(first).toBe(1);
    expect(second).toBe(1);
    expect(calls).toBe(1);
  });

  it("reloads after the ttl expires", async () => {
    const cache = makeCache({ ttlMs: 1 });
    let calls = 0;
    const load = Effect.sync(() => {
      calls += 1;
      return calls;
    });

    await Effect.runPromise(cache.get("k", load));
    await new Promise((resolve) => setTimeout(resolve, 10));
    const second = await Effect.runPromise(cache.get("k", load));

    expect(second).toBe(2);
  });

  it("evicts the oldest entry past capacity", async () => {
    const cache = makeCache({ capacity: 2, ttlMs: 10_000 });
    await Effect.runPromise(cache.get("a", Effect.succeed(1)));
    await Effect.runPromise(cache.get("b", Effect.succeed(2)));
    await Effect.runPromise(cache.get("c", Effect.succeed(3)));

    expect(await Effect.runPromise(cache.size())).toBe(2);
    expect(await Effect.runPromise(cache.get("a", Effect.succeed(99)))).toBe(99);
  });

  it("invalidates one key and all keys", async () => {
    const cache = makeCache({ ttlMs: 10_000 });
    await Effect.runPromise(cache.get("a", Effect.succeed(1)));
    await Effect.runPromise(cache.get("b", Effect.succeed(2)));

    await Effect.runPromise(cache.invalidate("a"));
    expect(await Effect.runPromise(cache.size())).toBe(1);

    await Effect.runPromise(cache.invalidateAll());
    expect(await Effect.runPromise(cache.size())).toBe(0);
  });

  it("counts hits and misses", async () => {
    const cache = makeCache({ ttlMs: 10_000 });
    await Effect.runPromise(cache.get("a", Effect.succeed(1)));
    await Effect.runPromise(cache.get("a", Effect.succeed(1)));
    await Effect.runPromise(cache.get("b", Effect.succeed(2)));

    const stats = await Effect.runPromise(cache.stats());
    expect(stats.hits).toBe(1);
    expect(stats.misses).toBe(2);
  });
});
