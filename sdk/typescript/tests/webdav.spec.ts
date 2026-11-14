import { expect, it, vi } from "vitest";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import { FetchHttpClient } from "effect/http";
import { OpenViking, layer as clientLayer } from "../src/effect/client.js";
import { Domains, layer as domainsLayer } from "../src/effect/domains.js";
import type { OpenVikingError } from "../src/effect/errors.js";
import { endpoints } from "../src/generated/endpoints.js";

const setup = (fetcher: typeof fetch) => {
  const base = clientLayer({ baseUrl: "https://example.test", retries: 0 });
  const stack = Layer.mergeAll(base, domainsLayer.pipe(Layer.provide(base)));
  return <A>(
    program: Effect.Effect<A, OpenVikingError, Domains | OpenViking>,
  ) =>
    Effect.runPromise(
      program.pipe(
        Effect.provide(stack),
        Effect.provideService(FetchHttpClient.Fetch, fetcher),
      ),
    );
};

it("gives shared GET/HEAD server IDs distinct stable callable names", () => {
  const all = Object.values(endpoints);
  expect(all).toHaveLength(205);
  expect(new Set(all.map(({ method, path }) => `${method} ${path}`)).size).toBe(
    205,
  );
  expect(new Set(all.map(({ operationId }) => operationId)).size).toBe(205);
  expect(endpoints.getOrHeadWebdavResourcesGet.method).toBe("GET");
  expect(endpoints.getOrHeadWebdavResourcesHead.method).toBe("HEAD");
  expect(endpoints.getOrHeadWebdavResourcesResourcePathGet.method).toBe("GET");
  expect(endpoints.getOrHeadWebdavResourcesResourcePathHead.method).toBe(
    "HEAD",
  );
});

it.each(["root", "path"] as const)(
  "supports every WebDAV verb through the %s route",
  async (route) => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (_url, init) => {
        switch (init?.method) {
          case "GET":
            return new Response("literal file body", {
              headers: { "Content-Type": "text/plain" },
            });
          case "PROPFIND":
            return new Response("<multistatus />", {
              status: 207,
              headers: { "Content-Type": "application/xml" },
            });
          case "PUT":
          case "MKCOL":
            return new Response(null, { status: 201 });
          case "DELETE":
          case "MOVE":
            return new Response(null, { status: 204 });
          default:
            return new Response(null, {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
        }
      });
    const run = setup(fetcher);
    const results = await run(
      Effect.gen(function* () {
        const d = (yield* Domains).webdav;
        const query = { resource_path: "folder/note.txt" };
        if (route === "root")
          return [
            yield* d.get(query),
            yield* d.head(query),
            yield* d.options(query),
            yield* d.propfind(query, { Depth: "0" }),
            yield* d.put(query, 'raw "file" body', {
              "Content-Type": "text/plain",
            }),
            yield* d.mkcol(query),
            yield* d.move(query, "/webdav/resources/moved.txt", false),
            yield* d.remove(query),
          ];
        return [
          yield* d.getPath("folder/note.txt"),
          yield* d.headPath("folder/note.txt"),
          yield* d.optionsPath("folder/note.txt"),
          yield* d.propfindPath("folder/note.txt", { Depth: "0" }),
          yield* d.putPath("folder/note.txt", 'raw "file" body', {
            "Content-Type": "text/plain",
          }),
          yield* d.mkcolPath("folder/note.txt"),
          yield* d.movePath(
            "folder/note.txt",
            "/webdav/resources/moved.txt",
            false,
          ),
          yield* d.removePath("folder/note.txt"),
        ];
      }),
    );
    expect(results).toEqual([
      "literal file body",
      undefined,
      undefined,
      "<multistatus />",
      undefined,
      undefined,
      undefined,
      undefined,
    ]);
    expect(fetcher.mock.calls.map(([, init]) => init?.method)).toEqual([
      "GET",
      "HEAD",
      "OPTIONS",
      "PROPFIND",
      "PUT",
      "MKCOL",
      "MOVE",
      "DELETE",
    ]);
    for (const [url, init] of fetcher.mock.calls) {
      const address = new URL(String(url));
      expect(address.pathname).toBe(
        route === "root"
          ? "/webdav/resources"
          : "/webdav/resources/folder%2Fnote.txt",
      );
      if (route === "root")
        expect(address.searchParams.get("resource_path")).toBe(
          "folder/note.txt",
        );
      if (init?.method === "PUT") {
        expect(new TextDecoder().decode(init.body as Uint8Array)).toBe(
          'raw "file" body',
        );
        expect(new Headers(init.headers).get("content-type")).toBe(
          "text/plain",
        );
      }
      if (init?.method === "MOVE") {
        expect(new Headers(init.headers).get("Destination")).toBe(
          "/webdav/resources/moved.txt",
        );
        expect(new Headers(init.headers).get("Overwrite")).toBe("F");
      }
    }
  },
);

it("never parses a HEAD response body and preserves HTTP errors", async () => {
  const ok = new Response("invalid JSON", {
    headers: { "Content-Type": "application/json" },
  });
  const missing = new Response(null, {
    status: 404,
    headers: { "Content-Type": "application/json" },
  });
  const read = vi.spyOn(ok, "text");
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(ok)
    .mockResolvedValueOnce(missing);
  const run = setup(fetcher);
  await expect(
    run(
      Effect.gen(function* () {
        return yield* (yield* OpenViking).call("getOrHeadWebdavResourcesHead", {
          query: { resource_path: "note.txt" },
        });
      }),
    ),
  ).resolves.toBeUndefined();
  expect(read).not.toHaveBeenCalled();
  const error = await run(
    Effect.gen(function* () {
      return yield* (yield* OpenViking)
        .raw({ method: "HEAD", path: "/webdav/resources/missing" })
        .pipe(
          Effect.match({
            onFailure: (failure) => failure,
            onSuccess: () => undefined,
          }),
        );
    }),
  );
  expect(error).toMatchObject({
    _tag: "OpenVikingHttpError",
    status: 404,
    method: "HEAD",
  });
});

it("preserves binary file bytes and sends raw Uint8Array PUT bodies", async () => {
  const bytes = new Uint8Array([0, 255, 127, 128]);
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      new Response(bytes, {
        headers: { "Content-Type": "application/octet-stream" },
      }),
    )
    .mockResolvedValueOnce(new Response(null, { status: 201 }));
  const run = setup(fetcher);
  const result = await run(
    Effect.gen(function* () {
      const d = (yield* Domains).webdav;
      const body = yield* d.getPath("file.bin");
      yield* d.putPath("copy.bin", bytes);
      return body;
    }),
  );
  expect(result).toBeInstanceOf(Uint8Array);
  expect(result).toEqual(bytes);
  expect(fetcher.mock.calls[1]?.[1]?.body).toEqual(bytes);
});

it.each(["text/plain", "application/octet-stream"])(
  "preserves empty %s files",
  async (contentType) => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(null, { headers: { "Content-Type": contentType } }),
      );
    const run = setup(fetcher);
    const result = await run(
      Effect.gen(function* () {
        return yield* (yield* Domains).webdav.getPath("empty-file");
      }),
    );
    expect(result).toEqual(
      contentType === "text/plain" ? "" : new Uint8Array(),
    );
  },
);
