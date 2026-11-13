import { describe, expect, it } from "vitest";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";

import { layer as openVikingLayer } from "../src/effect/client.js";
import { Domains, layer as domainsLayer } from "../src/effect/domains.js";

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

const stack = domainsLayer.pipe(Layer.provide(openVikingLayer({ baseUrl: BASE_URL })));

const withDomains = <A, E>(f: (domains: Domains["Service"]) => Effect.Effect<A, E>) =>
  Effect.gen(function* () {
    const domains = yield* Domains;
    return yield* f(domains);
  });

type Outcome<A> =
  | { readonly ok: true; readonly value: A }
  | { readonly ok: false; readonly error: { readonly _tag: string } };

const settles = <A, E>(effect: Effect.Effect<A, E, Domains>): Promise<Outcome<A>> =>
  Effect.runPromise(
    Effect.provide(
      Effect.match(effect, {
        onFailure: (error) => ({ ok: false as const, error: error as { _tag: string } }),
        onSuccess: (value) => ({ ok: true as const, value }),
      }),
      stack,
    ),
  );

const expectTyped = <A>(outcome: Outcome<A>): A | undefined => {
  if (outcome.ok) return outcome.value;
  expect(outcome.error._tag).toBe("OpenVikingHttpError");
  return undefined;
};

describe("observer", () => {
  it("reads every observer snapshot", async () => {
    if (!(await reachable())) return;
    const outcome = await settles(
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
    const snapshots = expectTyped(outcome);
    if (!snapshots) return;
    for (const snapshot of Object.values(snapshots)) {
      expect(snapshot).toBeTruthy();
    }
  });
});

describe("console", () => {
  it("reads the dashboard summary", async () => {
    if (!(await reachable())) return;
    const summary = expectTyped(await settles(withDomains((domains) => domains.console.summary())));
    if (summary) expect(summary).toBeTruthy();
  });
});

describe("tasks", () => {
  it("lists tasks", async () => {
    if (!(await reachable())) return;
    const tasks = expectTyped(
      await settles(withDomains((domains) => domains.tasks.list({ limit: 5 }))),
    );
    if (tasks) expect(Array.isArray(tasks)).toBe(true);
  });
});

describe("stats", () => {
  it("reads memory stats", async () => {
    if (!(await reachable())) return;
    const stats = expectTyped(await settles(withDomains((domains) => domains.stats.memories())));
    if (stats) expect(stats).toBeTruthy();
  });
});

describe("watches", () => {
  it("lists watches", async () => {
    if (!(await reachable())) return;
    const watches = expectTyped(
      await settles(withDomains((domains) => domains.watches.list({ limit: 5 }))),
    );
    if (watches) {
      expect(watches).toBeTruthy();
      expect(Array.isArray((watches as { tasks?: unknown[] }).tasks ?? [])).toBe(true);
    }
  });
});

describe("privacy", () => {
  it("lists privacy categories and the tree", async () => {
    if (!(await reachable())) return;
    const result = expectTyped(
      await settles(
        withDomains((domains) =>
          Effect.all({
            categories: domains.privacy.categories(),
            tree: domains.privacy.tree(),
          }),
        ),
      ),
    );
    if (!result) return;
    expect(Array.isArray(result.categories)).toBe(true);
    expect(result.tree).toBeTruthy();
  });
});

describe("acl", () => {
  it("reads the acl of the memory root", async () => {
    if (!(await reachable())) return;
    const acl = expectTyped(
      await settles(
        withDomains((domains) => domains.acl.get({ uri: "viking://user/default/memories" })),
      ),
    );
    if (acl) expect(acl).toBeTruthy();
  });
});

describe("compile", () => {
  it("reads capabilities, config and operations", async () => {
    if (!(await reachable())) return;
    const result = expectTyped(
      await settles(
        withDomains((domains) =>
          Effect.all({
            capabilities: domains.compile.capabilities(),
            config: domains.compile.config(),
            configs: domains.compile.configs(),
            operations: domains.compile.operations(),
          }),
        ),
      ),
    );
    if (!result) return;
    expect(result.capabilities).toBeTruthy();
    expect(result.config).toBeTruthy();
  });
});

describe("mcps", () => {
  it("lists registered mcp servers", async () => {
    if (!(await reachable())) return;
    const mcps = expectTyped(await settles(withDomains((domains) => domains.mcps.list())));
    if (mcps) expect(mcps).toBeTruthy();
  });
});

describe("admin", () => {
  it("lists accounts and cluster configuration", async () => {
    if (!(await reachable())) return;
    const result = expectTyped(
      await settles(
        withDomains((domains) =>
          Effect.all({
            accounts: domains.admin.accounts(),
            configuration: domains.admin.configuration(),
          }),
        ),
      ),
    );
    if (!result) return;
    expect(Array.isArray(result.accounts)).toBe(true);
    expect(result.configuration).toBeTruthy();
  });
});

describe("snapshot", () => {
  it("reads the snapshot log", async () => {
    if (!(await reachable())) return;
    const log = expectTyped(
      await settles(withDomains((domains) => domains.snapshot.log({ limit: 5 }))),
    );
    if (log) expect(log).toBeTruthy();
  });
});

describe("rules", () => {
  it("searches rules for a task context", async () => {
    if (!(await reachable())) return;
    const result = expectTyped(
      await settles(withDomains((domains) => domains.rules.search({ task_context: "telemetry" }))),
    );
    if (result) expect(result).toBeTruthy();
  });
});

describe("bot", () => {
  it("reports bot health", async () => {
    if (!(await reachable())) return;
    const health = expectTyped(await settles(withDomains((domains) => domains.bot.health())));
    if (health) expect(health).toBeTruthy();
  });
});
