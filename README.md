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
| `MAX_RETRIES` | Provider client retry count | `0` |

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

### `GET /health`

Returns `{"status":"ok"}` when the API process is running. This endpoint does
not check whether model providers are reachable.

### `POST /v1/chat/completions`

Example request:

```sh
curl http://localhost:7632/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: your-gateway-key' \
  -d '{
    "messages": [
      {"role": "user", "content": "Explain what an API gateway does."}
    ],
    "temperature": 0.2
  }'
```

The request accepts 1–64 OpenAI-compatible chat messages, a temperature from
`0.0` to `2.0`, and an optional model override. Text across all messages is
limited to 32,000 characters. The model override is limited to 128 characters.

When `GATEWAY_API_KEY` is set, every chat request must include it in
`X-API-Key`; invalid or missing keys receive HTTP `401`. Requests are limited
per client IP using `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW_SECONDS`; an
exceeded limit receives HTTP `429`. The built-in limiter is per process, so
deployments with multiple workers or replicas should enforce a shared limit at
the reverse proxy or API gateway as well.

The response includes the generated `content`, selected `provider` and `model`,
`routing` analysis, token `usage` when reported by the model provider, and
`performance` timings in milliseconds. For example:

```json
{
  "model": "qwen2.5:7b",
  "content": "An API gateway ...",
  "provider": "local",
  "routing": {
    "complexity": "simple",
    "task_type": "general",
    "contains_personal_data": false,
    "personal_data_types": [],
    "provider": "local",
    "reason": "Local model selected by the default routing policy."
  },
  "usage": {
    "analysis": {
      "prompt_tokens": 200,
      "completion_tokens": 50,
      "total_tokens": 250
    },
    "generation": {
      "prompt_tokens": 20,
      "completion_tokens": 10,
      "total_tokens": 30
    }
  },
  "performance": {
    "analysis_ms": 500.0,
    "generation_ms": 800.0,
    "total_ms": 1300.0
  }
}
```

Provider usage values depend on what the upstream model API reports. Missing
usage data is represented as `null` for that stage. `analysis_ms` measures the
local analysis request, `generation_ms` measures the selected generation
provider request, and `total_ms` measures the complete gateway operation.

## Routing behavior

The local model analyzes each request. Moderate and complex tasks use the cloud
provider when cloud routing is enabled. Strong complexity cues in the prompt,
such as architecture design, comparisons, implementation, debugging, and
multi-step planning, promote an underestimated simple classification to complex.
Simple tasks stay local. If cloud routing is disabled, requests stay local. The
returned `routing` object reports the estimated complexity, task type, detected
personal-data types, selected provider, and the reason for that decision.

`POST /v1/chat/completions` also accepts `provider_selection` with `auto`,
`local`, or `cloud`. It defaults to `auto`; explicit local or cloud selection
overrides the automatic generation provider while keeping request analysis and
personal-data checks on the local model. The `/v1/chat/completions/stream`
endpoint emits newline-delimited progress events for analysis, routing, and
generation.

## Personal-data handling

Before sending messages to the generation provider, the gateway redacts detected
email addresses, Taiwan mobile numbers, valid Taiwan national ID numbers, and
valid credit card numbers. The analysis model can also report additional exact
personal-data values, which are redacted from text message content. Common
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
