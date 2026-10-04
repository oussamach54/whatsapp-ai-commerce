# Step 7.5: WhatsApp images and trusted product links

## Architecture

This extends the existing signed WhatsApp webhook, inbound deduplication,
background reply task, AI admission service, catalog service, conversation `Turn`,
and COD checkout. It adds no alternate webhook, checkout engine, database table,
model-controlled tool, public frontend route, or human-takeover behavior.

```text
Signed webhook -> deduplicated inbound message -> admission claim
  image -> bounded authenticated Meta retrieval -> Responses observations
  own-store link -> parsed trusted origin/path -> active product by DB slug
                   -> bounded caption interpretation when needed
  external/malformed link -> local no-fetch explanation
                 |
                 v
Existing Turn -> existing catalog search/get -> DB facts + verified references
                 -> optional application-built product URLs
                 -> existing COD proposal/confirmation/finalization
```

The frontend currently contains a dashboard shell and legal pages, not a public
product page. Configure a real merchant storefront only when one exists. This
change does not create a storefront or point product links at the dashboard.

## Configuration

Both example environment files and Compose expose:

| Setting | Default | Behavior |
| --- | --- | --- |
| `WHATSAPP_IMAGE_MAX_BYTES` | `5242880` (5 MiB) | Bounded between 1 KiB and 10 MiB; rejects oversized metadata, headers or streams. |
| `WHATSAPP_MEDIA_TIMEOUT_SECONDS` | `10` | Positive, at most 30 seconds; shared elapsed-time deadline for metadata plus image retrieval, with bounded HTTP timeouts and checks between transport chunks. |
| `STOREFRONT_BASE_URL` | unset | Optional HTTPS merchant origin, such as `https://shop.example.com`. Empty means omit outbound links. |
| `STOREFRONT_PRODUCT_PATH_TEMPLATE` | `/products/{slug}` | Absolute path with fixed safe segments and one final `{slug}` segment; e.g. `/shop/item/{slug}`. |

Vision reuses `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS` (20 seconds
by default), and `OPENAI_MAX_OUTPUT_TOKENS` (512 by default). There is no second AI
client/model configuration. Configure a model that supports image input and
structured Responses output. The inspected deployment model, `gpt-5.4-mini`,
supports both according to its [official model documentation](https://developers.openai.com/api/docs/models/gpt-5.4-mini).
The existing SDK sends one `input_text` caption plus one base64 `input_image`
with `detail=low`, strict JSON output and `store=False`, following the
[Responses image-input guide](https://developers.openai.com/api/docs/guides/images-vision).

## Meta image retrieval and privacy

The parser accepts WhatsApp `type=image` events with a numeric media ID, optional
MIME and caption. Image-only messages use a local `[image]` placeholder. The
existing message enum already supports `image`, so no migration is needed.

The existing `WhatsAppClient` retrieves media metadata at
`https://graph.facebook.com/{configured-version}/{media-id}`, then downloads its
returned URL with Bearer authentication. Both requests prohibit redirects. The
download URL must have the exact HTTPS host `lookaside.fbsbx.com`, default/443
port, and `/whatsapp_business/attachments/` path; credentials, fragments, other
hosts, private IPs, alternate paths and non-HTTPS schemes are rejected. This
intentionally narrow Meta-host allowlist may need review if Meta changes its
official media delivery origin. See Meta's [media reference](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/media).

Supported types are **JPEG, PNG and WEBP** (`image/jpeg`, `image/png`,
`image/webp`). The declared MIME, metadata MIME, download Content-Type and binary
signature must agree. No filename extension is trusted. Metadata is capped at
16 KiB; image size is checked before and during streaming. Compressed HTTP
responses are rejected. A shared deadline is checked on transport chunks; each
request also receives only its remaining time as the HTTP timeout. The app does
not execute or decode image content, derive file paths from input, or create
temporary media files. Invalid/truncated image content can also be rejected by
the provider, resulting in a safe fallback.

Raw image bytes and base64 exist only in bounded memory for retrieval/inference;
they are **never stored in the application database, filesystem or logs**.
The application does not fetch customer-provided image URLs. It supplies image
bytes to the Responses API rather than revealing a signed Meta URL to OpenAI.
Provider-side retention is governed by the provider's account/data policies;
`store=False` is not a claim of universal zero retention.

The inbound message retains the media ID, MIME, caption and existing channel,
sender/conversation/message identifiers. These follow the existing message
retention lifecycle; this step adds no automatic deletion job. Raw structured
vision responses and OCR text are not persisted. Normalized search constraints,
verified focus/presented references and any verified cart proposal can remain in
existing conversation/checkpoint metadata. No frontend media viewer or preview
endpoint is introduced.

SDK/HTTP debug logging is suppressed only within the current sensitive-I/O
context. Other application logging continues. Provider bodies, exception strings,
Authorization headers, access tokens, signed download URLs and base64 are never
included in application diagnostics or customer error messages.

## Observations versus commercial truth

The strict `Observations` contract permits only:

- Up to six short product search terms.
- Optional short likely category (a hint, not a verified classification).
- Up to eight bounded attribute name/value observations.
- An ambiguity boolean.
- A bounded intent: discovery, availability, price, purchase, website ordering,
  or unknown.
- Speech act, literal caption evidence, optional caption-requested quantity
  bounded to 1-99, and supported reply language.

It has no price, stock, SKU, product ID, URL, policy, order-status or confirmation
fields. Extra keys and tool-call output are rejected. No free-form vision prose is
sent to customers. OCR/personal text transcription is deliberately omitted.
`CatalogAttributeAdapter` normalizes supported color/size requests; unsupported
attributes are discarded, never translated into database columns or SQL. Explicit
caption attributes override uncertain visual attributes. Caption budgets constrain
queries outside model control.

Images produce *possible catalog matches*, not proof of visual identity.
Ambiguous/unknown images request clarification; multiple verified candidates are
presented without silently selecting one exact product. Unmatched searches return
the existing honest catalog no-match reply. Current active products/variants and
DB prices/availability are read through the existing catalog operations. A
screenshot cannot establish that a website is down; the reply only offers
ordering directly in WhatsApp.

## Inbound and outbound product links

Inbound links use `urlsplit`, comparing HTTPS scheme, exact normalized hostname
and effective port against the configured origin. There is no subdomain suffix
trust. Userinfo and control/backslash characters are rejected. A matching path
must decode to one bounded canonical slug segment. Dot traversal, encoded path
separators, double-encoded escapes and nested paths are rejected. Query strings
and fragments never supply price, stock or identity overrides.

The slug resolves by exact parameterized SQL equality to an active product. That
verified product ID becomes the request target; caption observations cannot
override it. Variant/price/stock questions use fresh DB reads. A bare valid link
requires no model call; a natural caption may use one bounded `link_intent` call
through the same Responses client. Unknown slugs or malformed links ask for a
photo/name/details without inventing a match.

**External URLs are never fetched.** This includes localhost, internal/private
IP addresses, metadata endpoints, file/data/javascript schemes and attacker or
lookalike domains. The response asks for a photo or product details and makes no
claim about page contents. Multiple URLs are conservatively treated as unresolved.
Even trusted merchant pages are not scraped: commercial truth is already in DB.

Outbound URLs use the verified product's DB slug, configured origin and fixed
route template. The slug is percent-encoded as one path segment and cannot change
the origin/path structure. Model-generated URL fields are not accepted. Links are
added only for products actually verified and presented on this turn, in displayed
order; inherited focus or a failed lookup cannot generate a false link. Appended
links stay within the 4096-character reply limit. With no storefront configuration,
all ordinary commerce behavior continues without links.

Existing `presented != focus != selection` semantics remain in use. A recognized
referential choice such as `bghit hada` uses only the recent, verified,
unambiguous focus and re-reads the target. Failed URL lookup preserves trusted
state but marks the new target unresolved, preventing attachment of a subsequent
vague purchase to the old product.
An additive default-false flag in existing JSON conversation memory also blocks
bare attribute follow-ups from borrowing that older focus. A newly verified named
product or trusted link clears the flag; existing pending-cart consent still uses
its own independent provenance. No database migration is required for this flag.

## Checkout, failures and cost controls

Image/link context bypasses local cart-confirmation and single-missing-field
extraction. A caption can propose a purchase only with an affirmative speech act,
matching literal caption evidence and actual text; image-only, emoji-only and
bare acknowledgements cannot request purchase. Media never confirms a cart.
At the transaction boundary, confirmation provenance additionally requires an
inbound **text** message, so an image caption `oui` cannot authorize an order.
A URL-containing text is not accepted by the existing affirmative vocabulary.

The existing Step 7 checks remain: delivered proposal provenance, one affirmative
for an unchanged timely offer, 15-minute validity, fresh locked product/variant
price/stock checks, price-change reconfirmation, insufficient-stock blocking,
required checkout fields, source-cart uniqueness, transactional stock movements,
idempotent retry and completed-cart recovery. No stock is reserved by image
analysis or browsing.

Unsupported/oversized media, Meta failures and vision failures yield safe text
fallbacks while preserving the pending cart. An unrelated image cannot overwrite
checkout fields. No raw provider error is returned. Successful verified outbound
references remain usable by subsequent text turns; raw inbound link/vision hints
are excluded from later AI history.

The existing webhook external-ID lock/uniqueness prevents duplicate background
jobs. `AIAdmission.begin()` atomically claims processing once, including worker
retries. New `vision` and `link_intent` stages share existing customer/business
paid-attempt limits. One image causes at most one vision call; failed retrieval
conservatively consumes the reserved attempt. There are no automatic SDK retries.
The claim is not cleared on failure: a crash or failed send does not trigger
repeat paid analysis. A customer can send a new message to retry. There is no new
durable media retry queue or vision-result cache.

## Validation

Before editing, 775 baseline test identities were collected into ignored
`.validation/step75-baseline.txt`. Validation uses only the existing isolated
`confirmation_test` PostgreSQL database on port 15432, verified at Alembic
`20260929_0005`. Development data is not reset or used for pytest.

New test modules cover media transport, URL security, conversation/catalog/checkout
integration, real webhook deduplication/admission, and the mocked official SDK
payload. All Meta HTTP and OpenAI boundaries are mocked; an autouse fixture blocks
both HTTP transport packages from making real requests.

Final validation:

| Suite | Passed |
| --- | ---: |
| New media/parser/security tests | 31 |
| New product-link unit tests | 44 |
| New multimodal conversation/checkout tests | 65 |
| New webhook/admission/SDK tests | 5 |
| Focused Step 7.5 total | **145** |
| Existing related regression suites | **751** |
| Complete backend | **920** |

The complete run finished in 116.54 seconds with **0 failures and 0 skipped**.
The identity comparison against the saved pre-change collection found **all 775
baseline identities present and passing**, with exactly 145 added cases and no
missing or unsuccessful baseline cases. Reports and the comparison are in ignored
`.validation/step75-*.xml` and `.validation/step75-baseline-comparison.json`.

The 751-test related run covers WhatsApp integration/diagnostics (85), AI/admission
(48), catalog (159), conversations/history/recovery/API (106), generic attributes
(44), sales/scope/Step 7 flow (196), and checkout/concurrency/lifecycle/transport/
readiness/single-confirmation (113). All also pass in the final complete run.

Compilation, AST parsing of 123 Python files, unchanged two-tool catalog boundary,
read-only catalog static checks, `git diff --check`, explicit whitespace checks on
all 26 changed/new files, and `pip check` passed. Checkout readiness passed and
Alembic reports `20260929_0005 (head)` with no schema drift. Compose configuration
was checked to forward the new settings and preserve the literal `{slug}` template.
The only warning is the existing Starlette/AnyIO `BlockingPortal` deprecation.

The final diff review checked credential/media logging, URL/network boundaries,
commercial-fact authority, reference safety and order-consent provenance. No
baseline test assertions were weakened. There were **zero real WhatsApp requests
and zero paid OpenAI calls**. No migration, dependency upgrade, development-data
reset, commit or push was performed. The isolated test server is stopped after
validation, with its data and reports retained.

## Changed files

```text
.env.example
README.md
backend/.env.example
backend/app/ai/commerce_state.py
backend/app/ai/conversation_engine.py
backend/app/ai/multimodal.py
backend/app/ai/router.py
backend/app/api/routes/whatsapp.py
backend/app/core/config.py
backend/app/core/sensitive_io.py
backend/app/integrations/whatsapp/client.py
backend/app/integrations/whatsapp/media.py
backend/app/integrations/whatsapp/parser.py
backend/app/integrations/whatsapp/schemas.py
backend/app/services/ai_admission_service.py
backend/app/services/catalog_service.py
backend/app/services/checkout_service.py
backend/app/services/product_links.py
backend/app/services/whatsapp_service.py
backend/tests/conftest.py
backend/tests/test_multimodal_commerce.py
backend/tests/test_multimodal_media.py
backend/tests/test_multimodal_webhook.py
backend/tests/test_product_links.py
docker-compose.yml
docs/step-7.5-multimodal.md
```

## Manual WhatsApp E2E after deployment

Configure the real storefront only if public product pages already exist with
slugs matching DB products. Use a vision-capable configured Responses model and
valid Meta credentials. The examples below assume a verified Bleu/M product at
229 MAD; always expect the actual current DB price/stock.

1. **Image only:** send a clear photo of blue pants. Expect possible verified
   catalog matches or a clarification, with DB price/stock and no order.
2. **Image + caption:** send that image with `wach 3ndkom b7al hada?`, then separately
   try `ch7al hada?` and `kayn stock?`. Expect DB facts, never screenshot prices.
3. **Trusted link:** send `https://YOUR-STOREFRONT/products/YOUR-DB-SLUG wach kayn taille M?`.
   Specify color if more than one M variant exists; expect a verified reference.
4. **Agent link:** send `3ndkom pantalon bleu taille M ta7t 300dh?`. With configuration,
   the verified product reply includes its canonical merchant URL. Without it,
   the same commerce reply works without a URL.
5. **Image to checkout:** send the photo with `bghit hada taille M`. Resolve any
   product/variant ambiguity. After the verified cart proposal, send one **text**
   `oui` within 15 minutes. Expect only missing checkout fields; supply them and
   expect one order. Repeat `oui`; the same order must remain, without another
   stock decrement.
6. During a pending proposal, send an image captioned `oui` or send a product URL.
   Neither may confirm it. Send a separate timely text affirmative to continue.
7. Send an external URL and an oversized/unsupported image. Expect a safe request
   for photo/details or smaller/supported media; the pending cart must survive.
8. On controlled test inventory, change price after a proposal, reduce stock, and
   let an offer truly expire. Expect reconfirmation, blocking and refreshed consent
   respectively. No cleanup/reset of development data is required by this change.

V1 does not provide visual embeddings/exact image matching, external browsing,
OCR transcripts, outbound images, automatic retries, product-page hosting,
multi-tenant storefronts or website-health monitoring. Real provider/model quality
and merchant page availability remain manual deployment checks.
