# Step 7 COD implementation record

## Inspection before implementation

Baseline: 581 passing tests. Existing Order/OrderItem models, COD/payment/order
status enums, order API and order service already exist. Items snapshot product
name, variant name, SKU, unit price, quantity and line total. Orders snapshot
shipping information, subtotal, shipping cost and total. Customer has a unique
phone plus optional names; no customer address or city. InventoryMovement already
records stock changes; its service locks variants. Existing order creation does
not check activity/stock, decrement stock or provide idempotency. No automatic
stock lifecycle exists in order status updates. Transaction helper owns commit
and rollback. Webhooks serialize duplicate external IDs with advisory locks;
messages have unique external IDs. Conversation locks and receipt ordering reject
superseded responses. Delivered outbound metadata holds Step 6 state.

Read-only incident inspection found the actual multi-item text with two spaces
after `2`. Its generation completed, then the application recorded
`catalog_unavailable`. The Step 6 contract permits one quantity and one attribute
map, rejects duplicate attribute names, and has no cart/items representation.
It cannot represent Noir/M x2 plus Bleu/M x1. The exception category erased the
specific validation exception and raw model output was not stored, so its exact
runtime exception cannot honestly be reconstructed. The representational defect
and fallback boundary are established; tests must reproduce this gap without
claiming an unavailable provider trace.

The discount incident is fully explained by stored structured state: the model
correctly returned business_question/discount/quantity=3, but also supplied an
attribute named quantity. execute_intent checked unsupported attributes BEFORE
dispatching business questions. This produced a variant clarification and
persisted quantity as an unsupported attribute, polluting the following turn.

## Design decision

Extend the existing intent contract and Turn, not a second conversation engine.
Use bounded application-owned cart state, with durable checkpoints in existing
inbound message metadata. Delivered summaries establish confirmation provenance.
Finalization runs after admission accounting, inside a short transaction holding
the conversation row and ordered product/variant locks. Reuse Order, OrderItem
and InventoryMovement. Decrement stock only with a successfully persisted COD
order; no reservation and no implicit restocking on status changes.

A minimal additive order migration is necessary: unique nullable source cart ID
plus source conversation/message references provide database-level idempotency.
Nullable shipping name/address/city support businesses that do not require those
fields. Nullable shipping cost/total distinguish unknown delivery fees and final
payable amount from verified zero/free shipping. Product subtotal is always
known. Existing API creates retain their explicit shipping amounts and behavior.
No new tables or catalog JSONB migration. Deployment configuration controls
required checkout fields and optional verified shipping amount; unknown policies
remain unknown. Test database migration is needed for validation; the live
database will not be migrated during implementation.

## Resumed review and validation

The interrupted working tree was preserved. The initial targeted run passed 78
tests and the initial complete backend run passed 659 tests. Stronger synchronized
workers exposed a recovery bug: both workers could read the original checkpoint,
then the second worker rejected its stale checkpoint after the first committed.
The stock/order invariants held, but the second reply falsely implied failure.
Finalization now recovers a scoped existing order before rejecting a stale draft.

The order, items, stock updates, inventory movements and completed inbound
checkpoint commit together. Outbound WhatsApp work runs afterward. Failed sends
or outbound-message persistence cannot roll back that order. Same-inbound worker
retry and a later customer confirmation recover the same order. Webhook redelivery
remains ignored by external message ID. There is no automatic outbound retry queue.
Post-commit rendering failures also recover success from durable order/checkpoint
data. If the database cannot establish the outcome, the reply says status cannot
be verified rather than claiming that the order failed.

## Configuration and behavior

`CHECKOUT_REQUIRED_FIELDS` is a JSON list. Its default is
`["customer_name","phone","city","address"]`; supported entries also include
`delivery_note` and `postal_code`. Only missing fields are requested. The trusted
channel customer phone and an available profile name are reused. Literal customer
values can replace the delivery phone without changing the channel identity.
`CHECKOUT_COUNTRY` defaults to `MA`. Optional `CHECKOUT_SHIPPING_COST` must be a
verified nonnegative decimal; unset means unknown, and zero means free delivery.
For Docker, required fields and country are passed by Compose; set a verified
shipping amount in `backend.environment` using a local Compose override.

The existing Turn owns a cart of up to six distinct variants, with quantity 1–99
per line. Proposed multi-item requests resolve every line before presenting a
summary. Repeated variants merge quantities. Add/remove/replace/set/increase/
decrease edits invalidate confirmation; ambiguous edits ask for clarification.
Draft cancellation does not affect stock. Completed-order cancellation directs
the customer to the team without changing the order or restoring stock.

Consent requires a delivered WhatsApp summary for the exact cart ID/version and
a subsequent recognized affirmative customer message within 15 minutes of the
offer. Missing fields may then be collected. All product activity, stock, prices
and required fields are checked again at finalization. Price or shipping-fee
changes require a new summary and confirmation. Unavailable/insufficient lines
block the entire order. Recommendation references can become a purchase proposal.
Discount/policy questions dispatch before catalog attribute validation; no
unverified discount, delivery deadline, warranty or return policy is invented.

Finalization locks the conversation, then products and variants in stable UUID
order using PostgreSQL `FOR UPDATE`, refreshing ORM values. For each line it
subtracts the quantity from the locked variant and records one negative
`InventoryMovement` with reason `cod_order_created` and the order reference.
All lines succeed or all changes roll back. The unique nullable `source_cart_id`
is the database idempotency backstop. Existing manual order API behavior remains
unchanged; stock decrement guarantees described here apply to COD checkout.

Checkout values live in existing message metadata and order shipping snapshots.
The model receives missing-field names and product lines, not persisted checkout
values. Recognized private inbound messages are excluded from subsequent AI
history. The current raw message may reach the model when semantic extraction is
needed; this is not a claim that checkout PII never reaches the provider. A single
remaining free-text field is collected locally when the local routing rules allow.

## Migration review

`20260929_0005_cod_order_provenance.py` follows `20260927_0004` and adds no tables.
It adds nullable UUID `orders.source_cart_id` with unique constraint
`uq_orders_source_cart_id`, plus nullable `source_conversation_id` and
`source_message_id` foreign keys with `ON DELETE RESTRICT`. It makes
`shipping_full_name`, `shipping_address_line`, `shipping_city`, `shipping_cost`
and `total` nullable. Existing money types, nonnegative checks and subtotal remain.
No backfill or fabricated shipping amount is introduced.

The migration is a small extension of existing order storage: provenance supplies
idempotency/audit links; nullable fields support configured requirements and
unknown delivery fees. Schema downgrade removes provenance and restores NOT NULL.
It deliberately refuses data with null delivery fields instead of inventing values.
Operators must reconcile such rows before downgrade and retain provenance backups
if needed. PostgreSQL transactional DDL preserves schema and data on refusal.

Validation used an isolated PostgreSQL 16.2 instance on `127.0.0.1:55432` because
the installed PostgreSQL service was stopped. Only dedicated test databases were
migrated. Full-chain upgrade, 0005 downgrade/re-upgrade, legacy-compatible order
preservation, safe refusal of null-containing orders and Alembic drift checks passed.

The existing Python 3.13 environment contained seven cp314 distributions. They
were reinstalled at the same versions with cp313 wheels; no requirements changed.
Compilation and AST parsing of 115 Python files passed, as did trailing-whitespace
checks and the read-only model-tool boundary checks. No standalone static-check
script is present in this working tree.

Final validation on 2026-09-29:

- Targeted checkout suite: **83 passed, 0 failed, 0 skipped** (21.60 seconds).
- Complete backend suite: **664 passed, 0 failed, 0 skipped** (101.65 seconds).
- The **581 existing tests all pass**; no existing test expectations were changed
  during this resumed validation. The other 83 tests belong to the two COD modules.
- Added five cases during resumption: send failure with independent commit/retry
  verification, outbound persistence failure, post-commit rendering failure,
  multi-line rollback, and delivery-fee change. Strengthened the existing two
  concurrency cases with a barrier immediately before finalization and required
  both duplicate-cart workers to recover success.
- `compileall`, AST/read-only-boundary checks, `git diff --check`, explicit
  untracked-file whitespace checks, Compose configuration and `pip check`: passed.
- Alembic current/head: `20260929_0005`; schema check: no upgrade operations.
- One pre-existing Starlette/AnyIO `BlockingPortal` deprecation warning remains.
- No real WhatsApp sends or paid OpenAI requests were made. The isolated test
  PostgreSQL process was stopped after validation; development was not migrated.

## Next manual WhatsApp E2E sequence (not executed)

Use a test business number and isolated seeded catalog after deployment migration.
Set Noir/M to 249 MAD, stock 5; Bleu/M to 229 MAD, stock 3; Noir/L stock 0. Use
default required fields and unknown shipping cost. Reset synthetic stock/data
between scenarios; compare order IDs, item counts and inventory movements.

1. Send `salam bghet n commandé pantalon noir taille M`; expect 249 MAD summary.
   Send `oui`; expect only missing name/city/address, then send
   `Oussama, Casablanca, 12 rue Test`. Expect one confirmed COD order and stock 4.
   Send `oui` again and replay its signed webhook: still one order/decrement.
2. Start a new purchase: `bghit 2 pantalon noir M et 1 bleu M`; expect two lines
   totaling 727 MAD. Confirm and supply any missing data; expect one two-item order.
3. On a fresh draft send `ajoute un bleu M`, `mets trois noirs M`, `retire le bleu M`,
   then `remplace le panier par deux bleus M`. Check each revised summary and
   renewed confirmation. Send `annule`, then `oui`: no order/decrement.
4. Browse pants, send `3ndi 240dh chno katnsa7ni?`, then `je le prends`.
   Expect Bleu/M proposal, followed by confirmation and only missing fields.
5. Ask `ila khdit 3 tn9ess lia taman?`, delivery time and return-policy questions.
   Expect honest unknown-policy answers, no discount or variant clarification.
6. Change Noir/M price from 249 to 259 after its summary, then confirm. Expect a
   revised summary and no order until a new confirmation. Repeat the change while
   awaiting the last checkout field. Repeat with stock reduced below quantity:
   expect the whole multi-item order blocked and no partial decrement.
7. Set one variant's stock to 1. Two customers obtain summaries and complete
   required data, then send confirmations together. Expect one order, stock 0,
   one movement, and an unavailable/stock answer for the other customer.
8. In a controlled staging fault-injection run, fail the sender only after order
   commit. Inspect the committed order/checkpoint and stock before restoring the
   sender. Replay the webhook, retry the background worker, then send a new `oui`.
   Expect recovery of the same order and no extra decrement or failure claim.
9. Repeat with required fields `["phone"]`, then `["customer_name","phone","city"]`.
   Verify trusted phone reuse and that omitted address is not requested. Supply an
   explicit alternate delivery phone and verify the order snapshot uses it.

Real messaging and paid model inference remain unvalidated here. There is no
reservation, automatic restocking/cancellation workflow, or durable outbound job
queue. General order-status policy answers remain unconfigured. No Step 8 or admin
dashboard work was performed.

## Real sales-flow follow-up

Read-only incident inspection found the reported greeting/purchase message with
`customer_intent.intent=product_search`, `purchase_intent=true` and
`speech_act=affirmative`, followed by a search response without a cart. The
semantic transition required both the purchase intent label and the boolean,
discarding the explicit purchase signal. It now normalizes an explicit purchase
signal on search/detail/availability/price into the existing purchase transition.
Affirmative speech/evidence checks, current catalog verification and subsequent
delivered-cart confirmation remain required. Availability without purchase intent
does not create a cart. No phrase-specific fix was added.

Regression tests also exposed lexical choice shortcuts interpreting unfamiliar
purchase verbs as product-name terms. When an unresolved multiword literal query
cannot identify a product, it now falls through to the existing bounded semantic
planner. Single-name misses and known products with missing variants still use
local verification. Simple resolved
choices and cart confirmations remain deterministic. Legacy Step 6 purchase
metadata without a cart becomes a fresh verified cart offer; it cannot emit the
old "choice confirmed/no order created" response or bypass cart provenance.

The semantic `website_ordering` intent is a conversational entry point only.
Without a product, it offers ordering here and reuses `Pending` target
clarification. With product/items/checkout values, it enters the same purchase
and cart functions immediately. It neither diagnoses website health nor sends
the customer back to the website. Cancelled/blocked carts are not reoffered.
Normal cart consent, stock locks, transaction, provenance and idempotency are
unchanged. Business-policy answers preserve the pending cart; `wakha confirme`
is recognized by the existing deterministic confirmation vocabulary.

### Verified delivery configuration

`DELIVERY_POLICY` is one optional JSON environment variable, also passed by
Compose. The default `{}` is disabled and asserts no ETA or contact promise.
Configure only facts verified for this deployment, for example:

```dotenv
DELIVERY_POLICY='{"enabled":true,"eta_min_days":2,"eta_max_days":4,"contact_before_arrival":false}'
```

Both ETA bounds must be supplied together, are strict integers from 0 through
365, and the minimum cannot exceed the maximum. Unknown fields and incorrectly
typed booleans are rejected. `contact_before_arrival` defaults to unknown; only
explicit `true` with the policy enabled permits a contact statement. The statement
does not claim a phone call or invent a courier process. Contact-only configuration
still says the ETA is unknown. These are general delivery estimates, not a
shipment-tracking promise or a city-specific guarantee.

`ConfiguredBusinessKnowledge` implements the existing policy interface. Delivery
questions and committed-order receipts render its verified values locally. Fees
reuse `CHECKOUT_SHIPPING_COST`; no second fee configuration was introduced.
Discounts, returns, warranty and other policies remain unknown unless supplied by
a verified business-knowledge source. Customer/model claims cannot configure a
policy, price, ETA, fee or contact promise. No schema or migration change is needed.

The sales-flow tests use the real router, conversation engine, SQL catalog,
checkpoint/order services and WhatsApp persistence with mocked semantic/sender
boundaries. They include the exact inconsistent semantic result from the incident,
not only an ideal `intent=purchase` response. PII history exclusions and literal
field extraction remain in place; current-message semantic extraction can still
process customer-provided delivery data.

Final follow-up validation (2026-09-29):

- Sales-flow module: **71 passed**. Final focused rerun including the existing
  unknown-product regression: **72 passed** in 13.39 seconds.
- Existing COD checkout/concurrency modules: **83 passed** in 20.31 seconds.
- Complete backend suite: **735 passed, 0 failed, 0 skipped** in 94.77 seconds.
- All **664 baseline test identities** remain present and passing, verified
  against the previous complete-suite XML report. No baseline expectations changed.
- Compilation, AST checks on 116 Python files, read-only tool boundaries,
  tracked/untracked whitespace, configuration validation and `pip check`: passed.
- Compose forwards default-disabled and explicitly configured delivery JSON.
- Alembic current/head remains `20260929_0005`; no schema drift. No new migration
  or migration cycle is required for these application/configuration changes.
- One existing Starlette/AnyIO deprecation warning remains.
- Validation used the isolated PostgreSQL test database on port 15432 (Windows
  had reserved the previous 55432 port). No production writes, real WhatsApp
  messages or paid OpenAI calls were performed. The isolated server was stopped
  afterward.

### Next real WhatsApp messages for this follow-up (not executed)

Use the isolated seeded catalog from the prior sequence. Keep default required
fields and unknown delivery fees. Reset test orders/stock between cases.

1. `salam bghet n commandé pantalon noir taille M` → verified 249 MAD order
   proposal, then `oui` → only missing name/city/address, then
   `Oussama, Casablanca, 12 rue Test` → one committed order. Repeat `oui`.
2. In a fresh conversation, `wach pantalon noir taille M kayn?` → availability
   only. Then try `bghit nchri pantalon noir M`, `je veux acheter le noir en M`,
   and `I want to buy the black M` as separate purchase cases.
3. `site ma khdamch, bghit n commandé` → ordering-here invitation;
   `pantalon noir M` → verified order proposal; `oui` → missing fields.
4. `site ma khdamch, bghit pantalon noir M, Oussama, Casablanca, 12 rue Test`
   → proposal without asking which product; `oui` → commit without repeating
   known fields or requesting the trusted WhatsApp phone.
5. `site ma khdamch, bghit 2 pantalon noir M et wahed bleu M` → two-item 727 MAD
   proposal. Confirm and complete fields; verify one order and both stock updates.
6. With delivery policy unset, ask `chhal katb9a livraison?` → unknown ETA.
   Configure verified 1–2 days and restart the backend; repeat → 1–2 days.
   Configure 2–4 days and repeat → 2–4 days. Enable contact-before-arrival only
   if verified; otherwise no contact promise should appear in answers/receipts.
7. During a pending proposal, ask `chhal katb9a livraison?`, then
   `ila khdit 3 tn9ess lia taman?`, then `wakha confirme`. Verify the same cart
   survives, no discount is invented, and checkout asks only missing fields.
8. Try an out-of-stock and nonexistent variant; neither should offer confirmation.
   Repeat prior price-change, last-unit race and post-commit send-failure scenarios
   in the controlled test environment; all existing safeguards remain applicable.
