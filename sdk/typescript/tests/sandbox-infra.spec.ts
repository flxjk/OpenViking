import { describe, expect, it, beforeAll } from "vitest";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";

import { layer as openVikingLayer } from "../src/effect/client.js";
import { Domains, layer as domainsLayer } from "../src/effect/domains.js";

const SANDBOX_URL = process.env["OV_SANDBOX_URL"] ?? "http://127.0.0.1:1944";

let available = false;

beforeAll(async () => {
  try {
    const response = await fetch(`${SANDBOX_URL}/health`, {
      signal: AbortSignal.timeout(2_000),
    });
    const body = (await response.json()) as { auth_mode?: string };
    available = response.ok;
    if (body.auth_mode !== "dev") {
      available = false;
    }
  } catch {
    available = false;
  }
});

const base = openVikingLayer({ baseUrl: SANDBOX_URL, cache: false });
const stack = Layer.mergeAll(base, domainsLayer.pipe(Layer.provide(base)));

const withDomains = <A, E>(f: (domains: Domains["Service"]) => Effect.Effect<A, E>) =>
  Effect.gen(function* () {
    const domains = yield* Domains;
    return yield* f(domains);
  });

const run = <A, E>(effect: Effect.Effect<A, E, Domains>): Promise<A> =>
  Effect.runPromise(Effect.provide(effect, stack));

type Outcome<A> =
  | { readonly ok: true; readonly value: A }
  | { readonly ok: false; readonly error: { readonly _tag: string } };

const settles = <A, E>(effect: Effect.Effect<A, E, Domains>): Promise<Outcome<A>> =>
  run(
    Effect.match(effect, {
      onFailure: (error) => ({ ok: false as const, error: error as { _tag: string } }),
      onSuccess: (value) => ({ ok: true as const, value }),
    }),
  );

const expectTyped = <A>(outcome: Outcome<A>): A | undefined => {
  if (outcome.ok) return outcome.value;
  expect(outcome.error._tag).toBe("OpenVikingHttpError");
  return undefined;
};

const isContainer = (value: unknown): boolean =>
  typeof value === "object" && value !== null;

const settle = async <A, E>(effect: Effect.Effect<A, E, Domains>): Promise<A | undefined> =>
  expectTyped(await settles(effect));

describe("mcps", () => {
  it("lists servers, status and mounts", async () => {
    if (!available) return;

    const listed = await settle(withDomains((domains) => domains.mcps.list()));
    if (listed) expect(Array.isArray(listed.servers)).toBe(true);

    const status = await settle(withDomains((domains) => domains.mcps.status()));
    if (status) expect(isContainer(status)).toBe(true);

    const mounts = await settle(withDomains((domains) => domains.mcps.mounts()));
    if (mounts) expect(isContainer(mounts)).toBe(true);
  });

  it("reports a bogus url as a typed error", async () => {
    if (!available) return;

    const validation = await settles(
      withDomains((domains) =>
        domains.mcps.validate({ name: "sdk-infra-bogus", url: "not-a-url" }),
      ),
    );
    if (validation.ok) {
      expect(isContainer(validation.value)).toBe(true);
    } else {
      expect(validation.error._tag).toBe("OpenVikingHttpError");
    }
  });
});

describe("compile", () => {
  it("reads capabilities and configs", async () => {
    if (!available) return;

    const capabilities = await settle(withDomains((domains) => domains.compile.capabilities()));
    if (capabilities) {
      expect(typeof (capabilities as { configured?: unknown }).configured).toBe("boolean");
    }

    const configs = await settle(withDomains((domains) => domains.compile.configs()));
    if (configs) expect(Array.isArray((configs as { targets?: unknown }).targets)).toBe(true);
  });

  it("tolerates config and operations being denied", async () => {
    if (!available) return;

    const config = await settle(withDomains((domains) => domains.compile.config()));
    if (config) expect(isContainer(config)).toBe(true);

    const operations = await settle(withDomains((domains) => domains.compile.operations()));
    if (operations) expect(isContainer(operations)).toBe(true);
  });
});

describe("observer", () => {
  it("reads every observer snapshot", async () => {
    if (!available) return;

    const snapshots = await settle(
      withDomains((domains) =>
        Effect.all({
          system: domains.observer.system(),
          queue: domains.observer.queue(),
          models: domains.observer.models(),
          retrieval: domains.observer.retrieval(),
          filesystem: domains.observer.filesystem(),
          lock: domains.observer.lock(),
          vikingdb: domains.observer.vikingdb(),
        }),
      ),
    );
    if (!snapshots) return;
    for (const snapshot of Object.values(snapshots)) {
      expect(typeof snapshot["is_healthy"]).toBe("boolean");
    }
  });
});

describe("debug", () => {
  it("reports health, vector count and a vector scroll", async () => {
    if (!available) return;

    const health = await settle(withDomains((domains) => domains.debug.health()));
    if (health) expect(typeof (health as { healthy?: unknown }).healthy).toBe("boolean");

    const count = await settle(withDomains((domains) => domains.debug.vectorCount()));
    if (count) expect(typeof (count as { count?: unknown }).count).toBe("number");

    const scroll = await settle(
      withDomains((domains) => domains.debug.vectorScroll({ limit: 2 } as never)),
    );
    if (scroll) expect(Array.isArray((scroll as { records?: unknown }).records)).toBe(true);
  });
});

describe("tasks", () => {
  it("lists tasks", async () => {
    if (!available) return;
    const tasks = await settle(withDomains((domains) => domains.tasks.list({ limit: 5 })));
    if (tasks) expect(Array.isArray(tasks)).toBe(true);
  });
});

describe("privacy", () => {
  it("lists categories and the config tree", async () => {
    if (!available) return;

    const categories = await settle(withDomains((domains) => domains.privacy.categories()));
    if (categories) expect(Array.isArray(categories)).toBe(true);

    const tree = await settle(withDomains((domains) => domains.privacy.tree()));
    if (tree) expect(Array.isArray((tree as { categories?: unknown }).categories)).toBe(true);
  });
});

describe("snapshot", () => {
  it("reads the log and the ignore list", async () => {
    if (!available) return;

    const log = await settle(withDomains((domains) => domains.snapshot.log({ limit: 5 })));
    if (log) expect(isContainer(log)).toBe(true);

    const ignore = await settle(withDomains((domains) => domains.snapshot.ignore()));
    if (ignore !== undefined) expect(typeof ignore).toBe("string");
  });
});

describe("agent evolution", () => {
  it("tolerates the outcomes distribution being denied", async () => {
    if (!available) return;
    const outcomes = await settle(withDomains((domains) => domains.agentEvolution.outcomes()));
    if (outcomes) expect(isContainer(outcomes)).toBe(true);
  });
});

describe("stats", () => {
  it("reads memory stats", async () => {
    if (!available) return;
    const stats = await settle(withDomains((domains) => domains.stats.memories()));
    if (stats) expect(typeof stats["total_memories"]).toBe("number");
  });
});

describe("console", () => {
  it("reads the dashboard summary", async () => {
    if (!available) return;
    const summary = await settle(withDomains((domains) => domains.console.summary()));
    if (summary) expect(summary).toHaveProperty("context_counts");
  });
});
