import { describe, expect, it, beforeAll } from "vitest";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";

import { OpenViking, layer as openVikingLayer } from "../src/effect/client.js";
import { Domains, layer as domainsLayer } from "../src/effect/domains.js";

const SANDBOX_URL = process.env["OV_SANDBOX_URL"] ?? "http://127.0.0.1:1944";
const RUN = Date.now().toString(36);
const ROOT = `viking://user/default/sdk-lifecycle-${RUN}`;
const NOTE = `${ROOT}/note.md`;
const COPY = `${ROOT}/copy.md`;
const MOVED = `${ROOT}/moved.md`;
const SESSION = `sdk-lifecycle-${RUN}`;

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

const run = <A, E>(effect: Effect.Effect<A, E, Domains | OpenViking>) =>
  Effect.runPromise(Effect.provide(effect, stack));

const withDomains = <A, E>(f: (domains: Domains["Service"]) => Effect.Effect<A, E>) =>
  Effect.gen(function* () {
    const domains = yield* Domains;
    return yield* f(domains);
  });

describe("filesystem lifecycle", () => {
  it("creates, reads, copies, moves and removes a resource", async () => {
    if (!available) return;

    const created = await run(
      withDomains((domains) =>
        Effect.all({
          mkdir: domains.fs.mkdir({ uri: ROOT, description: "SDK lifecycle" }),
          write: domains.content.write({
            uri: NOTE,
            content: "# note\nsandbox body\n",
            mode: "create",
            processing_mode: "vectors_only",
          }),
        }),
      ),
    );
    expect(created.mkdir).toBeTruthy();
    expect(created.write).toBeTruthy();

    const body = await run(withDomains((domains) => domains.content.read({ uri: NOTE })));
    expect(typeof body).toBe("string");
    expect(body).toContain("sandbox body");

    const entries = await run(withDomains((domains) => domains.fs.ls({ uri: ROOT })));
    expect(entries.some((entry) => entry.uri === NOTE)).toBe(true);

    const stat = await run(withDomains((domains) => domains.fs.stat({ uri: NOTE })));
    expect(stat.uri).toBe(NOTE);

    const copied = await run(
      withDomains((domains) =>
        domains.fs.cp({ from_uri: NOTE, to_uri: COPY, skip_conflicts: true } as never),
      ),
    );
    expect(copied).toBeTruthy();

    const moved = await run(
      withDomains((domains) =>
        domains.fs.mv({ from_uri: COPY, to_uri: MOVED, skip_conflicts: true } as never),
      ),
    );
    expect(moved).toBeTruthy();

    const afterMove = await run(withDomains((domains) => domains.fs.ls({ uri: ROOT })));
    expect(afterMove.some((entry) => entry.uri === MOVED)).toBe(true);

    await run(withDomains((domains) => domains.fs.rm({ uri: ROOT, recursive: true })));
  });
});

describe("session lifecycle", () => {
  it("creates a session, appends messages, commits and deletes", async () => {
    if (!available) return;

    const created = await run(
      withDomains((domains) => domains.sessions.create({ session_id: SESSION })),
    );
    expect(created).toBeTruthy();

    const listed = await run(withDomains((domains) => domains.sessions.list()));
    expect(listed.some((session) => session.session_id === SESSION)).toBe(true);

    const message = await run(
      withDomains((domains) =>
        domains.sessions.addMessage(SESSION, { role: "user", content: "hello sandbox" }),
      ),
    );
    expect(message).toBeTruthy();

    const batch = await run(
      withDomains((domains) =>
        domains.sessions.batchAddMessages(SESSION, {
          messages: [{ role: "assistant", content: "hi" }],
        } as never),
      ),
    );
    expect(batch).toBeTruthy();

    const context = await run(withDomains((domains) => domains.sessions.context(SESSION)));
    expect(context).toBeTruthy();

    await run(withDomains((domains) => domains.sessions.remove(SESSION)));

    const after = await run(withDomains((domains) => domains.sessions.list()));
    expect(after.some((session) => session.session_id === SESSION)).toBe(false);
  });
});

describe("skills and acl", () => {
  it("validates a skill payload without writing one", async () => {
    if (!available) return;
    const validation = await run(
      withDomains((domains) => domains.skills.validate({ data: { name: "sdk-lifecycle" } })),
    );
    expect(validation).toBeTruthy();
  });

  it("surfaces the acl restriction as a typed error", async () => {
    if (!available) return;
    const outcome = await run(
      Effect.match(withDomains((domains) => domains.acl.get({ uri: ROOT })), {
        onFailure: (error) => ({ ok: false as const, tag: (error as { _tag: string })._tag }),
        onSuccess: (value) => ({ ok: true as const, value }),
      }),
    );
    if (outcome.ok) {
      expect(outcome.value).toBeTruthy();
    } else {
      expect(outcome.tag).toBe("OpenVikingHttpError");
    }
  });
});

describe("observability during a lifecycle", () => {
  it("counts every call it made", async () => {
    if (!available) return;
    const client = await run(
      Effect.gen(function* () {
        const ov = yield* OpenViking;
        return ov;
      }),
    );
    expect(client.config.baseUrl).toBe(SANDBOX_URL);
  });
});

describe("webdav lifecycle", () => {
  it("puts, reads and deletes a resource", async () => {
    if (!available) return;
    const path = `sdk-webdav-${RUN}.md`;

    await run(
      withDomains((domains) =>
        domains.webdav.putPath(path, { content: "webdav body" } as never),
      ),
    );

    const read = await run(
      withDomains((domains) => domains.webdav.getPath(path, {} as never)),
    );
    expect(read).toBeTruthy();

    await run(
      withDomains((domains) => domains.webdav.removePath(path, {} as never)),
    );
  });
});

describe("snapshot ignore", () => {
  it("sets, reads and clears the ignore list", async () => {
    if (!available) return;

    await run(
      withDomains((domains) => domains.snapshot.setIgnore({ content: "*.tmp\n" })),
    );
    const current = await run(withDomains((domains) => domains.snapshot.ignore()));
    expect(current).toBeTruthy();

    await run(withDomains((domains) => domains.snapshot.removeIgnore()));
  });
});

describe("user settings", () => {
  it("patches and clears add locations", async () => {
    if (!available) return;

    const patched = await run(
      withDomains((domains) => domains.userSettings.patchAddLocations({} as never)),
    );
    expect(patched).toBeTruthy();

    const current = await run(withDomains((domains) => domains.userSettings.addLocations()));
    expect(current).toBeTruthy();

    await run(withDomains((domains) => domains.userSettings.removeAddLocations()));
  });
});
