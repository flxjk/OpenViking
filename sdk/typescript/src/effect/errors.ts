import * as Schema from "effect/Schema";

export class OpenVikingHttpError extends Schema.TaggedError<OpenVikingHttpError>()(
  "OpenVikingHttpError",
  {
    status: Schema.Number,
    method: Schema.String,
    path: Schema.String,
    body: Schema.Unknown,
    requestId: Schema.optional(Schema.String),
  },
) {}

export class OpenVikingTransportError extends Schema.TaggedError<OpenVikingTransportError>()(
  "OpenVikingTransportError",
  {
    method: Schema.String,
    path: Schema.String,
    cause: Schema.Unknown,
  },
) {}

export class OpenVikingDecodeError extends Schema.TaggedError<OpenVikingDecodeError>()(
  "OpenVikingDecodeError",
  {
    path: Schema.String,
    cause: Schema.Unknown,
  },
) {}

export class OpenVikingTimeoutError extends Schema.TaggedError<OpenVikingTimeoutError>()(
  "OpenVikingTimeoutError",
  {
    method: Schema.String,
    path: Schema.String,
    timeoutMs: Schema.Number,
  },
) {}

export type OpenVikingError =
  | OpenVikingHttpError
  | OpenVikingTransportError
  | OpenVikingDecodeError
  | OpenVikingTimeoutError;

export const isRetryable = (error: OpenVikingError): boolean => {
  switch (error._tag) {
    case "OpenVikingTransportError":
    case "OpenVikingTimeoutError":
      return true;
    case "OpenVikingHttpError":
      return error.status === 408 || error.status === 429 || error.status >= 500;
    default:
      return false;
  }
};

export const describeError = (error: OpenVikingError): string => {
  switch (error._tag) {
    case "OpenVikingHttpError":
      return `${error.method} ${error.path} failed with ${error.status}`;
    case "OpenVikingTransportError":
      return `${error.method} ${error.path} could not reach the server`;
    case "OpenVikingDecodeError":
      return `response from ${error.path} did not match the expected shape`;
    case "OpenVikingTimeoutError":
      return `${error.method} ${error.path} exceeded ${error.timeoutMs}ms`;
  }
};
