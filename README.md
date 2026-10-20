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

### Authentication and limits

When `GATEWAY_API_KEY` is set, chat requests must send it either as
`Authorization: Bearer <key>` (what OpenAI SDKs send) or as `X-API-Key`;
invalid or missing keys receive HTTP `401`. Requests are limited per client IP
using `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW_SECONDS`; an exceeded limit
receives HTTP `429`. The built-in limiter is per process, so deployments with
multiple workers or replicas should enforce a shared limit at the reverse proxy
or API gateway as well.

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
reports a personal-data type that cannot be safely redacted, the gateway rejects
the request instead of forwarding it unredacted. PII detection is a safeguard,
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
