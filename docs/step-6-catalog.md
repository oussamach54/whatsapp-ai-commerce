# Step 6: Catalog intelligence

## Deployment boundary

This version supports **one business/catalog per PostgreSQL database and deployment**.
It is NOT safe for multiple businesses sharing a database. There is no partial
`business_id` implementation. Admin APIs still require deployment-level access
control; this change does not introduce admin authentication.

Catalog reads validate the persisted inbound ID, conversation ID and configured
`WHATSAPP_PHONE_NUMBER_ID`. Missing/mismatched scope, or a conversation containing
inbound messages with another/missing receiving number, fails closed before paid
commercial generation. Social/unrelated local paths remain zero-call paths.

`CATALOG_CURRENCY=MAD` is the explicit deployment currency (and documented default).
Configuration rejects other values, lowercase values and blank values. All existing
variant prices are interpreted in MAD; no customer text selects the catalog currency
and no currency conversion is performed. Explicit EUR/USD requests receive a local
unsupported response; other unsupported requests use the constrained plan.

## Migration and admin attributes

`20260927_0004_catalog_variant_attributes.py` follows `20260925_0003` and adds only:

- `product_variants.size VARCHAR(64) NULL`
- `product_variants.color VARCHAR(64) NULL`

No defaults, inferred backfill, renamed variants, tenancy tables or memory tables.
Existing rows remain NULL until explicitly updated. Downgrade drops these columns.

Create/PATCH schemas and catalog argument schemas share normalization:

1. Reject non-text values and Unicode control/format characters.
2. Unicode NFKC normalization, trim and collapse whitespace.
3. Empty/whitespace-only values become NULL.
4. Size is uppercase (`ｍ` -> `M`); color is casefolded (`BLEU Marine` -> `bleu marine`).
5. Common exact color aliases map to French canonical values:

| Canonical | Additional aliases |
|---|---|
| noir | black, k7el, كحل, أسود |
| blanc | white, byed, بيض, أبيض |
| bleu | blue, zre9, زرق, أزرق |
| rouge | red, 7mer, حمر, أحمر |
| vert | green, khder, خضر, أخضر |

Other colors retain normalized text; compound colors are not heuristically parsed.
Normalized values are limited to 64 characters. Variant names are preserved.
NULL is unknown, never an inferred size/color from a name or description.

## Read-only tools

Only these two names are registered, with strict schemas (`additionalProperties:
false` recursively). Provider schemas require all properties; optional values use
null. Local validation additionally enforces defaults, lengths, types and ID rules.

```text
search_products(
  terms: string[0..6],                 # each 1..64 characters
  category: string|null,               # <=255 characters
  brand: string|null,                  # <=255 characters
  max_price: decimal-string|null,      # nonnegative, <=10 integral/2 fractional digits
  in_stock_only: boolean,              # local default true
  size: string|null, color: string|null,
  limit: integer                      # local default 3, maximum 5
)

get_products(
  product_ids: UUID[0..3],
  variant_ids: UUID[0..6],             # at least one ID overall
  size: string|null, color: string|null # normalized filters, applied before LIMIT
)
```

Application scope is never a model argument. No SQL, column selection, arbitrary
sort, offset, write operation, inventory movements or network tools are exposed.

Search uses escaped, parameterized literal ILIKE across product name/category/brand/
description and variant name/SKU. All terms are required; each term may match any
of those fields. Product fields, all terms, active status, stock, price and structured
attributes are evaluated against the same eligible variant. Category/brand are
additional literal filters. Rank weights per term: exact SKU 200, exact product name
100, partial name 20, category 10; ties use product creation time then UUID. Variant
order is price then UUID. There is no embedding service or extension dependency.

The planner can translate/interpret multilingual requests into bounded query terms;
its interpretations are not proof of facts or suitability. Uncertain gift requests
can ask one budget/preference question. No free-form suitability or promotion claims
are emitted. No conversion or automatic budget relaxation is allowed. Explicit
numeric dh/MAD/dirham budgets are also clamped in application code across tool rounds.

Active products and active variants only. Discovery with `in_stock_only=false` and
no variant constraints can return an active product with no active variants. Detail
lookup distinguishes a found product from its available variants. Unknown/inactive
IDs return a safe not-found result; a supplied variant must belong to supplied
parent IDs. Variant price/stock always come from current PostgreSQL reads.

Product-scoped detail queries include zero-stock variants. Optional size/color
filters apply in SQL before result limits. A found product with no matching variants
and `has_more_variants=false` proves no matching active variant; an empty in-stock
discovery search does not prove variant nonexistence.

Results contain bounded DTOs with IDs, names, descriptions, category, brand and
variants (ID, parent ID, SKU, name, price string, size, color, stock quantity,
`in_stock`/`out_of_stock`). Currency is application configuration. No compare-at
price/promotion, inventory history, image fetching or internal customer data.

## Bounds and orchestration

| Resource | Bound |
|---|---|
| Search products | Default 3, maximum 5 |
| Search variants | Maximum 3 per product |
| Detail variants | Maximum 6 across a detail result |
| Description | Search 200 / detail 600 characters |
| Tool arguments | Maximum 3,000 serialized characters |
| Tool result | Maximum 8,000 serialized characters |
| Customer list | Maximum 3 products, normally 2–3 |
| Final displayed variants | Maximum 6 total |
| Model tool executions/rounds | Maximum 2 |
| Generation calls | Maximum 3 including final plan |
| Classifier calls | Existing maximum 1 |
| Catalog orchestration deadline | 45 seconds |

Parent/nested SQL queries are bounded. Tool serialization drops whole records or
variants with truncation flags rather than cutting JSON. Descriptions are bounded
in SQL before materialization. More variants are never presented as nonexistent.

The existing scope guard/classifier precedes orchestration. The production
`get_ai_service` dependency enables the catalog path; standalone legacy AIService
clients remain available to existing transport tests. There is no runtime environment
switch exposing unrestricted catalog prose in the production dependency.

Responses uses exactly two tools, strict response plans, `parallel_tool_calls=false`,
`store=false`, no SDK retries, and typed response items/function outputs linked by
`call_id`. Reasoning items (including encrypted content when returned) are preserved
only for the current turn. After two tool executions, tools are disabled; additional
calls, malformed plans, unknown tools, or unexpected hosted actions fail safely.

Each network timeout is capped by remaining orchestration time. SQL queries have
short statement/lock timeouts; catalog statement timeouts shrink with the remaining
deadline. Deadline checks prevent starting another provider call or rendering a late
answer. Synchronous connection acquisition/cleanup can still add latency; this is not
a durable task queue or hard process-level cancellation mechanism.

Existing input/history/output limits remain. The transcript is bounded within a
turn and is not persisted in conversation history. A small final refresh of selected
identities is application-owned verification, not another model tool execution.

## Factual output

The model returns only `action`, `items`, `question`, `reference`. Actions are show,
compare, select, clarify, no_matches, unsupported. Questions are budget, preference,
product, variant; references are none, focus, first, second, third, pair.

There is no unrestricted customer-facing model prose. Application templates render
current product/variant names, prices, MAD currency, structured attributes and stock
states. Candidate IDs are validated; selected records are refreshed before rendering,
and query constraints are checked again. Comparisons show verified facts only.
Descriptions never reach the renderer. Compare-at price does not imply a promotion.
Names remain literal catalog labels; owners are responsible for catalog data quality.

Templates cover Darija Latin, Arabic-script Darija, French, English and mixed
Darija/French, using the existing language/scope decision. Model semantic quality
and language preference detection still need live evaluation; automated tests mock
all provider calls and do not establish live-model interpretation accuracy.

## Follow-up references and Step 7

Successful outbound messages can contain application-owned `catalog_refs` JSONB:

```json
{
  "version": 1,
  "presented": [{"product_id": "UUID", "variant_id": null}],
  "focus": {"product_id": "UUID", "variant_id": null},
  "selection": {
    "product_id": "UUID",
    "variant_id": null,
    "source_message_id": "UUID",
    "resolution": "product"
  }
}
```

Only identities/order and selection state are stored, never catalog snapshots.
Presentation order is the actual rendered order. Public message creation rejects
`catalog_refs` and `ai_guard`. Reads use eligible sent messages in the same
conversation before the inbound anchor; malformed/missing metadata, intervening
inbound turns, ambiguous focus and unavailable records prompt clarification.
No identities/prices are reconstructed from assistant prose.

Clear short variant follow-ups (`w taille L?`, `et taille L?`, `et en taille L?`,
`taille L?`, `w noir?`, `et en bleu?`) use an application-owned path without a
classifier or generation call. The parser matches complete short size/color
questions, not substrings of new/broadened shopping requests. Common clothing size
codes and numeric sizes, plus a small color vocabulary using existing normalization,
are recognized; other phrasing remains available to the planner.

With a valid variant focus, read its CURRENT structured attributes, replace the
requested attribute, and preserve the other when known. Query siblings through
the parent product ID; do not restrict the new lookup to the old variant ID. The
last detail query is final revalidation and includes stock zero. A unique match
becomes the new variant focus through the unchanged renderer/reference format.
Multiple matches retain product-level focus rather than arbitrarily selecting a
variant. Missing/ambiguous focus prompts clarification.

Thus Noir/M -> `w taille L?` can render Noir/L, 249 MAD, out of stock; Noir/M ->
`w taille XL?` renders a distinct variant-not-found reply; Noir/M -> `w bleu?`
resolves Bleu/M. Missing/inactive parent products have a separate unavailable reply.
A following `ch7al taman dyalha?` retains the resolved variant identity and re-reads
its price. References never retain prices or stock quantities.

When the planner recognizes an attribute follow-up after an empty in-stock search,
the application performs the same stock-inclusive focused lookup before accepting
no_matches. Detail revalidation supersedes discovery's stock-only restriction while
preserving other applicable customer constraints. Ordinary discovery remains
unchanged and may still exclude zero-stock variants. No new tool or migration is
needed for this distinction.

Ordinal selection preserves the exact identity level of the displayed item; it
does not silently choose a variant. A product-level selection asks for size/color.
Unknown attributes stay unknown. Every factual follow-up re-reads current records.

References are persisted only after WhatsApp accepts the outbound send. Failed
sends store no reference. If send succeeds but DB persistence fails, there is no
automatic resend; a later follow-up may need clarification. No quoted-message
support or distributed conversation queue was added; overlapping turns are handled
conservatively rather than guessing which list the customer meant.

Step 6 selections are NOT orders, stock reservations, or frozen prices. Step 7 must
revalidate active identity, currency, price and quantity and perform transactional
stock handling. Existing order services were not changed or exposed as tools.

## Admission, sessions and failures

Paid attempts use `classifier`, `generation`, `generation_2`, `generation_3`.
Every call reserves existing business/customer counters atomically before network
work. Continuations require a completed predecessor; duplicates are suppressed;
failed calls stay counted. Telemetry records per-call usage/status/latency and safe
failure categories, with no prompts or tool transcripts. Storage failure stops paid
work; recording failure prevents continuation.

Catalog/history/admission sessions close before provider calls. Inbound FastAPI
database dependencies use function scope, so their sessions close before background
OpenAI/WhatsApp calls too. Outbound persistence opens a new short transaction after
the send. Valid webhooks are acknowledged before background catalog work.

Catalog/provider/validation failures use a localized unavailable/clarification
response without claiming stock, price or an order. Wrong/unknown scope suppresses
commercial generation. Social/unrelated paths, signature verification, inbound
idempotency, spam/rate controls and legacy fallback behavior remain covered by tests.

## Validation

The new catalog suites cover database filtering/bounds/attributes, strict tools,
factual rendering, references, orchestration, atomic admission, mocked SDK wire
contracts, successful/failed outbound persistence, webhook acknowledgment,
duplicates, and DB/network session lifetimes. Existing PostgreSQL and Step 1–5
regression suites remain part of the full backend run. Tests block real OpenAI
transport requests and use the separately migrated test database.

Run from backend with TEST_DATABASE_* set for the dedicated test database:

```powershell
python -m pytest -q
python -m compileall -q app tests alembic
```

Validation completed on 2026-09-27:

- Reviewed and applied migration `20260927_0004` to development and the existing
  dedicated `whatsapp_ai_commerce_test` database using the backend virtual environment.
- Initial focused catalog run: 27 passed. Expanded catalog run: 60 passed.
- Final full backend suite (including additional boundary tests): **328 passed**
  in 24.25 seconds. No paid OpenAI requests; the transport blocker remained enabled.
- `python -m compileall -q app tests alembic`: passed.
- `python -m alembic current`: `20260927_0004 (head)` on development.
- `python -m alembic check`: no new upgrade operations detected.
- `git diff --check`: passed; Git emitted only existing Windows line-ending notices.
- One dependency deprecation warning: Starlette's use of AnyIO `BlockingPortal`.
- Docker socket access was unavailable in the sandbox. Direct PostgreSQL and the
  existing Python virtual environment worked, so validation and migration were
  completed without Docker, dependency installation, or permission changes.

Intermediate regressions in local social routing and an incomplete classifier mock
were corrected before the final passing run. No outstanding test failures.

Variant-follow-up regression fix validation (2026-09-27):

- Focused catalog suites: 99 passed before the final three pipeline/reset cases.
- Final full backend suite: **368 passed** in 32.99 seconds, including **34 new
  variant-follow-up regression cases** in `test_catalog_variant_followups.py`.
- Compilation and whitespace checks passed; the existing AnyIO deprecation warning
  remains. No real OpenAI requests or WhatsApp sends were made by tests.
- The existing PostgreSQL container was started to run the tests; no migrations
  were created or applied. Reference persistence/loading and Step 7/order behavior
  were not changed.

This fix changes only `app/ai/catalog_schemas.py`, `app/ai/catalog_tools.py`,
`app/ai/catalog_orchestrator.py`, `app/ai/catalog_renderer.py`, `app/ai/router.py`,
`app/services/catalog_service.py` (all under `backend/`), the new regression test
module, and this documentation.

## Files introduced or modified by Step 6

Existing uncommitted Step 1–5 work was preserved. This manifest describes this step,
not every file shown by Git status in the shared working tree.

New implementation files:

- `backend/app/services/catalog_service.py`
- `backend/app/ai/catalog_tools.py`
- `backend/app/ai/catalog_schemas.py`
- `backend/app/ai/catalog_renderer.py`
- `backend/app/ai/catalog_orchestrator.py`
- `backend/app/schemas/catalog_attributes.py`
- `backend/alembic/versions/20260927_0004_catalog_variant_attributes.py`

Modified implementation/configuration files:

- `backend/app/models/product.py`
- `backend/app/schemas/product.py`
- `backend/app/core/config.py`
- `backend/app/api/dependencies.py`
- `backend/app/ai/client.py`
- `backend/app/ai/dependencies.py`
- `backend/app/ai/router.py`
- `backend/app/ai/schemas.py`
- `backend/app/ai/service.py`
- `backend/app/services/ai_admission_service.py`
- `backend/app/services/conversation_service.py`
- `backend/app/services/whatsapp_service.py`
- `.env.example`
- `backend/.env.example`
- `docker-compose.yml`

New tests:

- `backend/tests/test_catalog_service.py`
- `backend/tests/test_catalog_tools.py`
- `backend/tests/test_catalog_renderer.py`
- `backend/tests/test_catalog_references.py`
- `backend/tests/test_catalog_orchestration.py`
- `backend/tests/test_catalog_admission.py`
- `backend/tests/test_catalog_pipeline.py`
- `backend/tests/test_catalog_webhook.py`

Shared fixtures were added to `backend/tests/conftest.py`. Documentation changes are
`README.md` and this new `docs/step-6-catalog.md`. No frontend, order service,
historical migration, local secret environment file or dependency file was changed
by Step 6.
# Conversational explanation and selection safety

Conversational understanding, verified commercial facts and action authorization
are separate boundaries. Common misunderstanding/meaning-request families are
recognized locally after scope/security checks. Other paraphrases use the existing
bounded planner, whose `explain` action has only state, availability, price and
out-of-stock topics. No additional paid classifier or catalog tool is introduced.

Explanation resolves the eligible persisted focus and fresh-reads its exact
variant. Application-owned multilingual sentences render current DTO facts;
assistant prose is never catalog evidence. Explicit stock-term questions receive
a fixed definition followed by the current verified state, including when stock
has changed. Missing, ambiguous, inactive or unsupported explanation targets
clarify without fabricating facts or discarding prior references. Product-only
focus asks which variant rather than guessing.

Focus means the subject of conversation; selection means an explicit customer
choice. Explanation preserves presented/focus/selection metadata and never creates
a selection. A planner `select` alone cannot authorize selection: application code
requires a complete affirmative choice utterance and validates its referenced or
named variant target. Questions, negation, quotations, examples and uncertain
formulations cannot authorize it. The conservative recognizer deliberately asks
for clarification on unfamiliar choice wording. Recommendations and showing
products never create selection. Step 7 must still establish order consent;
selection is neither an order nor a reservation. Historical malformed selections
are not retroactively repaired by this change.

Rendering is deterministic in Darija Latin, Arabic-script Darija, French, English
and mixed style. Common acknowledgements remain local. No unrestricted model
catalog prose, migrations or checkout behavior are added.
