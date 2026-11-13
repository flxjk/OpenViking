# Examples

Runnable against a live server. Each imports the built bundle, so run
`npm run build` first (or `just sdk-build`).

| Example | What it shows |
|---|---|
| [01-connect.mjs](01-connect.mjs) | health, status, task list, operation count |
| [02-memory.mjs](02-memory.mjs) | adaptive recall: the policy before and after a used result |
| [03-observe.mjs](03-observe.mjs) | cache hit/miss counters and per-operation latency percentiles |
| [04-agent.mjs](04-agent.mjs) | recall → gateway chat → cite → feed the citation back |
| [05-context.mjs](05-context.mjs) | prompt-ready context block assembled under a token budget |
| [06-feedback.mjs](06-feedback.mjs) | the adaptive policy surviving a process restart |

```bash
node examples/01-connect.mjs
node examples/02-memory.mjs "local AI stack"
node examples/03-observe.mjs
node examples/04-agent.mjs "what is my local AI stack?"
node examples/05-context.mjs "local AI stack"
node examples/06-feedback.mjs
node examples/06-feedback.mjs
```

## Environment

| Variable | Default | Used by |
|---|---|---|
| `OV_URL` | `http://127.0.0.1:1933` | all |
| `OV_GW_KEY` | `~/.openviking/gateway.key` | 04 |
| `OV_GW_MODEL` | `deepseek/deepseek-chat` | 04 |
| `OV_MAX_TOKENS` | `1200` | 05 |
| `OV_FEEDBACK_PATH` | `$TMPDIR/ov-sdk-feedback.jsonl` | 06 |
| `OV_RUN` | unset | 06 |

Example 04 reaches the model through the OpenViking gateway, so it needs no
provider SDK and no API key beyond the gateway key issued in Studio.

Example 06 is meant to be run twice: it loads and appends to the same JSONL
file, so the second run starts from the first run's records. `OV_RUN` only
labels the printed run.

## Sample output

`02-memory.mjs` shows the self-improving policy moving after one used result:

```json
{
  "policyBefore": { "limit": 11, "minScore": 0.2 },
  "policyAfter":  { "limit": 10, "minScore": 0.253 },
  "total": 10,
  "used": true
}
```

`04-agent.mjs` cites the URI it actually relied on and records it:

```json
{
  "recalled": 10,
  "cited": "viking://user/default/skills/local-ai-stack/SKILL.md",
  "markedUsed": true
}
```

`05-context.mjs` returns one block plus the budget accounting around it
(`citations` and `preview` are abridged here; `preview` is 300 characters):

```json
{
  "query": "local AI stack",
  "citations": [
    "viking://user/default/memories/entities/software/antigravity_cli.md",
    "viking://user/default/memories/events/2026/11/10/gateway_implementation.md",
    "viking://user/default/memories/entities/software/vikingbot.md"
  ],
  "tokensUsed": 1172,
  "truncated": false,
  "entries": [
    {
      "uri": "viking://user/default/memories/entities/software/antigravity_cli.md",
      "score": 0.697,
      "category": "entities"
    },
    {
      "uri": "viking://user/default/memories/events/2026/11/10/gateway_implementation.md",
      "score": 0.682,
      "category": "events"
    },
    {
      "uri": "viking://user/default/memories/entities/software/vikingbot.md",
      "score": 0.678,
      "category": "entities"
    }
  ],
  "preview": "<memory uri=\"viking://user/default/memories/entities/software/antigravity_cli.md\" …"
}
```

`06-feedback.mjs` run twice in a row — the second run's `samplesBefore` is the
first run's `samplesAfter`, so the policy came back from disk:

```json
{
  "path": "/var/folders/…/T/ov-sdk-feedback.jsonl",
  "samplesBefore": 0,
  "samplesAfter": 1,
  "hitRate": 0.6,
  "limit": 10,
  "minScore": 0.253,
  "fileLines": 1
}
```

```json
{
  "path": "/var/folders/…/T/ov-sdk-feedback.jsonl",
  "samplesBefore": 1,
  "samplesAfter": 2,
  "hitRate": 0.68,
  "limit": 9,
  "minScore": 0.295,
  "fileLines": 2
}
```
