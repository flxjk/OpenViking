import assert from 'node:assert/strict';
import test from 'node:test';
import { createReporter } from '../src/shared/analytics/reporter.ts';

test('a configured analytics host receives no events or identifiers', () => {
  const instance = { current: undefined };
  const reporter = createReporter({
    onEvent: () => assert.fail('host received analytics'),
  }, instance);

  assert.equal(reporter.enabled('viking_bot_message_submit'), false);
  reporter.emit('viking_bot_message_submit', () => assert.fail('event was collected'));
  assert.equal(instance.current, undefined);
});
