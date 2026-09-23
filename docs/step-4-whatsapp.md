# Step 4: WhatsApp Business Cloud API

Implementation is available for local verification. It is not live and has not
been tested against Meta. No AI, n8n, authentication, or tunneling is added.

## Configuration

Set these variables in the backend environment (or root `.env` for Compose):

- `WHATSAPP_VERIFY_TOKEN`: your private webhook verification token.
- `WHATSAPP_APP_SECRET`: Meta app secret, used for incoming HMAC signatures.
- `WHATSAPP_ACCESS_TOKEN`: Meta access token with permission to send messages.
- `WHATSAPP_PHONE_NUMBER_ID`: the business phone-number ID, not its phone number.
- `WHATSAPP_API_VERSION`: explicitly selected supported Graph API version,
  formatted `vNN.N`. No hardcoded version default is assumed.

Settings may be absent while using the existing API or health endpoint. GET
verification requires the verify token; POST requires the app secret and
phone-number ID; processing text events also requires outbound configuration.
Missing configuration returns a sanitized 503. Status events need no access token.
Secrets use Pydantic SecretStr and are never included in application log messages.
Examples contain blank values only. Dependencies and Python version are unchanged.

## Meta setup, when ready

Configure the callback URL as:

`https://YOUR_DOMAIN/api/webhooks/whatsapp`

Enter the same verify token configured on the backend and subscribe to the
WhatsApp Business Account `messages` webhook field. Meta's GET challenge uses
`hub.mode=subscribe`, `hub.verify_token`, and `hub.challenge`. Valid verification
returns the raw challenge as text with HTTP 200; invalid/missing query arguments
return 403. GET does not require a signature.

A public HTTPS URL with a valid certificate is required. A localhost FastAPI URL
cannot directly receive Meta callbacks. Arrange hosting/public HTTPS separately;
no tunnel is installed or configured by this step.

POST validates `X-Hub-Signature-256` as `sha256=<hex HMAC>` over the exact raw
HTTP bytes using the app secret and constant-time comparison. Missing, malformed,
or invalid signatures return 403 before payload processing. Signed invalid JSON
returns 400. Signed status-only, unknown, empty, and unsupported media events
return 200 without replies. Events for other phone-number IDs are ignored.

Reference: [Meta webhook SDK verification behavior](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/),
[Meta webhook payload reference](https://www.postman.com/meta/whatsapp-business-platform/folder/tduohwq/webhook-payload-reference),
and [Meta text-message request format](https://www.postman.com/meta/whatsapp-business-platform/request/0arw2jw/send-text-message-with-preview-url).

## Persistence and idempotency

Text messages store the original sender number, text, external WhatsApp message
ID, provider metadata, and provider timestamp when present. Invalid individual
message shapes are ignored without blocking valid siblings in the same payload.
Customer resolution lives in the existing customer service and uses a PostgreSQL
upsert plus a customer row lock. No name is required. Exact phone strings are
preserved; pre-existing alternate formatting is not normalized or merged.

The conversation service reuses the oldest WhatsApp conversation with status
`active` or `waiting_human`, ordered by creation timestamp and UUID. When none
exists, it creates an active conversation. The customer lock serializes concurrent
webhook conversation creation. Closed conversations are not reopened.

The existing nullable `Message.external_message_id` originally had a nonunique
index. Migration `20260912_0002` replaces only that index with a unique index;
multiple NULL values remain valid. No columns or unrelated tables change.
Existing duplicate non-null IDs cause migration failure without deleting data.
An operator must investigate such duplicates before retrying the migration.

A transaction-scoped PostgreSQL advisory lock derived from the external message
ID serializes duplicate deliveries. Under the lock, an already stored ID is
ignored before customer/conversation creation and reply scheduling. The database
unique index is the final safeguard, including other API writers. Text or
timestamps are never used as idempotency keys.

Each inbound message and its customer/conversation changes commit atomically.
Only newly persisted messages schedule a reply. The acknowledgement is sent
before outbound HTTP work using FastAPI background tasks and a separate database
session, so provider failures never roll back inbound messages.

## Replies and failure limits

The automatic reply is:

`Bonjour, votre message a bien ?t? re?u.`

The pooled HTTPX client uses the configured version and phone ID at
`https://graph.facebook.com/{version}/{phone_number_id}/messages`, a Bearer token,
and the Cloud API text-message body. Connect timeout is 3 seconds and other I/O
timeouts are 5 seconds. Redirects are disabled and failures are not retried.
Non-2xx, transport, and malformed provider responses produce sanitized domain
errors without response bodies or tokens.

Successful sends create outbound `system` / `text` messages with the returned
external ID and inbound reference in metadata. Failed sends are logged safely;
inbound data remains committed, and redelivery does not send another reply.
Delivery/read/sent status updates are logged and ignored, not persisted.

Background tasks are in-process, not a durable queue. A process crash after
inbound commit may lose a pending reply. A batch failure after an earlier inbound
commit can likewise leave that earlier message without a scheduled reply.
A successful Meta send followed by database failure may leave the outbound row
missing. These cases are logged where possible, are not retried automatically,
and do not provide exactly-once delivery to Meta. A durable outbox/recovery policy
is deferred. No claim of guaranteed delivery is made.

## Local tests

Automated tests make no requests to Meta. Webhook tests inject a mocked sender
and prohibit real HTTP transports; client tests use HTTPX MockTransport. Test
secrets are generated at runtime and are not real credentials.

Set `TEST_DATABASE_HOST`, `TEST_DATABASE_NAME`, `TEST_DATABASE_USER`, and
`TEST_DATABASE_PASSWORD` explicitly for a separate PostgreSQL test database.
`TEST_DATABASE_PORT` defaults to 5432. Do not substitute the development database.
Missing test settings fail database tests explicitly; there is no SQLite fallback.

From `backend`, with DATABASE_* deliberately targeting that same separate test
database for the migration commands, run:

```powershell
.venv/Scripts/alembic.exe upgrade head
.venv/Scripts/alembic.exe downgrade -1
.venv/Scripts/alembic.exe upgrade head
.venv/Scripts/python.exe -m compileall app tests
.venv/Scripts/python.exe -m pytest -rs
.venv/Scripts/alembic.exe current
```

Migration commands read DATABASE_*; tests read TEST_DATABASE_*. Check both targets
before running the round trip. Do not downgrade a live database for verification.
From the project root run `docker compose config --quiet`.

Tests cover verification challenge, raw-body signature tampering, malformed
signatures/JSON, ignored events, text route scheduling, PostgreSQL persistence,
customer/conversation reuse, duplicates, database uniqueness, reply failure,
request formatting, timeout, provider errors, and missing configuration.

For a local server use:

```powershell
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --no-access-log
```

Disable query-string logging in any reverse proxy too: verification puts the
verify token in the URL. The Docker command disables Uvicorn access logging for
this reason. Do not enable HTTP wire/header debug logging with live credentials.
Both webhook endpoints are listed under the WhatsApp OpenAPI tag.

## Real Meta validation still required

Configure real app/phone credentials and a supported API version, apply the
migration after test validation, configure public HTTPS, verify the callback and
messages subscription, send a real inbound text, confirm the automatic reply and
database rows, and inspect delivery/status and duplicate handling. This has not
been performed by the implementation or automated tests.
