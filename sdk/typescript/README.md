# @openviking/sdk

TypeScript SDK for OpenViking. Two entry points share one package:

| Entry point | Style | Use it when |
|---|---|---|
| `OpenViking` / `Domains` / `Feedback` | Effect 4.1 | you want typed errors, retries, streaming, concurrency and the self-improving retrieval loop |
| `OpenVikingClient` | plain `async`/`await` | you want a dependency-free client with no Effect runtime |

Both speak the same HTTP API, identity headers, response envelope and error codes as the Python and Go SDKs.

```bash
npm install @openviking/sdk effect
```

## Effect SDK

The generated surface covers **every operation the server exposes** — 194 operations across 27 routers, regenerated from the live OpenAPI spec. Every operation is reachable through a domain method — [coverage.test.ts](tests/coverage.spec.ts) fails the build if one is missing. Response types are inferred from live responses for 63 operations — 38 read-only ones against the configured server, and 31 mutating ones against a throwaway sandbox. The rest stay `unknown` rather than guessed.

```ts
import { Effect } from "effect";
import { Domains, openVikingStack } from "@openviking/sdk";

const program = Effect.gen(function* () {
  const ov = yield* Domains;

  const health = yield* ov.system.health();
  const hits = yield* ov.search.find({ query: "deployment guide", limit: 5 });
  const entries = yield* ov.fs.ls({ uri: "viking://user/default/memories" });

  return { health, hits, entries };
});

const result = await Effect.runPromise(
  Effect.provide(program, openVikingStack({ baseUrl: "http://127.0.0.1:1933" })),
);
```

### Typed errors

Every failure is a tagged error, so `catchTag` narrows without string matching:

```ts
import { Effect } from "effect";
import { OpenVikingHttpError, OpenVikingTimeoutError } from "@openviking/sdk";

program.pipe(
  Effect.catchTags({
    OpenVikingHttpError: (error) => Effect.succeed(`http ${error.status}`),
    OpenVikingTimeoutError: (error) => Effect.succeed(`timed out after ${error.timeoutMs}ms`),
  }),
);
```

`OpenVikingTransportError` and `OpenVikingDecodeError` cover the remaining two failure modes. `isRetryable` classifies them; the client already retries transport errors, timeouts, `408`, `429` and `5xx` with jittered exponential backoff.

### Full surface

`ov.call(key, args)` reaches any operation with typed body, query, path and response:

```ts
const task = yield* ov.call("getTaskApiV1TasksTaskIdGet", {
  path: { task_id: "task-123" },
});
```

`endpoints` is the catalog: `{ operationId, method, path, tag, summary }` per key. `EndpointTypes` binds each key to its `body`, `query`, `path` and `response`.

### Streaming

Server-sent events are a `Stream`:

```ts
import { Stream } from "effect";

const events = ov.stream({ method: "POST", path: "/bot/v1/chat/stream", body: { message: "hi" } });
yield* events.pipe(Stream.tap((event) => Effect.log(event.event)), Stream.runDrain);
```

`parseSse` and `parseSseBlock` are exported for tests and custom transports.

### Prompt-ready context

`assembleContext` turns a query into a token-budgeted block with citations,
using the server's own recall renderer:

```ts
import { assembleContext } from "@openviking/sdk";

const block = yield* assembleContext(domains, {
  query: "deployment guide",
  maxTokens: 2_000,
  purpose: "coding",
});
// block.text, block.citations, block.tokensUsed, block.truncated
```

Oversized output is cut on a line boundary and marked with `…[truncated]`;
citations are deduplicated and ordered by score.

### Self-improving retrieval

`Feedback` closes the loop between what retrieval returned and what the caller actually used. It keeps an exponential moving average of hit rate and mean score, then adapts `limit` and `minScore` per query:

```ts
import { Feedback } from "@openviking/sdk";

const program = Effect.gen(function* () {
  const feedback = yield* Feedback;

  const result = yield* feedback.find("deployment guide");
  const first = result.memories[0];
  if (first) yield* feedback.markUsed(first.uri);

  return yield* feedback.stats();
});
```

Nothing leaves the machine. The policy can also survive a process restart:

```ts
import { feedbackFile } from "@openviking/sdk";

const store = feedbackFile({ path: "~/.openviking/sdk-feedback.jsonl" });

openVikingStack({ baseUrl }, { load: store.load, persist: store.persist });
```

`load` seeds the moving averages from the file; unreadable or malformed lines
are skipped rather than fatal, and a failed write never breaks a request.

### Caching and concurrency

GET responses are cached in memory with a TTL, and every call passes through a
bounded semaphore so a fan-out cannot exhaust the server:

```ts
openVikingStack({
  baseUrl: "http://127.0.0.1:1933",
  concurrency: 8,
  cache: { ttlMs: 5_000, capacity: 256 },
});
```

`cache: false` disables it. `ov.cache?.stats()` reports hits, misses and size;
`invalidate(key)` and `invalidateAll()` drop entries after a write.

### Local metrics

Every call is timed and counted per operation. Nothing is exported; the counters
live in the process:

```ts
const snapshot = yield* ov.metrics.snapshot();
// { totalCalls: 42, totalErrors: 0,
//   endpoints: { health_check_health_get: { calls: 3, p50Ms: 4, p95Ms: 9, ... } } }
```

`metrics: false` disables collection, `samplesPerEndpoint` bounds the latency
reservoir (default 256). Raw calls are tracked under `raw:<METHOD> <path>`.

A `sink` receives every sample, so you can persist them without a collector:

```ts
import { jsonlSink } from "@openviking/sdk";

openVikingStack({
  baseUrl: "http://127.0.0.1:1933",
  metrics: { sink: jsonlSink({ path: "~/.openviking/sdk-metrics.jsonl" }) },
});
```

The sink never throws into a request path — a broken file just drops samples.

### Layers

| Layer | Provides | Depends on |
|---|---|---|
| `openVikingLayer(config)` | `OpenViking` | — |
| `domainsLayer` | `Domains` | `OpenViking` |
| `feedbackLayer(options)` | `Feedback` | `Domains` |
| `openVikingStack(config, feedback)` | all three | — |

`makeCache(options)` and `makeMetrics(options)` are exported on their own for
callers that want the same behaviour outside the client.

## Plain client

```ts
import { OpenVikingClient } from "@openviking/sdk";

const client = new OpenVikingClient({
  baseUrl: "http://127.0.0.1:1933",
  apiKey: "your-key",
});

const results = await client.search("deployment guide", {
  targetUri: "viking://resources",
  limit: 10,
});
```

The plain client follows the same HTTP API, identity headers, response envelope and error codes as `openviking-sdk` for Python and the Go SDK. It supports resources and skills, MCP servers and live context, filesystem/content operations, retrieval, sessions, OVPack files, snapshots, tasks, watches, observer status and tenant administration.

Existing local file paths are uploaded automatically, and local directories are zipped before upload. Other strings are sent to the server as URLs or server-side paths.

To ingest content without VLM semantic understanding, pass `processingMode: "vectors_only"` to `addResource`. This writes or syncs the resource tree and vectorizes current files, but does not generate or refresh `.abstract.md` / `.overview.md`.

```ts
const task = await client.addResource("./docs/guide.md", {
  processingMode: "vectors_only",
});
```

## Regenerating the surface

Batch tag operations use a dedicated POST endpoint:

```ts
const attrs = await client.attrs("viking://resources/docs/a.md");
const updated = await client.batchSetTags(
  ["viking://resources/docs/a.md", "viking://resources/docs/b.md"],
  ["used_count+=1"],
  { mode: "append" },
);
```

Event-memory tags can be configured as session defaults, updated later, or overridden per commit. Passing `[]` to `commitSession` explicitly skips the session defaults for that commit.
The generated files are checked in. Refresh them whenever the server's API changes:

```bash
npm run generate                       # reads http://127.0.0.1:1933/openapi.json
python3 scripts/generate.py --spec /tmp/openapi.json
```

Mutation responses come from a disposable server so nothing touches your data:

```bash
just sdk-generate-mutations            # two passes, then regenerate mutations.ts
just sdk-sandbox-up                    # or drive it by hand on 127.0.0.1:1944
just sdk-sandbox-down                  # stop it and wipe the workspace
```

`sdk-generate-mutations` runs two passes because the sandbox cannot be both at
once: dev mode reaches account-scoped endpoints, and a generated `root_api_key`
reaches the admin ones. Only endpoints the sandbox actually answers get a type.

A probe that the server rejects (403 without a root key, 400 on invalid input)
is skipped and stays `unknown`; the generator never invents a shape.

## Development

```bash
npm install
npm run typecheck
npm test
npm run build
```

Tests that need a server skip themselves when `http://127.0.0.1:1933/health` is unreachable. Point them elsewhere with `OV_URL`.

The lifecycle suite needs a disposable server and drives real mutations
(mkdir, write, read, cp, mv, rm, session create/append/commit/delete):

```bash
just sdk-test-sandbox     # starts the sandbox, runs everything, tears it down
```

Point it elsewhere with `OV_SANDBOX_URL`. It refuses to run against a server
whose `auth_mode` is not `dev`, so it cannot touch a live workspace.
