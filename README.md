# UGen AI Gateway

[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.143%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![uv](https://img.shields.io/badge/uv-package%20manager-DE5FE9)](https://docs.astral.sh/uv/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

UGen AI Gateway is a FastAPI service that analyzes incoming chat requests,
redacts detected personal data, and routes the generation request to a local or
cloud OpenAI-compatible model. The local model is also used to analyze requests
for task type, complexity, and personal data.

## Requirements

- Python 3.13 or later
- [uv](https://docs.astral.sh/uv/)
- A running local model endpoint with an OpenAI-compatible Chat Completions API

Ollama is one option for a local endpoint. Its OpenAI-compatible API is commonly
available at `http://localhost:11434/v1`.

## Configuration

Copy the example settings file and edit it for your model endpoint:

```sh
cp .env.example .env
```

| Variable | Purpose | Example / default |
| --- | --- | --- |
| `APP_NAME` | FastAPI application title | `UGen AI Gateway` |
| `APP_ENV` | Environment label | `development` |
| `GATEWAY_API_KEY` | API key required in the `X-API-Key` header; required when `APP_ENV=production` | blank in development |
| `RATE_LIMIT_REQUESTS` | Requests allowed per client IP in the configured window | `60` |
| `RATE_LIMIT_WINDOW_SECONDS` | Rate-limit window in seconds | `60` |
| `LOCAL_API_KEY` | API key passed to the local provider | `local` |
| `LOCAL_BASE_URL` | Local OpenAI-compatible API base URL | `http://localhost:11434/v1` |
| `LOCAL_MODEL` | Local model used for analysis and local generation | `qwen2.5:7b` |
| `CLOUD_API_KEY` | Optional cloud provider API key; blank disables cloud routing | blank |
| `CLOUD_BASE_URL` | Optional cloud-compatible API base URL; blank uses the SDK default | blank |
| `CLOUD_MODEL` | Cloud model used for complex requests | blank |
| `REQUEST_TIMEOUT` | Provider request timeout in seconds | `300` |
| `MAX_RETRIES` | Provider client retry count | `2` |

Cloud routing is enabled only when both `CLOUD_API_KEY` and `CLOUD_MODEL` are
configured. Use a cloud endpoint and model that support the OpenAI-compatible
Chat Completions API.

## Run the service

Install the project dependencies and start the API:

```sh
uv sync
uv run ugen-ai-gateway
```

The server listens on `0.0.0.0:7632` by default. `/health` reports process
liveness. `/ready` checks that the local model endpoint (and configured cloud
endpoint) can be reached.

## API

### OpenAI-compatible endpoints

Any OpenAI SDK or tool can use the gateway by pointing its base URL at
`http://localhost:7632/v1`:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:7632/v1", api_key="your-gateway-key")
reply = client.chat.completions.create(
    model="ugen-auto",
    messages=[{"role": "user", "content": "Explain what an API gateway does."}],
)
print(reply.choices[0].message.content)
```

#### `POST /v1/chat/completions`

Accepts the standard Chat Completions request and returns the standard
response. `stream: true` returns Server-Sent Events chunks ending with
`data: [DONE]`; `stream_options: {"include_usage": true}` adds a final usage
chunk. `temperature`, `max_tokens`, `max_completion_tokens`, `top_p`, `stop`,
`presence_penalty`, `frequency_penalty`, and `seed` are forwarded to the
selected model. Tool calling and `n > 1` are not supported yet and return HTTP
`400`.

The `model` field picks the routing mode:

| `model` | Behavior |
| --- | --- |
| `ugen-auto` (default) | Analyze the request and route automatically |
| `ugen-local` | Always use the local model |
| `ugen-cloud` | Always use the cloud model |
| the configured `LOCAL_MODEL` / `CLOUD_MODEL` name | Same as `ugen-local` / `ugen-cloud` |

Any other model name returns HTTP `404` with code `model_not_found`. Every
request is still analyzed and redacted locally, whichever mode is chosen.

The routing decision is reported in response headers (`X-UGen-Provider`,
`X-UGen-Model`, `X-UGen-Route-Reason`, `X-UGen-Redacted-Values`) and in an extra
`ugen` object on the response (or on the final stream chunk) with the full
`routing` analysis and `performance` timings. Errors use the OpenAI error shape:
`{"error": {"message": "...", "type": "...", "code": ...}}`.

#### `GET /v1/models`

Lists `ugen-auto`, `ugen-local`, `ugen-cloud` (when cloud routing is enabled),
and the configured model names.

### Authentication and API keys

Requests authenticate with `Authorization: Bearer <key>` (what OpenAI SDKs
send) or `X-API-Key`. Three kinds of key are accepted:

- `GATEWAY_API_KEY` and `ADMIN_API_KEY`: no restrictions, and stats cover
  every request.
- API keys created through the admin API: one per team or application, each
  with its own policy, and stats and feedback limited to its own requests.

The gateway runs open (no key needed) only while there is no
`GATEWAY_API_KEY`, no `ADMIN_API_KEY`, and no API key has ever been created.
Creating the first key locks it, and revoking every key does not reopen it.

Manage keys with `ADMIN_API_KEY` (or `GATEWAY_API_KEY` when no admin key is
set). Without either, the key endpoints work only until the first key exists:

```sh
curl -X POST localhost:7632/v1/ugen/keys -H 'X-API-Key: <admin key>' \
  -H 'Content-Type: application/json' \
  -d '{"name": "marketing", "allow_cloud": true, "monthly_budget_usd": 20, "rate_limit_per_minute": 60}'
```

The response includes the secret `key` (`ugk_…`) once; only its SHA-256 hash is
stored. `GET /v1/ugen/keys` lists keys with this month's request count and
cost, `DELETE /v1/ugen/keys/{id}` revokes one, and `GET /v1/ugen/whoami`
reports what the presented key allows. A key with `allow_cloud: false`, or
whose monthly cloud spend has reached `monthly_budget_usd`, is answered by the
local model instead (`routing.rule` is `key.local_only` or `key.budget`).

Requests are rate limited per API key (`rate_limit_per_minute`) or, for the
other keys and open mode, per client IP (`RATE_LIMIT_REQUESTS` per
`RATE_LIMIT_WINDOW_SECONDS`); an exceeded limit returns HTTP `429`. The
limiter is in-memory and per process behind a small `RateLimiter` interface,
so running several workers needs a shared store, such as Redis, behind that
interface.

The dashboard's settings dialog (gear icon in the sidebar) stores the key it
uses in the browser, shows what the key allows, and lets an admin create,
list, and revoke keys and export the audit log.

### Dashboard endpoints

These power the bundled dashboard and expose more detail than the OpenAI API.

- `GET /health` returns `{"status":"ok"}` when the process is running.
- `GET /ready` checks that the configured model endpoints can be reached.
- `GET /v1/ugen/config` returns the configured model names and privacy
  settings (no secrets).
- `POST /v1/ugen/chat` takes `messages`, `temperature`, and
  `provider_selection` (`auto`, `local`, or `cloud`) and returns:

```json
{
  "model": "qwen2.5:7b",
  "content": "An API gateway ...",
  "finish_reason": "stop",
  "provider": "local",
  "routing": {
    "complexity": "simple",
    "task_type": "general",
    "contains_personal_data": false,
    "personal_data_types": [],
    "provider": "local",
    "reason": "Local model selected by the default routing policy.",
    "redacted_values": 0
  },
  "usage": {
    "analysis": {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250},
    "generation": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
  },
  "performance": {"analysis_ms": 500.0, "generation_ms": 800.0, "total_ms": 1300.0}
}
```

- `POST /v1/ugen/chat/stream` takes the same body and emits newline-delimited
  JSON events: `analysis_started`, `analysis_completed`, `routed`,
  `generation_started`, one `delta` event per generated text chunk
  (`{"type":"delta","content":"..."}`), and finally `result` with the body
  above. A failure emits an `error` event instead of `result`, even after some
  deltas were sent.

- `GET /v1/ugen/providers` reports each provider's circuit-breaker state.
- `GET /v1/ugen/stats?days=7&tz_offset_minutes=480` returns totals for the
  period: completed and failed requests, local/cloud split, tokens, generation
  speed, cost, savings, personal-data and fallback counts, per-rule counts,
  feedback, and daily counts bucketed in the given UTC offset.
- `GET /v1/ugen/requests?limit=50&before=<created_at>` lists recent request
  metadata, newest first.
- `POST /v1/ugen/feedback` with `{"request_id": "...", "rating": 1}` (or `-1`)
  records feedback on an answer; `request_id` is the `id` of the chat response.

### Guards

**Prompt injection.** User messages are checked for common injection
phrasings in English and Chinese ("ignore all previous instructions",
「忽略之前的指示」, requests to reveal the system prompt, "developer mode",
sending keys to a URL). This is a heuristic tripwire, not a guarantee.
`INJECTION_ACTION` decides what happens:

| Value | Behavior |
| --- | --- |
| `flag` (default) | Record the signals (`routing.injection_signals`, a `guard` stream event, usage and audit logs) and continue |
| `local` | Also keep the request on the local model |
| `block` | Reject with HTTP `400` before any model is called |
| `off` | Skip the check |

**Personal data in answers.** The reply is scanned for personal data that the
user did not provide, such as a phone number or key the model produced on its
own. `OUTPUT_PII_ACTION=redact` (default) replaces it with a `[LABEL]` marker,
`flag` only records it, `off` skips the check. Values from the request are left
alone. When streaming, text is released only at points no value can span (the
last 24 characters are held until the next safe break), so a value is never
sent in pieces before it can be checked. Findings appear in
`routing.output_findings`.

### Audit log

Every request appends an entry to an audit log in the usage database: the same
metadata as usage tracking (never message text or values), chained so that
each entry's SHA-256 covers the previous entry's hash. Changing, deleting, or
reordering an entry breaks verification from that point on.

- `GET /v1/ugen/audit?since=<unix time>&limit=1000&format=json|csv` exports it.
- `GET /v1/ugen/audit/verify` recomputes the chain and returns
  `{"ok": true, "entries": N, "first_invalid_seq": null}`.

Both need `ADMIN_API_KEY` (or `GATEWAY_API_KEY` when no admin key is set),
like key management.

### Usage tracking

Each request's metadata is stored in SQLite at `USAGE_DB_PATH` (default
`data/ugen.db`; empty disables tracking): routing decision, provider and model,
matched rule, task type, complexity, analysis source, which personal-data
types were found and how many values were redacted, fallback, token counts,
timings, and cost. Message text and personal-data values are never written.

Cost uses the per-million-token prices in the routing configuration. Savings
compare it with the same tokens on the default cloud model, so answers kept on
the local model count as saved. The dashboard shows these totals, and its
👍/👎 buttons send feedback.

Provider usage values depend on what the upstream model API reports; missing
usage is `null`. `analysis_ms` measures the local analysis request,
`generation_ms` the generation request, and `total_ms` the whole operation.

## Routing behavior

Each request is analyzed locally before routing. `ANALYSIS_MODE` controls how:

| Mode | Behavior |
| --- | --- |
| `hybrid` (default) | Fast rule-based analysis when the request would stay on the local model anyway; the local model analyzes anything that could go to the cloud |
| `llm` | Always ask the local model |
| `rules` | Regular expressions only, never call the model |

Rule-based analysis only detects the structured personal data listed under
[Personal-data handling](#personal-data-handling); names and addresses need the
model. That is why `hybrid` only skips the model for requests that stay on this
machine, and why `rules` is not recommended when cloud routing is enabled.

Model analysis results are cached in memory for identical request text
(`ANALYSIS_CACHE_SIZE`, default `256` entries, `0` disables;
`ANALYSIS_CACHE_TTL_SECONDS`, default `600`). Cache keys are SHA-256 hashes,
and cached results never leave the process. `routing.analysis_source` and the
`analysis_completed` stream event report `llm`, `rules`, or `cache`.

The analysis model classifies each request. Moderate and complex tasks use the cloud
provider when cloud routing is enabled. Strong complexity cues in the prompt,
such as architecture design, comparisons, implementation, debugging, and
multi-step planning, promote an underestimated simple classification to complex.
Simple tasks stay local. If cloud routing is disabled, requests stay local. The
returned `routing` object reports the estimated complexity, task type, detected
personal-data types, selected provider, and the reason for that decision.

### Routing rules and multiple models

For more than one local and one cloud model, point `ROUTING_CONFIG` at a YAML
file. [`routing.example.yaml`](routing.example.yaml) documents every field:

- `providers`: each OpenAI-compatible endpoint, whether it is `local` or `cloud`,
  its API key (`api_key_env` reads it from the environment), and its models
  with per-million-token prices for cost reporting. A provider whose key is
  missing is skipped with a warning, and rules pointing at it are ignored.
- `defaults`: the local and (optional) cloud model used for `ugen-local`,
  `ugen-cloud`, fallbacks, and requests no rule matches.
- `analysis`: the model that analyzes requests. It sees unredacted text, so it
  must be a local provider; the file is rejected otherwise.
- `rules`: checked in order, first match wins. A rule can match on
  `complexity`, `task_type`, `contains_personal_data`, `personal_data_types`
  (any of), `min_chars`, and `max_chars`, and routes to a provider and model.
- `privacy.never_cloud`: personal-data types that always keep the request on
  the local default model, overriding rules and explicit `ugen-cloud` requests.

Every configured model appears in `GET /v1/models`; requesting one by name pins
that model, still subject to `never_cloud`. `routing.provider_name` and
`routing.rule` in responses report which provider and rule were used
(`privacy.never_cloud` when the privacy rule applied).

Without `ROUTING_CONFIG`, the `LOCAL_*` and `CLOUD_*` variables define one local
and one cloud provider, with moderate and complex requests going to the cloud.

## Resilience

Provider calls are retried with exponential backoff on connection errors, HTTP
`429`, and `5xx` responses (`MAX_RETRIES`, default `2`); a stream is only
retried before any text has been sent.

If the routed provider is still unavailable, automatically routed requests fall
back to the other provider before any text has been sent:

| Variable | Behavior | Default |
| --- | --- | --- |
| `FALLBACK_TO_LOCAL` | Cloud unavailable: answer with the local model, using the same redacted messages | `true` |
| `FALLBACK_TO_CLOUD` | Local unavailable: answer with the cloud model, only when the local model analyzed the request and found no personal data | `false` |

Requests that chose a provider explicitly (`ugen-local`, `ugen-cloud`, or
`provider_selection`) never fall back. A fallback is reported as a `fallback`
stream event, in `routing.fallback_from`, and in the `X-UGen-Fallback-From`
header.

Each provider has a circuit breaker: after `CIRCUIT_FAILURE_THRESHOLD`
consecutive outages (default `3`) the provider is skipped for
`CIRCUIT_COOLDOWN_SECONDS` (default `30`), then a single request probes it
again. `GET /v1/ugen/providers` reports each breaker's state (`closed`, `open`,
or `half_open`).

## Personal-data handling

Before sending messages to the generation provider, the gateway redacts the
following, detected locally with regular expressions (and checksums where the
format has one):

| Type | Examples |
| --- | --- |
| `email` | `alex@example.com` |
| `taiwan_mobile`, `taiwan_landline` | `0912-345-678`, `(02)2345-6789`, `+886-2-2345-6789` |
| `taiwan_id`, `taiwan_resident_certificate` | National ID, old and new resident certificate numbers (checksum validated) |
| `taiwan_ubn` | 統一編號 (checksum validated) |
| `passport`, `nhi_card` | Passport and 健保卡 numbers |
| `credit_card` | Card numbers (Luhn validated) |
| `api_key`, `jwt`, `private_key`, `password` | `sk-…`, `AKIA…`, `ghp_…`, JWTs, PEM private keys, `password=…` |

Short numeric identifiers (統一編號, passport, 健保卡) only count when a keyword
such as `統編`, `護照`, or `健保卡號` comes right before them, so ordinary 8-digit
amounts or dates are left alone. For passwords and those keyword-led values,
only the value is replaced and the keyword stays readable. The analysis model
can also report additional exact personal-data values, which are redacted from
text message content. Matching works inside Chinese text without spaces.

Names and places can optionally be detected with
[Presidio](https://microsoft.github.io/presidio/) named-entity recognition:

```sh
uv sync --extra ner
uv run python -m spacy download zh_core_web_sm
```

Then set `PII_NER=presidio` (`PII_NER_LANGUAGE`, default `zh`;
`PII_NER_SPACY_MODEL`, default `zh_core_web_sm`; `PII_NER_SCORE_THRESHOLD`,
default `0.5`).

Redaction is reversible. Each value is replaced with a numbered placeholder such
as `[EMAIL_1]`; the same value gets the same placeholder across every message in
the request, and a short system instruction asks the model to keep placeholders
as written. The placeholder-to-value mapping exists only in memory for that one
request. When the reply comes back, placeholders are replaced with the original
values (also in streamed deltas, even when a placeholder is split across
chunks), so the user sees a complete answer while the model never saw the data.
The response's `routing.redacted_values` reports how many values were replaced.

| Variable | Purpose | Default |
| --- | --- | --- |
| `REDACT_LOCAL` | Also redact requests routed to the local model | `true` |
| `RESTORE_REDACTED` | Put original values back into the reply | `true` |

Requests routed to the cloud are always redacted. Common
patterns are detected locally as a backstop if the analysis model misses them.
The current analysis and redaction flow applies to text; image or other
non-textual content is not inspected for personal data.

Name, address, and other free-form data do not have a universal deterministic
pattern. Detection of those values depends on the analysis model; if the model
reports personal data it cannot point to exactly (a type without the values),
the request is kept on the local model (`routing.rule` is
`privacy.unredactable`) and is only refused when no local model is available. PII detection is a safeguard,
not a guarantee that every sensitive value will be recognized.

## Limits and errors

- At most 64 messages per request.
- At most 32,000 text characters across the request.
- Invalid request data returns HTTP `422`; a request exceeding the gateway's
  input limit returns HTTP `413`.
- Missing provider configuration or an unavailable provider returns HTTP `503`.
- Invalid or missing gateway API keys return HTTP `401`; rate-limited requests
  return HTTP `429`.
- Provider timeouts return HTTP `504`.
- Other upstream provider errors return HTTP `502`.

## Tests

Run the automated test suite and lint checks with:

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Contributing

Bug reports and pull requests are welcome. Before opening a pull request, run
the test suite and lint checks above. For larger changes, open an issue first
to discuss the proposed behavior.

## License

This project is licensed under the [MIT License](LICENSE).
