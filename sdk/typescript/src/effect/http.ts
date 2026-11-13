import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";
import { HttpClient, HttpClientRequest, HttpClientResponse } from "effect/http";

import type { HttpMethod } from "../generated/endpoints.js";
import { identityHeaders, type OpenVikingConfig } from "./config.js";
import {
  OpenVikingDecodeError,
  OpenVikingHttpError,
  OpenVikingTimeoutError,
  OpenVikingTransportError,
  isRetryable,
  type OpenVikingError,
} from "./errors.js";

export interface Envelope<T> {
  readonly status: string;
  readonly result: T;
  readonly error: unknown;
  readonly telemetry: unknown;
  readonly profile: unknown;
}

export interface RequestOptions {
  readonly method: HttpMethod;
  readonly path: string;
  readonly query?: Readonly<Record<string, unknown>> | undefined;
  readonly body?: unknown;
  readonly headers?: Readonly<Record<string, string>> | undefined;
  readonly timeoutMs?: number | undefined;
  readonly retries?: number | undefined;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export const buildUrl = (
  baseUrl: string,
  path: string,
  query?: Readonly<Record<string, unknown>> | undefined,
): string => {
  const url = new URL(path, baseUrl);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue;
      if (Array.isArray(value)) {
        for (const item of value) url.searchParams.append(key, String(item));
      } else {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
};

export const unwrap = <T>(body: unknown): T => {
  if (isRecord(body) && "result" in body && "status" in body) {
    return body["result"] as T;
  }
  return body as T;
};

const backoffMs = (attempt: number): number => {
  const base = Math.min(2_000, 100 * 2 ** attempt);
  return Math.round(base * (0.5 + Math.random() * 0.5));
};

const runOnce = (
  client: HttpClient.HttpClient,
  config: OpenVikingConfig,
  options: RequestOptions,
): Effect.Effect<unknown, OpenVikingError> =>
  Effect.gen(function* () {
    const url = buildUrl(config.baseUrl, options.path, options.query);
    const headers = { ...identityHeaders(config), ...options.headers };

    let request = HttpClientRequest.make(options.method)(url);
    request = HttpClientRequest.setHeaders(request, headers);
    if (options.body !== undefined) {
      request = yield* HttpClientRequest.bodyJson(request, options.body).pipe(
        Effect.catch((cause) =>
          Effect.fail(new OpenVikingDecodeError({ path: options.path, cause })),
        ),
      );
    }

    const response = yield* client.execute(request).pipe(
      Effect.catch((cause) =>
        Effect.fail(
          new OpenVikingTransportError({
            method: options.method,
            path: options.path,
            cause,
          }),
        ),
      ),
    );

    if (response.status === 204) return undefined;

    const decoded = yield* HttpClientResponse.schemaBodyJson(Schema.Unknown)(response).pipe(
      Effect.catch((cause) =>
        Effect.fail(new OpenVikingDecodeError({ path: options.path, cause })),
      ),
    );

    if (response.status >= 400) {
      const requestId =
        isRecord(decoded) && typeof decoded["request_id"] === "string"
          ? decoded["request_id"]
          : undefined;
      return yield* Effect.fail(
        new OpenVikingHttpError({
          status: response.status,
          method: options.method,
          path: options.path,
          body: decoded,
          ...(requestId === undefined ? {} : { requestId }),
        }),
      );
    }

    return decoded;
  });

const withRetry = (
  effect: Effect.Effect<unknown, OpenVikingError>,
  retries: number,
): Effect.Effect<unknown, OpenVikingError> => {
  const loop = (attempt: number): Effect.Effect<unknown, OpenVikingError> =>
    effect.pipe(
      Effect.catch((error) => {
        if (attempt >= retries || !isRetryable(error)) return Effect.fail(error);
        return Effect.sleep(backoffMs(attempt)).pipe(Effect.andThen(loop(attempt + 1)));
      }),
    );
  return loop(0);
};

export const execute = (
  client: HttpClient.HttpClient,
  config: OpenVikingConfig,
  options: RequestOptions,
): Effect.Effect<unknown, OpenVikingError> => {
  const timeoutMs = options.timeoutMs ?? config.timeoutMs ?? 30_000;
  const retries = options.retries ?? config.retries ?? 3;

  const timed = runOnce(client, config, options).pipe(
    Effect.timeout(timeoutMs),
    Effect.catch((error) =>
      error._tag === "TimeoutError"
        ? Effect.fail(
            new OpenVikingTimeoutError({
              method: options.method,
              path: options.path,
              timeoutMs,
            }),
          )
        : Effect.fail(error as OpenVikingError),
    ),
  );

  return withRetry(timed, retries);
};
