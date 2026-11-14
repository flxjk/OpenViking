// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

export { OpenVikingClient, normalizeURI } from "./client.js";
export { OpenVikingError, isOpenVikingError } from "./errors.js";
export type * from "./types.js";

export {
  OpenViking,
  layer as openVikingLayer,
  layerWith as openVikingLayerWith,
  make as makeOpenViking,
  type CallArgs,
  type CallEnvelopeResult,
  type OpenVikingShape,
} from "./effect/client.js";
export {
  makeMetrics,
  type EndpointMetrics,
  type MetricsOptions,
  type MetricsSample,
  type MetricsShape,
  type MetricsSnapshot,
} from "./effect/metrics.js";
export { jsonlSink, type JsonlSinkOptions } from "./effect/metrics-node.js";
export { feedbackFile } from "./effect/feedback-node.js";
export {
  makeCache,
  type CacheOptions,
  type CacheShape,
  type CacheStats,
} from "./effect/cache.js";
export {
  DEFAULT_BASE_URL,
  DEFAULT_CONCURRENCY,
  DEFAULT_RETRIES,
  DEFAULT_TIMEOUT_MS,
  identityHeaders,
  makeConfig,
  type OpenVikingConfig,
} from "./effect/config.js";
export {
  Domains,
  layer as domainsLayer,
  makeDomains,
  type AclDomain,
  type AdminDomain,
  type BotDomain,
  type CompileDomain,
  type ConsoleDomain,
  type ContentDomain,
  type DomainsShape,
  type FsDomain,
  type McpsDomain,
  type ObserverDomain,
  type PackDomain,
  type PrivacyDomain,
  type RulesDomain,
  type SearchDomain,
  type SessionsDomain,
  type SkillsDomain,
  type SnapshotDomain,
  type StatsDomain,
  type SystemDomain,
  type TasksDomain,
  type WatchesDomain,
} from "./effect/domains.js";
export {
  OpenVikingDecodeError,
  OpenVikingHttpError,
  OpenVikingTimeoutError,
  OpenVikingTransportError,
  describeError,
  isRetryable,
  type OpenVikingError as OpenVikingFailure,
} from "./effect/errors.js";
export { buildUrl, unwrap, type Envelope, type RequestOptions } from "./effect/http.js";
export {
  assembleContext,
  CHARS_PER_TOKEN,
  DEFAULT_MAX_TOKENS,
  TRUNCATION_MARKER,
  type AssembleOptions,
  type ContextBlock,
  type ContextEntry,
} from "./effect/context.js";
export type * from "./effect/responses.js";
export {
  Feedback,
  layer as feedbackLayer,
  makeFeedback,
  type AdaptivePolicy,
  type FeedbackOptions,
  type FeedbackRecord,
  type FeedbackShape,
  type FeedbackStats,
} from "./effect/self-improve.js";
export { stack as openVikingStack } from "./effect/stack.js";
export {
  collectSse,
  parseSse,
  parseSseBlock,
  stream as sseStream,
  type ServerSentEvent,
} from "./effect/stream.js";
export {
  endpoints,
  type EndpointBody,
  type EndpointDescriptor,
  type EndpointKey,
  type EndpointPath,
  type EndpointQuery,
  type EndpointResponse,
  type EndpointTypes,
  type HttpMethod,
} from "./generated/endpoints.js";
export * as schemas from "./generated/schemas.js";
