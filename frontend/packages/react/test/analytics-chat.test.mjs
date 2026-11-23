import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createReporter } from '../src/shared/analytics/reporter.ts';
const { createChatController } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-chat.js'))
);

for (const complete of [true, false]) {
  test(`reply ${complete ? 'completion' : 'truncation'} works with an opted-in analytics receiver without sending events`, async () => {
    const events = [];
    const reporter = createReporter(
      { onEvent: (event) => events.push(event) },
      { current: undefined },
    );
    const service = {
      createSession: async () => ({ id: 'private-session' }),
      loadMessages: async () => [],
      async *stream() {
        yield { type: 'text', text: 'private reply', delta: true };
        if (complete) yield { type: 'complete' };
      },
    };
    const controller = createChatController(
      service,
      { persist: false },
      undefined,
      { current: reporter },
    );
    const cleanup = controller.start();
    try {
      await controller.send('private input');
      assert.deepEqual(events, []);
    } finally {
      cleanup();
    }
  });
}
