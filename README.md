# UGen AI Gateway

[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.143%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)](#deployment)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

UGen AI Gateway sits between your apps and your language models. Every request
is analyzed on your own machine first: personal data is detected and replaced
with placeholders, and the request is routed to a local model or a cloud model
depending on its complexity and your privacy rules. Replies come back with the
original values restored, so users see a complete answer while the cloud model
never saw the data.

- **Privacy first**: reversible redaction of emails, phone numbers, national
  IDs, cards, keys and more; data types you choose never leave the machine.
- **Smart routing**: simple requests stay on the local model, complex ones go
  to the cloud; YAML rules for several providers and models; automatic
  fallback when a provider is down.
- **OpenAI-compatible API**: point any OpenAI SDK or tool at `/v1`.
- **Dashboard**: chat with live routing, usage and cost savings, accounts with
  an administrator, per-team API keys with budgets and rate limits, and a
  tamper-evident audit log.

## Quick start

You need [Docker](https://docs.docker.com/engine/install/) with the Compose
plugin, and an OpenAI-compatible model server for the local model, such as
[Ollama](https://ollama.com):

```sh
ollama pull qwen2.5:7b

git clone https://github.com/baihufox3210/ugen-ai-gateway.git
cd ugen-ai-gateway
cp .env.example .env          # set LOCAL_MODEL (and CLOUD_* for a cloud model)
docker compose up -d --build
```

Open `http://<host>:7632`, create the administrator account, and start
chatting. The first account manages the gateway; see [Accounts](#accounts).

## Deployment

The Docker image builds the dashboard and serves it together with the API on
one port. Models and every other setting come from `.env`
(see [Configuration](#configuration)).

### On a Linux server

1. Install Docker and your local model server (for example Ollama with
   `ollama pull <model>`).
2. Clone the repository, `cp .env.example .env`, and set at least
   `LOCAL_MODEL` (plus `CLOUD_API_KEY`, `CLOUD_MODEL` and, for non-OpenAI
   providers, `CLOUD_BASE_URL` to enable the cloud).
3. `docker compose up -d --build`, then check `docker compose ps` shows the
   container as `healthy`, and `curl http://localhost:7632/ready` returns
   `{"status":"ready"}` once the model server is reachable.
4. Open the dashboard and create the administrator account. To stop strangers
   from signing up afterwards, set `ALLOW_REGISTRATION=false` and run
   `docker compose up -d` again.
5. For other apps that call the API, create API keys on the dashboard's
   API keys page. Once any account or key exists, the API requires a key.

The compose file uses host networking, so the container reaches a model
server on the same machine at `localhost` (Ollama's default
`http://localhost:11434/v1` works as is). On Docker Desktop (macOS, Windows),
or to isolate the container, replace `network_mode: host` with the
`ports`/`extra_hosts` lines in the file's comment and set
`LOCAL_BASE_URL=http://host.docker.internal:11434/v1`.

To use a routing YAML file (`ROUTING_CONFIG`), mount it into the container,
for example add `- ./routing.yaml:/app/routing.yaml:ro` under `volumes` and
set `ROUTING_CONFIG=/app/routing.yaml`. API keys it names with `api_key_env`
can go in `.env`.

### HTTPS

Sign-in passwords and session tokens travel with every request, so serve the
gateway over HTTPS whenever it is reachable beyond your own network.

- **Tailscale** (private access): `tailscale serve --bg 7632` publishes the
  gateway at `https://<machine>.<tailnet>.ts.net` to your tailnet only.
- **A public domain**: run a reverse proxy such as [Caddy](https://caddyserver.com),
  which obtains certificates automatically. With this `Caddyfile`:

  ```
  gateway.example.com {
      reverse_proxy 127.0.0.1:7632
  }
  ```

  set `GATEWAY_HOST=127.0.0.1` in `.env` so the gateway itself is only
  reachable through the proxy, and allow ports 80 and 443 in your firewall.
  The proxy's `X-Forwarded-For` header is honored, so rate limits still apply
  per client.

### Updating, backups and logs

```sh
git pull && docker compose up -d --build   # update; data is kept
docker compose logs -f gateway             # follow the logs
```

Accounts, API keys, usage metadata and the audit log live in one SQLite file
in the `ugen-data` volume, which survives rebuilds (`docker compose down -v`
deletes it). Conversations are never stored on the server; they stay in each
user's browser. To take a consistent backup while the gateway runs:

```sh
docker compose exec gateway python -c "import sqlite3; sqlite3.connect('/app/data/ugen.db').backup(sqlite3.connect('/app/data/backup.db'))"
docker compose cp gateway:/app/data/backup.db ./ugen-backup.db
```

To restore a backup, copy it in and load it the same way, then restart:

```sh
docker compose cp ./ugen-backup.db gateway:/app/data/restore.db
docker compose exec gateway python -c "import sqlite3; sqlite3.connect('/app/data/restore.db').backup(sqlite3.connect('/app/data/ugen.db'))"
docker compose restart gateway
```

## Configuration

Copy `.env.example` to `.env`; it documents every setting. The main ones:

| Variable | Purpose | Default |
| --- | --- | --- |
| `LOCAL_BASE_URL` | Local OpenAI-compatible API base URL | `http://localhost:11434/v1` |
| `LOCAL_MODEL` | Local model, also used to analyze requests | `qwen2.5:7b` |
| `LOCAL_API_KEY` | API key sent to the local server | `local` |
| `CLOUD_API_KEY`, `CLOUD_MODEL` | Cloud provider key and model; blank disables the cloud | blank |
| `CLOUD_BASE_URL` | Cloud OpenAI-compatible base URL; blank means OpenAI | blank |
| `LOCAL_MODEL_ALIAS`, `CLOUD_MODEL_ALIAS` | Public names for the models ([Model aliases](#model-aliases)) | blank |
| `ROUTING_CONFIG` | YAML with several providers, models and rules ([Routing rules](#routing-rules-and-multiple-models)) | blank |
| `ALLOW_REGISTRATION` | Allow sign-ups after the first (administrator) account | `true` |
| `SESSION_TTL_DAYS` | How long a dashboard sign-in lasts | `30` |
| `GATEWAY_HOST`, `GATEWAY_PORT` | Docker only: listen address and port | `0.0.0.0`, `7632` |
| `USAGE_DB_PATH` | SQLite database; empty turns off accounts and usage tracking | `data/ugen.db` (Docker: `/app/data/ugen.db`) |
| `GATEWAY_API_KEY`, `ADMIN_API_KEY` | Optional fixed keys with full access (for scripts); `APP_ENV=production` requires `GATEWAY_API_KEY` | blank |
| `ANALYSIS_MODE` | How requests are analyzed ([Routing behavior](#routing-behavior)) | `hybrid` |
| `INJECTION_ACTION`, `OUTPUT_PII_ACTION` | Guards ([Guards](#guards)) | `flag`, `redact` |
| `REDACT_LOCAL`, `RESTORE_REDACTED` | Redaction ([Personal-data handling](#personal-data-handling)) | `true`, `true` |
| `FALLBACK_TO_LOCAL`, `FALLBACK_TO_CLOUD` | Fallback when a provider is down ([Resilience](#resilience)) | `true`, `false` |
| `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS` | Requests per client IP per window | `60`, `60` |
| `REQUEST_TIMEOUT`, `MAX_RETRIES` | Provider timeout (seconds) and retries | `300`, `2` |

Cloud routing is enabled only when both `CLOUD_API_KEY` and `CLOUD_MODEL` are
set. Use a cloud endpoint and model that support the OpenAI-compatible Chat
Completions API. After changing `.env`, apply it with `docker compose up -d`.

`/health` reports process liveness; `/ready` checks that the local model
endpoint (and configured cloud endpoint) can be reached.

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

### Accounts

The dashboard requires signing in. The first account created becomes the
administrator; later accounts are members. Administrators manage API keys,
export the audit log and see every request's usage; members see only their own.
`ALLOW_REGISTRATION=false` closes sign-up once the first account exists.

- `GET /v1/ugen/auth/status` tells the sign-in screen whether accounts are
  enabled, whether any exist and whether sign-up is open.
- `POST /v1/ugen/auth/register` and `POST /v1/ugen/auth/login` take
  `{"username": "...", "password": "..."}` and return
  `{"token": "ugs_…", "expires_at": ..., "user": {...}}`. Usernames are 3–32
  letters, digits, dots, dashes or underscores (case-insensitive); passwords
  are at least 8 characters. Sign-in and sign-up are limited to 10 attempts
  per minute per IP.
- `GET /v1/ugen/auth/me` returns the signed-in user and
  `POST /v1/ugen/auth/logout` ends the session.
- `PUT /v1/ugen/auth/me/avatar` with `{"image": "data:image/webp;base64,..."}`
  sets the profile picture (PNG, JPEG or WebP, at most 200 KB; the dashboard
  crops and resizes it to 160×160 first) and `{"image": null}` removes it.
- `DELETE /v1/ugen/auth/me` with `{"password": "..."}` deletes the account,
  its sessions, and its request metadata and feedback. Audit entries stay
  (they never name the user, and removing them would break the hash chain).
  If the last administrator leaves, the oldest remaining account is promoted.

Passwords are stored as salted scrypt hashes and session tokens as SHA-256
hashes, in the same SQLite database as usage (`USAGE_DB_PATH`). Without a
database, accounts are off and the dashboard opens without sign-in.

### Authentication and API keys

Requests authenticate with `Authorization: Bearer <key>` (what OpenAI SDKs
send) or `X-API-Key`. Accepted credentials:

- `GATEWAY_API_KEY` and `ADMIN_API_KEY`: no restrictions, and stats cover
  every request.
- A dashboard session token (`ugs_…`) from signing in.
- API keys created by an administrator: one per team or application, each
  with its own policy, and stats and feedback limited to its own requests.

The gateway runs open (no key needed) only while there is no
`GATEWAY_API_KEY`, no `ADMIN_API_KEY`, no account and no API key has ever been
created. Creating the first account or key locks it, and deleting every
account or revoking every key does not reopen it.

Manage keys from the dashboard settings as an administrator, or with
`ADMIN_API_KEY` (or `GATEWAY_API_KEY` when no admin key is set). With none of
those, the key endpoints work only until the first key or account exists:

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

In the dashboard, the account button in the sidebar opens the Account page
(profile picture, sign out, delete account) and the key icon opens the API
keys page, where an administrator creates, lists and revokes keys and exports
the audit log.

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

Both need an administrator account, or `ADMIN_API_KEY` (or `GATEWAY_API_KEY`
when no admin key is set), like key management.

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

### Model aliases

`LOCAL_MODEL_ALIAS` and `CLOUD_MODEL_ALIAS` (or `alias:` on a model in the
routing YAML) give a model a public name. The alias replaces the real model
name everywhere clients look: the dashboard, `GET /v1/models`, the `model`
field and `X-UGen-Model` header of responses, stream events and usage records.
Requests may use either the alias or the real name as `model`; the provider is
always called with the real name. Aliases and model ids must be unique.

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

## Development

Requirements: Python 3.13 or later, [uv](https://docs.astral.sh/uv/) and
Node.js 22.

```sh
uv sync
uv run ugen-ai-gateway                     # API on http://localhost:7632
cd frontend && npm install && npm run dev  # dashboard on http://localhost:5173
```

The development dashboard reloads as its code changes and proxies `/api` to
the API on port `7632` (set `UGEN_BACKEND_PORT` for another port). When
`frontend/dist` exists (after `npm run build`), the API server also serves the
dashboard at `/`, as the Docker image does.

Run the tests and checks with:

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
cd frontend && npm run build
```

## Contributing

Bug reports and pull requests are welcome. Before opening a pull request, run
the test suite and lint checks above. For larger changes, open an issue first
to discuss the proposed behavior.

## License

This project is licensed under the [MIT License](LICENSE).
