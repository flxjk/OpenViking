import * as Context from "effect/Context";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as Semaphore from "effect/Semaphore";
import * as Stream from "effect/Stream";
import { FetchHttpClient, HttpClient } from "effect/http";

import {
  endpoints,
  type EndpointBody,
  type EndpointKey,
  type EndpointPath,
  type EndpointQuery,
  type EndpointResponse,
} from "../generated/endpoints.js";
import { makeCache, type CacheShape } from "./cache.js";
import { makeConfig, type OpenVikingConfig } from "./config.js";
import { makeMetrics, type MetricsShape } from "./metrics.js";
import { OpenVikingDecodeError, type OpenVikingError } from "./errors.js";
import { execute, unwrap, type Envelope, type RequestOptions } from "./http.js";
import { stream as streamSse, type ServerSentEvent } from "./stream.js";

export interface CallArgs<K extends EndpointKey> {
  readonly path?: EndpointPath<K> | undefined;
  readonly query?: EndpointQuery<K> | undefined;
  readonly body?: EndpointBody<K> | undefined;
  readonly headers?: Readonly<Record<string, string>> | undefined;
  readonly timeoutMs?: number | undefined;
  readonly retries?: number | undefined;
}

export interface OpenVikingShape {
  readonly config: OpenVikingConfig;
  readonly call: <K extends EndpointKey>(
    key: K,
    args?: CallArgs<K>,
  ) => Effect.Effect<EndpointResponse<K>, OpenVikingError>;
  readonly callEnvelope: <K extends EndpointKey>(
    key: K,
    args?: CallArgs<K>,
  ) => Effect.Effect<Envelope<EndpointResponse<K>>, OpenVikingError>;
  readonly raw: (options: RequestOptions) => Effect.Effect<unknown, OpenVikingError>;
  readonly stream: (options: RequestOptions) => Stream.Stream<ServerSentEvent, OpenVikingError>;
  readonly streamCall: <K extends EndpointKey>(
    key: K,
    args?: CallArgs<K>,
  ) => Stream.Stream<ServerSentEvent, OpenVikingError>;
  readonly cache: CacheShape | undefined;
  readonly metrics: MetricsShape | undefined;
}

export class OpenViking extends Context.Service<OpenViking, OpenVikingShape>()("OpenViking") {}

const fillPath = (
  template: string,
  params: Readonly<Record<string, unknown>> | undefined,
): Effect.Effect<string, OpenVikingDecodeError> => {
  const names = [...template.matchAll(/\{([^}]+)\}/g)].map((match) => match[1] as string);
  for (const name of names) {
    if (params?.[name] === undefined) {
      return Effect.fail(
        new OpenVikingDecodeError({
          path: template,
          cause: `missing path parameter: ${name}`,
        }),
      );
    }
  }
  return Effect.succeed(
    template.replace(/\{([^}]+)\}/g, (_, name: string) =>
      encodeURIComponent(String(params?.[name])),
    ),
  );
};

export const make = (
  client: HttpClient.HttpClient,
  overrides: Partial<OpenVikingConfig> = {},
): OpenVikingShape => {
  const config = makeConfig(overrides);
  const semaphore = Semaphore.makeUnsafe(config.concurrency ?? 8);
  const cache: CacheShape | undefined =
    config.cache === false ? undefined : makeCache(config.cache ?? {});
  const metrics: MetricsShape | undefined =
    config.metrics === false ? undefined : makeMetrics(config.metrics ?? {});

  const callEnvelope = <K extends EndpointKey>(
    key: K,
    args?: CallArgs<K>,
  ): Effect.Effect<Envelope<EndpointResponse<K>>, OpenVikingError> =>
    Effect.gen(function* () {
      const descriptor = endpoints[key];
      const path = yield* fillPath(
        descriptor.path,
        args?.path as Readonly<Record<string, unknown>> | undefined,
      );
      const options: RequestOptions = {
        method: descriptor.method,
        path,
        ...(args?.query === undefined
          ? {}
          : { query: args.query as Readonly<Record<string, unknown>> }),
        ...(args?.body === undefined ? {} : { body: args.body }),
        ...(args?.headers === undefined ? {} : { headers: args.headers }),
        ...(args?.timeoutMs === undefined ? {} : { timeoutMs: args.timeoutMs }),
        ...(args?.retries === undefined ? {} : { retries: args.retries }),
      };
      const run = semaphore.withPermit(execute(client, config, options));
      const cached =
        cache !== undefined && descriptor.method === "GET"
          ? cache.get(
              `${descriptor.method} ${path} ${JSON.stringify(args?.query ?? {})}`,
              run,
            )
          : run;
      const tracked =
        metrics === undefined
          ? cached
          : metrics.track(descriptor.operationId, cached);
      const body = yield* tracked;
      return body as Envelope<EndpointResponse<K>>;
    });

  const call = <K extends EndpointKey>(
    key: K,
    args?: CallArgs<K>,
  ): Effect.Effect<EndpointResponse<K>, OpenVikingError> =>
    callEnvelope(key, args).pipe(Effect.map((body) => unwrap<EndpointResponse<K>>(body)));

  const raw = (options: RequestOptions): Effect.Effect<unknown, OpenVikingError> => {
    const run = semaphore.withPermit(execute(client, config, options));
    return metrics === undefined
      ? run
      : metrics.track(`raw:${options.method} ${options.path}`, run);
  };

  const stream = (options: RequestOptions): Stream.Stream<ServerSentEvent, OpenVikingError> =>
    streamSse(client, config, options);

  const streamCall = <K extends EndpointKey>(
    key: K,
    args?: CallArgs<K>,
  ): Stream.Stream<ServerSentEvent, OpenVikingError> =>
    Stream.unwrap(
      Effect.gen(function* () {
        const descriptor = endpoints[key];
        const path = yield* fillPath(
          descriptor.path,
          args?.path as Readonly<Record<string, unknown>> | undefined,
        );
        const options: RequestOptions = {
          method: descriptor.method,
          path,
          ...(args?.query === undefined
            ? {}
            : { query: args.query as Readonly<Record<string, unknown>> }),
          ...(args?.body === undefined ? {} : { body: args.body }),
          ...(args?.headers === undefined ? {} : { headers: args.headers }),
        };
        return streamSse(client, config, options);
      }),
    );

  return { config, call, callEnvelope, raw, stream, streamCall, cache, metrics };
};

export const layer = (overrides: Partial<OpenVikingConfig> = {}) =>
  Layer.effect(
    OpenViking,
    Effect.gen(function* () {
      const client = yield* HttpClient.HttpClient;
      return make(client, overrides);
    }),
  ).pipe(Layer.provide(FetchHttpClient.layer));

export const layerWith = (config: OpenVikingConfig) => layer(config);
