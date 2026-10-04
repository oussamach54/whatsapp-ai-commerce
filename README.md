# WhatsApp AI Commerce

Step 7.5 adds bounded WhatsApp image interpretation and optional trusted merchant
product links. See [the multimodal guide](docs/step-7.5-multimodal.md) for configuration,
privacy, trust boundaries, validation and manual E2E scenarios. The existing frontend
is not a public storefront; configure `STOREFRONT_BASE_URL` only for real product pages.

A production-oriented foundation for a cash-on-delivery e-commerce platform in which customers will communicate through WhatsApp and business owners will manage commerce operations through an admin dashboard.

## Architecture

- `backend/`: Python 3.13 FastAPI API, SQLAlchemy 2.x, Pydantic Settings, Alembic, and pytest.
- `frontend/`: React, TypeScript, Vite, and Tailwind CSS admin dashboard shell.
- `infra/docker/`: Docker build configuration for the backend.
- `docs/`: Documentation reserved for subsequent implementation steps.
- `docker-compose.yml`: Local PostgreSQL 17 and the backend service.

The backend includes commerce persistence, a working WhatsApp Cloud API integration,
and the Step 5.1 AI reply foundation. The frontend remains a dashboard shell with public legal pages.

## Prerequisites

- Python 3.13
- Node.js 20 or newer and npm
- Docker Desktop with Docker Compose v2

## Environment variables

Copy the root example before using Docker Compose:

```powershell
Copy-Item .env.example .env
```

Set a strong local value for `DATABASE_PASSWORD` in `.env`. Compose and the backend share `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_PORT`, `BACKEND_PORT`, and `ENVIRONMENT`. The backend constructs its SQLAlchemy URL from these individual settings, so passwords containing URL-reserved characters are supported.

For a directly run backend, copy `backend/.env.example` to `backend/.env` and set the database values to match the root `.env`.

The frontend optionally reads `VITE_API_BASE_URL`; see `frontend/.env.example`.

## Start PostgreSQL

```powershell
docker compose up -d postgres
docker compose ps
```

The database is PostgreSQL 17. Its data is stored in the named `postgres_data` Docker volume.

## Alembic convention

When a future SQLAlchemy model is added, import it from `app.models.__init__`. Alembic imports that registry before reading `Base.metadata`, allowing autogeneration to discover model tables.

Domain status and channel values are native PostgreSQL enums. Future additive values require an Alembic migration using PostgreSQL's `ALTER TYPE ... ADD VALUE` before application code uses them. Monetary database fields use `NUMERIC(12,2)`, which preserves Decimal precision for Moroccan dirham pricing.

## Run the backend

Create and activate a Python 3.13 virtual environment, then install dependencies:

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

The health endpoint is available at `http://localhost:8000/health`.

Alternatively, start the database and backend with Docker Compose:

```powershell
docker compose up --build
```

## Run the frontend

```powershell
cd frontend
npm install
npm run dev
```

Vite prints the local URL, normally `http://localhost:5173`.

## Run tests

With the backend virtual environment activated:

```powershell
cd backend
pytest
```

Database model tests require a PostgreSQL database that has been migrated with Alembic. Use a dedicated database and set `TEST_DATABASE_HOST`, `TEST_DATABASE_PORT`, `TEST_DATABASE_NAME`, `TEST_DATABASE_USER`, and `TEST_DATABASE_PASSWORD` before running pytest. The tests use a transaction and roll it back after each test; they never create schema objects outside Alembic.

## Current status

Step 6 adds verified catalog search, structured size/color, current price/stock,
bounded read-only AI tools and structured follow-up references. It preserves
WhatsApp, memory, scope and admission protections from Steps 1–5. Set
`CATALOG_CURRENCY=MAD`; one business/catalog per database/deployment is required.
Step 6 now includes bounded commercial conversation state, clarification recovery,
direct named choices, safe selection changes, and reply supersession protection.
See [conversational architecture and manual validation](docs/step-6-conversations.md).
See [trusted-state recovery, alternatives and the real WhatsApp retest](docs/step-6-recovery.md)
for the subsequent E2E fixes and intentional behavior changes.
See [natural intent and safe purchase confirmation](docs/step-6-natural-sales.md)
for the simplified Step 6 integration and the next real WhatsApp test sequence.
The [category-agnostic attribute boundary](docs/step-6-generic-attributes.md)
supports reuse across commerce domains. The current schema still verifies only
size/color as structured variant attributes; other requested attributes safely
clarify until separately approved schema support exists.

Application-owned COD checkout now creates orders after verified confirmation;
see [Step 7 checkout and validation](docs/step-7-cod.md). Apply migration
`20260929_0005` before enabling this version. The model cannot create orders.
Shared-database tenancy, RAG, n8n and human handoff are not implemented.
See [Step 6 architecture and validation](docs/step-6-catalog.md).
The Step 5 sections below describe the earlier foundation; Step 6's production
catalog path supersedes their single-generation/no-tools behavior.

## Step 5.1: AI reply foundation

`backend/app/ai/` separates the official OpenAI SDK adapter (`client.py`), reply
validation and fallback (`service.py`), instructions (`prompts.py`), Pydantic
request/response contracts (`schemas.py`), sanitized errors (`exceptions.py`),
and FastAPI dependency wiring (`dependencies.py`).

The webhook commits a new inbound message and schedules the existing background
task. After HTTP acknowledgment, the task sends the customer's text to the AI
service, sends the validated reply through the existing WhatsApp client, and
persists the exact text sent in a separate database transaction. Duplicate inbound
messages schedule neither another AI call nor another reply. The inbound transaction
is committed before AI generation; the outbound database session opens after sending.
Conversation ID is accepted as local context but is
not sent to OpenAI; phone numbers, customer records, and conversation history are
not added to the request. The customer's current message may itself contain
personal information and is sent to OpenAI when configured.

Install the updated `backend/requirements.txt`. Set these in `backend/.env` for a
direct backend run, or root `.env` for Docker Compose (the examples contain blanks):

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Your OpenAI project API key; keep it secret. |
| `OPENAI_MODEL` | Explicit model ID available to your project that supports text generation through the Responses API. |
| `OPENAI_TIMEOUT_SECONDS` | SDK request timeout, default 20 seconds (per network operation, not a total job deadline). |
| `OPENAI_MAX_OUTPUT_TOKENS` | Generation budget, default 512; some models may need more to produce a completed response. |

Restart the backend after changing settings; settings are cached. For Compose,
rebuild/recreate the backend to install the dependency and apply the environment.
No Meta or database configuration changes are needed.

The adapter uses the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/text)
through the official Python SDK, with retries disabled and `store=False`. This is
a request setting, not a claim that all provider retention is disabled. Only
completed, nonempty text responses of at most 4096 characters are accepted.
Instructions cover French, English, and Moroccan Darija in Latin characters,
concise replies, and no invented business facts or unperformed actions.

Missing AI configuration, timeouts, API/network errors, invalid output, and
unexpected SDK failures produce the fallback:
`Bonjour, votre message a bien été reçu.`
It does not promise human handoff. AI failure never changes the successful webhook
HTTP 200 acknowledgment. WhatsApp send failures retain the existing behavior.

`ai.reply_failed` logs only `failure_category`, numeric `http_status` when available,
and `fallback_used`. It never logs provider error messages/bodies, headers, keys,
customer text, or generated text. Do not enable OpenAI/HTTP wire debug logging in
production. Existing WhatsApp diagnostics also redact the configured OpenAI key.

Run focused validation from `backend`:

```powershell
python -m pytest tests/test_ai.py tests/test_whatsapp_client.py tests/test_whatsapp_diagnostics.py tests/test_whatsapp_webhook.py -q
python -m compileall -q app tests
python -m pip check
```

OpenAI calls are mocked; a test fixture blocks the SDK's real HTTP transport, so
tests cannot issue paid calls. PostgreSQL-backed tests still require the dedicated
test database described above. Language tests verify request/response handling and
instructions with mocks; they do not evaluate live model fluency or factuality.

This foundation has no business tools. Prompt instructions cannot guarantee factual correctness. Background
tasks remain in-process rather than durable jobs; process shutdown may lose pending
work, and concurrent replies are not serialized. No retries or new delivery guarantees
are introduced.

## Step 5.2: recent conversation context

PostgreSQL messages are the only memory source. Each background reply opens a
short-lived read session, finds the current inbound in its conversation, and reads
at most `AI_HISTORY_MAX_MESSAGES` preceding text records (default 12, range 0–100).
The query uses descending `(created_at, id)` ordering, a strict inbound cutoff,
and SQL LIMIT; selected messages are returned chronologically. Invalid legacy
text is discarded after the bounded query without fetching replacement rows.
Customer inbound text becomes `user`; sent WhatsApp AI/human replies and existing
customer-visible system replies become `assistant`, never a system instruction.
Media, empty/invalid text, internal records and outbound records without send
evidence are excluded. Sent fallback replies remain eligible.

`AI_HISTORY_MAX_CHARS` (default 12000, range 0–100000) keeps the newest contiguous
suffix of complete eligible messages that fits. A zero value for either setting
disables history. The current inbound is appended once, independently of the
history budget; identical text is not deduplicated. The character budget is not
an exact token budget. The latest customer message controls language/script;
past turns are context, not proof of business facts or completed actions.

The read session closes before OpenAI/WhatsApp calls; a separate write session
persists the outbound as before. Read failures log sanitized diagnostics and use
current-message-only generation. AI failures still use the Step 5.1 fallback.
OpenAI storage remains disabled; no provider conversation IDs or external memory
systems are used. No schema migration is needed.

Snapshot limitations: rapidly arriving messages may have overlapping background
generation, miss a not-yet-persisted reply, or produce replies out of order.
WhatsApp inbound timestamps and database outbound timestamps have different
origins; UUID tie-breaking is deterministic, not a guarantee of causal ordering.
Status callbacks are ignored, so accepted sends cannot be distinguished from
later delivery failures. Process shutdown can lose background work; successful
sends whose persistence fails are absent from history. No sequencing locks are
held during network calls. The existing conversation index bounds the scope and
LIMIT bounds returned rows, but very long conversations may still require a sort;
a composite chronology index can be evaluated later if measurements warrant it.

Include `tests/test_conversation_history.py` and `tests/test_conversations_api.py`
in the focused pytest command above. Query tests require the existing migrated
dedicated PostgreSQL test database; all OpenAI calls remain mocked.

## Step 5.3: sales scope and AI admission

The background reply now performs shared PostgreSQL admission before routing.
Gifts, shopping advice, budgets, comparisons, variants and support remain in scope
without a product name. Local phrase shortcuts accept obvious shopping requests;
unknown wording is not rejected for missing a keyword. A separate semantic
classifier handles unresolved intent with at most six previous messages and 3000
history characters, plus the current message. It returns only validated scope,
intent, language style and security action fields, never customer-facing prose.

| Route | Classifier calls | Sales generation calls |
| --- | ---: | ---: |
| Social-only, clearly unrelated, blocked, spam, throttled | 0 | 0 |
| Clear commerce | 0 | 1 |
| Ambiguous, classified commercial | 1 | 1 |
| Ambiguous, unrelated/unresolved/classifier failure | At most 1 | 0 |

Classifier failure, refusal or malformed output produces a short local shopping
clarification; it never automatically escalates to full generation. Local templates
support Darija Latin, Arabic-script Darija, French, English and mixed Darija/French.
Social greetings do not require an AI key. Accepted commerce still uses the exact
Step 5.1 fallback on generation failure or missing generation configuration.
When admission storage fails, the task suppresses its reply and makes no paid call.

Configure the following in `.env` (Docker) or `backend/.env` (local backend):

| Setting | Default | Meaning |
| --- | --- | --- |
| `AI_CLASSIFIER_MODEL` | `gpt-4.1-nano` | Separate lightweight model supporting Responses structured output; blank disables classification and uses clarification |
| `AI_CLASSIFIER_TIMEOUT_SECONDS` | `8` | SDK per-operation timeout; no retries |
| `AI_CLASSIFIER_MAX_OUTPUT_TOKENS` | `256` | Classifier output cap |
| `AI_MAX_INPUT_CHARS` | `2000` | Maximum current text sent to either AI model; ceiling 4096 |
| `AI_INBOUND_PER_MINUTE` | `10` | Unique inbound messages per business/customer/fixed UTC minute |
| `AI_CUSTOMER_ATTEMPTS_PER_HOUR` | `60` | Classifier and generation attempts combined, per customer/fixed UTC hour |
| `AI_CUSTOMER_ATTEMPTS_PER_DAY` | `200` | Per customer/fixed UTC day |
| `AI_BUSINESS_ATTEMPTS_PER_DAY` | `1000` | Shared per WhatsApp business phone-number ID/fixed UTC day |
| `AI_SPAM_THRESHOLD` | `3` | Third identical normalized message is suppressed or replaced by a bounded notice |
| `AI_SPAM_WINDOW_SECONDS` | `60` | Rolling repeat window |
| `AI_NOTICE_COOLDOWN_SECONDS` | `60` | At most one throttling notice per customer during this interval |
| `WHATSAPP_MAX_BODY_BYTES` | `1048576` | Streamed request-body limit; oversize bodies return 413 |

No paid model evaluation was run. The classifier model is configurable because
multilingual semantic quality must be evaluated for each business; mocks verify
routing, budgets and API contracts, not live classification accuracy.

Messages of 2001–4096 characters are persisted intact and receive a local request
to shorten them; no truncated text is sent to AI. The existing parser still ignores
text over 4096 characters. Signature verification uses the exact original bounded
body bytes. Application throttling never returns 429 to Meta; valid persisted
webhooks still acknowledge 200 before background work.

Run `python -m alembic upgrade head` before enabling this version. Migration
`20260925_0003` adds only `ai_budget_counters`: a composite key/window primary key,
a nonnegative count constraint and an expiration index. Conversation/message models
remain unchanged. Customer checks and each paid-stage reservation commit in short
transactions before any network call. Ordered row locks enforce shared limits
across workers; classification and generation each consume one attempt. Customer
limits span conversations. Database time, never WhatsApp event timestamps, controls
windows. Repeat detection stores a hash and at most the configured threshold of
timestamps. Up to 100 expired counter rows are removed per admitted new task with
`SKIP LOCKED`; old rows may remain while idle but cannot grant stale quota.

`Customer.is_blocked` suppresses automatic replies. Existing external-ID deduplication
is preserved, and a persisted inbound-stage claim also prevents a duplicate background
invocation from spending again. A crash after a claim/reservation may lose that reply
and conservatively consume quota; no automatic replay/refund is attempted.

Compact `Message.metadata.ai_guard` records the rule version, routing decision/path,
language, fallback status, stage timestamps and per-stage model/status/latency,
sanitized failure category and provider input/output/cached-input token counts.
Unknown usage (including timeouts) is null, not zero. Records belong to the inbound,
so WhatsApp send failure does not erase AI usage. Telemetry write failures are logged
without raw exceptions; a previously committed reservation remains as unknown usage.
No prompts/history/credentials are duplicated into telemetry, and API message
creation rejects caller-supplied `ai_guard` metadata.

Tagged spam, unrelated/injection records and guard notices are excluded from future
sales history. Obvious legacy injection is also removed from the AI window; other
legacy history remains untrusted. Pure extraction/general-assistant requests redirect
locally. Mixed requests only proceed when a clearly commercial independent clause can
be isolated; otherwise they clarify. Benign corrections such as 'ignore my previous
size, I need M' remain valid. Neither heuristic nor model guards guarantee immunity
to novel injection. The generation prompt independently enforces sales scope and
business-fact restrictions.

Remaining limits: fixed windows permit boundary bursts; API-attempt quotas and
character/output caps are not an exact monetary budget. Classification can be wrong,
and ambiguous commerce costs two calls. Reply generation remains concurrent and
in-process; no durable queue, delivery reconciliation or strict reply sequencing was
added. The business quota is per configured WhatsApp number, not a multi-number tenant.
No catalog/tools were added: shopping discovery asks about preferences/budget and
never invents products. Step 6 can supply verified catalog tools after admission.

Run the full backend suite against the migrated dedicated test database:

```powershell
python -m pytest -q
python -m compileall -q app tests
python -m alembic check
python -m pip check
```

`test_ai_admission.py` includes real competing PostgreSQL connections and cleans up
its committed test rows. All OpenAI and WhatsApp traffic remains mocked in tests.
