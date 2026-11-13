import * as Layer from "effect/Layer";

import { layer as openVikingLayer } from "./client.js";
import type { OpenVikingConfig } from "./config.js";
import { layer as domainsLayer } from "./domains.js";
import { layer as feedbackLayer, type FeedbackOptions } from "./self-improve.js";

export const stack = (
  config: Partial<OpenVikingConfig> = {},
  feedback: FeedbackOptions = {},
) => {
  const base = openVikingLayer(config);
  const domains = domainsLayer.pipe(Layer.provide(base));
  const feedbackLayerResolved = feedbackLayer(feedback).pipe(Layer.provide(domains));
  return Layer.mergeAll(base, domains, feedbackLayerResolved);
};
