import { describe, expect, it } from "vitest";
import * as Effect from "effect/Effect";

import { OpenViking, layer } from "../src/effect/client.js";
import { buildUrl, unwrap } from "../src/effect/http.js";
import { parseSse, parseSseBlock } from "../src/effect/stream.js";
import { endpoints } from "../src/generated/endpoints.js";

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

describe("url building", () => {
  it("joins the base url and path", () => {
    expect(buildUrl("http://127.0.0.1:1933", "/health")).toBe(
      "http://127.0.0.1:1933/health",
    );
  });

  it("encodes scalar query parameters", () => {
    const url = buildUrl("http://x", "/api/v1/search/find", { limit: 5, q: "a b" });
    expect(url).toContain("limit=5");
    expect(url).toContain("q=a+b");
  });

  it("repeats array query parameters", () => {
    const url = buildUrl("http://x", "/p", { tag: ["a", "b"] });
    expect(url).toContain("tag=a");
    expect(url).toContain("tag=b");
  });

  it("drops undefined and null query parameters", () => {
    const url = buildUrl("http://x", "/p", { a: undefined, b: null, c: 1 });
    expect(url).toBe("http://x/p?c=1");
  });
});

describe("envelope unwrapping", () => {
  it("returns the result of a wrapped response", () => {
    expect(unwrap({ status: "ok", result: { a: 1 } })).toEqual({ a: 1 });
  });

  it("passes an unwrapped body through", () => {
    expect(unwrap({ a: 1 })).toEqual({ a: 1 });
  });
});

describe("endpoint catalog", () => {
  it("includes the generated core HTTP operation catalog", () => {
    expect(Object.keys(endpoints).length).toBeGreaterThanOrEqual(190);
  });

  it("has a unique operation id per endpoint", () => {
    const ids = Object.values(endpoints).map((endpoint) => endpoint.operationId);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("uses only absolute paths", () => {
    for (const endpoint of Object.values(endpoints)) {
      expect(endpoint.path.startsWith("/")).toBe(true);
    }
  });
});

describe("live server", () => {
  it("reports health through the typed client", async (testContext) => {
    if (!(await reachable())) testContext.skip();
    const program = Effect.gen(function* () {
      const ov = yield* OpenViking;
      return yield* ov.call("healthCheckHealthGet");
    });
    const result = await Effect.runPromise(
      Effect.provide(program, layer({ baseUrl: BASE_URL })),
    );
    expect(result).toBeTruthy();
  });

  it("reads system status through the typed client", async (testContext) => {
    if (!(await reachable())) testContext.skip();
    const program = Effect.gen(function* () {
      const ov = yield* OpenViking;
      return yield* ov.call("systemStatusApiV1SystemStatusGet");
    });
    const result = await Effect.runPromise(
      Effect.provide(program, layer({ baseUrl: BASE_URL })),
    );
    expect(result).toBeTruthy();
  });

  it("surfaces a typed http error for an unknown path", async (testContext) => {
    if (!(await reachable())) testContext.skip();
    const program = Effect.gen(function* () {
      const ov = yield* OpenViking;
      return yield* ov.raw({ method: "GET", path: "/api/v1/definitely-not-a-route" });
    });
    const exit = await Effect.runPromiseExit(
      Effect.provide(program, layer({ baseUrl: BASE_URL })),
    );
    expect(exit._tag).toBe("Failure");
  });
});

describe("server-sent events", () => {
  it("parses a single event block", () => {
    const event = parseSseBlock("event: token\ndata: hello\nid: 7");
    expect(event).toEqual({ event: "token", data: "hello", id: "7" });
  });

  it("joins multi-line data", () => {
    const event = parseSseBlock("data: a\ndata: b");
    expect(event?.data).toBe("a\nb");
  });

  it("ignores comment-only blocks", () => {
    expect(parseSseBlock(": keep-alive")).toBeUndefined();
  });

  it("splits a stream into events", () => {
    const events = parseSse("event: a\ndata: 1\n\nevent: b\ndata: 2\n\n");
    expect(events.map((event) => event.event)).toEqual(["a", "b"]);
    expect(events.map((event) => event.data)).toEqual(["1", "2"]);
  });
});

describe("concurrency", () => {
  it("runs many calls through a bounded semaphore", async (testContext) => {
    if (!(await reachable())) testContext.skip();
    const program = Effect.gen(function* () {
      const ov = yield* OpenViking;
      return yield* Effect.forEach(
        Array.from({ length: 20 }, () => ov.call("healthCheckHealthGet")),
        (effect) => effect,
        { concurrency: "unbounded" },
      );
    });
    const results = await Effect.runPromise(
      Effect.provide(program, layer({ baseUrl: BASE_URL, concurrency: 2 })),
    );
    expect(results).toHaveLength(20);
    expect(results.every((result) => result !== undefined)).toBe(true);
  });
});

describe("response cache", () => {
  it("serves a repeated GET from the cache", async (testContext) => {
    if (!(await reachable())) testContext.skip();
    const program = Effect.gen(function* () {
      const ov = yield* OpenViking;
      yield* ov.call("healthCheckHealthGet");
      yield* ov.call("healthCheckHealthGet");
      return ov.cache ? yield* ov.cache.stats() : undefined;
    });
    const stats = await Effect.runPromise(
      Effect.provide(program, layer({ baseUrl: BASE_URL, cache: { ttlMs: 10_000 } })),
    );
    expect(stats?.hits).toBe(1);
    expect(stats?.misses).toBe(1);
  });

  it("does not cache when disabled", async (testContext) => {
    if (!(await reachable())) testContext.skip();
    const program = Effect.gen(function* () {
      const ov = yield* OpenViking;
      return ov.cache;
    });
    const cache = await Effect.runPromise(
      Effect.provide(program, layer({ baseUrl: BASE_URL, cache: false })),
    );
    expect(cache).toBeUndefined();
  });
});

describe("metrics", () => {
  it("records every call through the client", async (testContext) => {
    if (!(await reachable())) testContext.skip();
    const program = Effect.gen(function* () {
      const ov = yield* OpenViking;
      yield* ov.call("healthCheckHealthGet");
      yield* ov.call("systemStatusApiV1SystemStatusGet");
      return ov.metrics ? yield* ov.metrics.snapshot() : undefined;
    });
    const snapshot = await Effect.runPromise(
      Effect.provide(program, layer({ baseUrl: BASE_URL, cache: false })),
    );
    expect(snapshot?.totalCalls).toBe(2);
    expect(snapshot?.totalErrors).toBe(0);
    expect(Object.keys(snapshot?.endpoints ?? {})).toHaveLength(2);
  });

  it("records a failed call as an error", async (testContext) => {
    if (!(await reachable())) testContext.skip();
    const program = Effect.gen(function* () {
      const ov = yield* OpenViking;
      yield* Effect.exit(ov.raw({ method: "GET", path: "/api/v1/not-a-route" }));
      return ov.metrics ? yield* ov.metrics.snapshot() : undefined;
    });
    const snapshot = await Effect.runPromise(
      Effect.provide(program, layer({ baseUrl: BASE_URL, cache: false })),
    );
    expect(snapshot?.totalErrors).toBe(1);
  });
});
