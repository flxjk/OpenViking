import * as Effect from "effect/Effect";
import * as Stream from "effect/Stream";
import { HttpClient, HttpClientResponse } from "effect/http";

import { identityHeaders, type OpenVikingConfig } from "./config.js";
import {
  OpenVikingHttpError,
  OpenVikingTransportError,
  type OpenVikingError,
} from "./errors.js";
import { makeRequest, type RequestOptions } from "./http.js";

export interface ServerSentEvent {
  readonly event: string;
  readonly data: string;
  readonly id?: string;
  readonly retry?: number;
}

export const parseSseBlock = (block: string): ServerSentEvent | undefined => {
  const lines = block.split("\n");
  let event = "message";
  let id: string | undefined;
  let retry: number | undefined;
  const data: string[] = [];

  for (const line of lines) {
    if (line === "" || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const raw = colon === -1 ? "" : line.slice(colon + 1);
    const value = raw.startsWith(" ") ? raw.slice(1) : raw;
    switch (field) {
      case "event":
        event = value;
        break;
      case "data":
        data.push(value);
        break;
      case "id":
        id = value;
        break;
      case "retry": {
        const parsed = Number.parseInt(value, 10);
        if (Number.isFinite(parsed)) retry = parsed;
        break;
      }
      default:
        break;
    }
  }

  if (data.length === 0 && event === "message") return undefined;
  return {
    event,
    data: data.join("\n"),
    ...(id === undefined ? {} : { id }),
    ...(retry === undefined ? {} : { retry }),
  };
};

export const parseSse = (text: string): ServerSentEvent[] => {
  const events: ServerSentEvent[] = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    const parsed = parseSseBlock(block);
    if (parsed) events.push(parsed);
  }
  return events;
};

const openStream = (
  client: HttpClient.HttpClient,
  config: OpenVikingConfig,
  options: RequestOptions,
): Effect.Effect<
  Stream.Stream<ServerSentEvent, OpenVikingError>,
  OpenVikingError
> =>
  Effect.gen(function* () {
    const request = yield* makeRequest(config, {
      ...options,
      headers: {
        accept: "text/event-stream",
        ...identityHeaders(config),
        ...options.headers,
      },
    });

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

    if (response.status >= 400) {
      return yield* Effect.fail(
        new OpenVikingHttpError({
          status: response.status,
          method: options.method,
          path: options.path,
          body: undefined,
        }),
      );
    }

    const decoded: Stream.Stream<ServerSentEvent, OpenVikingError> =
      response.stream.pipe(
        Stream.decodeText(),
        Stream.mapError(
          (cause) =>
            new OpenVikingTransportError({
              method: options.method,
              path: options.path,
              cause,
            }),
        ),
        Stream.mapAccum(
          () => "",
          (buffer: string, chunk: string) => {
            const combined = buffer + chunk;
            const blocks = combined.split(/\r?\n\r?\n/);
            const rest = blocks.pop() ?? "";
            const events: ServerSentEvent[] = [];
            for (const block of blocks) {
              const parsed = parseSseBlock(block);
              if (parsed) events.push(parsed);
            }
            return [rest, events] as const;
          },
        ),
      );

    return decoded;
  });

export const stream = (
  client: HttpClient.HttpClient,
  config: OpenVikingConfig,
  options: RequestOptions,
): Stream.Stream<ServerSentEvent, OpenVikingError> =>
  Stream.unwrap(openStream(client, config, options));

export const collectSse = (
  client: HttpClient.HttpClient,
  config: OpenVikingConfig,
  options: RequestOptions,
): Effect.Effect<ServerSentEvent[], OpenVikingError> =>
  stream(client, config, options).pipe(
    Stream.runCollect,
    Effect.map((chunk) => Array.from(chunk)),
  );

export const responseStream = (
  response: HttpClientResponse.HttpClientResponse,
): Stream.Stream<string, OpenVikingError> =>
  response.stream.pipe(
    Stream.decodeText(),
    Stream.mapError(
      (cause) =>
        new OpenVikingTransportError({ method: "GET", path: "", cause }),
    ),
  );
