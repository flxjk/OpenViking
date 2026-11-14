import { expect, it, vi } from "vitest";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import { FetchHttpClient } from "effect/http";
import { OpenVikingClient } from "../src/client.js";
import { layer as openVikingLayer } from "../src/effect/client.js";
import { Domains, layer as domainsLayer } from "../src/effect/domains.js";

it("sends the batch tags contract through both SDK entry points", async () => {
  const response = {
    items: [{ uri: "viking://resources/note.md" }],
    failed: [],
  };
  const fetcher = vi.fn<typeof fetch>().mockImplementation(
    async () =>
      new Response(JSON.stringify({ status: "ok", result: response }), {
        headers: { "Content-Type": "application/json" },
      }),
  );
  const plain = new OpenVikingClient({
    baseUrl: "https://example.test",
    fetch: fetcher,
  });
  await expect(
    plain.batchSetTags(["resources/note.md"], ["team=docs"], {
      mode: "append",
      recursive: true,
    }),
  ).resolves.toEqual(response);

  const base = openVikingLayer({ baseUrl: "https://example.test", retries: 0 });
  const stack = domainsLayer.pipe(Layer.provide(base));
  const program = Effect.gen(function* () {
    const domains = yield* Domains;
    return yield* domains.content.batchSetTags({
      uris: ["viking://resources/note.md"],
      tags: ["team=docs"],
      mode: "append",
      recursive: true,
    });
  });
  const result = await Effect.runPromise(
    program.pipe(
      Effect.provide(stack),
      Effect.provideService(FetchHttpClient.Fetch, fetcher),
    ),
  );
  expect(result).toEqual(response);
  expect(fetcher).toHaveBeenCalledTimes(2);
  for (const [url, init] of fetcher.mock.calls) {
    expect(String(url)).toBe(
      "https://example.test/api/v1/content/batch_set_tags",
    );
    expect(init?.method).toBe("POST");
    const body =
      typeof init?.body === "string"
        ? init.body
        : new TextDecoder().decode(init?.body as Uint8Array);
    expect(JSON.parse(body)).toEqual({
      uris: ["viking://resources/note.md"],
      tags: ["team=docs"],
      mode: "append",
      recursive: true,
    });
  }
});
