# Step 7.6A: customer COD order cancellation

Cancellation uses the existing WhatsApp router, bounded semantic intent interpreter,
conversation turn, database transaction helper, order snapshots and inventory ledger.
A request presents a verified order summary. Only a later affirmative bound to that
delivered cancellation prompt can mutate an order. General order editing is outside
this change.

## Policy and ownership

| Existing status | Default customer cancellation policy |
| --- | --- |
| `pending` | Eligible |
| `confirmed` | Eligible |
| `processing` | Human handling; deployment may explicitly opt in |
| `shipped` | Human handling |
| `delivered` | Human handling |
| `cancelled` | Already cancelled; no inventory mutation |

Eligible orders must also use COD with pending payment. Paid, failed or refunded
payment requires human handling. `disposition()` centralizes this decision.

Settings accept `CUSTOMER_CANCELLATION_STATUSES` (JSON list; default
`["pending","confirmed"]`), `CUSTOMER_CANCELLATION_RECENT_DAYS` (default 30), and
`CUSTOMER_CANCELLATION_CONFIRMATION_SECONDS` (default 900, maximum 900). Shipped and
delivered cannot be enabled through the cancellation status setting.

Ownership comes from the authenticated inbound WhatsApp message and its conversation's
customer relationship. The phone-number deployment scope is authorized through the
existing catalog guard. Queries constrain orders by that trusted customer ID, including
explicit customer-facing order-number queries. A model cannot supply cancellation order
IDs. Orders from an earlier conversation belonging to the same customer remain eligible.

One eligible recent order gets a summary. Multiple eligible orders get at most three
numbered candidates and require a choice before a new confirmation prompt. No match,
already-cancelled and non-cancellable outcomes have distinct honest responses.

## Consent and state

`confirmation_context.py` implements the shared checkout/cancellation invariant:

- The latest outbound before consent must be successfully delivered, persisted,
  current and carry the exact application-owned action marker.
- No unanswered intervening inbound may exist, including a failed outbound turn.
- Consent must come from the current customer's text message within the bounded lifetime.
- Checkout markers bind cart ID/version; cancellation markers bind the action, unique
  prompt token and verified order ID. They cannot substitute for one another.

`PendingCancellation` records the action, unique token, trusted customer/conversation,
bounded order identities, request message, offered time, status and confirmation origin.
The durable inbound `cancellation_state` checkpoint is authoritative; outbound commerce
state carries the display context. Cancellation state is excluded from model prompts.
Both cancellation checkpoint and confirmation marker metadata are protected from the
public conversation-message API.

A newer unrelated response supersedes cancellation consent. Images and links cannot
authorize cancellation. A failed cancellation-prompt send or persistence cannot authorize
a later affirmative. A new checkout prompt can supersede cancellation, but the earlier
checkout prompt cannot consume cancellation consent. Catalog focus, selection, completed
checkout snapshots and unrelated pending carts remain intact.

Older persisted checkout offers without the new explicit action marker require a fresh
offer. Failed image responses preserve their carts but supersede the older checkout
consent context. Normal immediate checkout still needs only one confirmation.

## Atomic cancellation and inventory

After admission succeeds, cancellation finalization locks the conversation, checks
supersession and checkpoint identity, then rechecks consent. It locks the owned order
with ORM refresh and rechecks current ownership, status and payment policy. Variant
locks are acquired in stable UUID order. The existing transaction helper commits order
status, inventory changes, inventory audit and cancellation checkpoint together.

Restoration uses only negative `cod_order_created` movements referencing this order.
Existing `cod_order_cancelled` credits are subtracted. Reversible quantities must not
exceed order-item quantities, and stock must stay within PostgreSQL integer bounds.
No audited decrement means no stock credit. Each restored variant gets a positive
`InventoryMovement` with reason `cod_order_cancelled`, reference type `order` and the
order's internal reference ID. Customer-facing responses use the existing order number.
Historical item names, prices, quantities and monetary totals remain unchanged.

The existing inbound webhook advisory lock/deduplication remains in place. Conversation
checkpoint checks, locked terminal order status and ledger reversal protect worker retries,
repeated affirmatives and independent cancellation attempts. A committed cancellation
survives a failed success reply and can be recovered without another reversal.

Order-status updates now lock and refresh the same order row and reject leaving
`cancelled`. Thus a shipping transition that wins the race prevents cancellation; a
cancellation that wins prevents later resurrection into shipping.

No migration is needed: all statuses, order snapshots, inventory columns and message
JSON checkpoints already exist. No new cancellation diagnostics log customer data,
secrets or raw provider payloads.

## Files changed for this step

New application files:

- `backend/app/ai/cancellation_state.py`
- `backend/app/ai/order_cancellation.py`
- `backend/app/services/confirmation_context.py`
- `backend/app/services/order_cancellation_service.py`

Updated application files:

- `backend/app/ai/catalog_orchestrator.py`
- `backend/app/ai/catalog_schemas.py`
- `backend/app/ai/checkout.py`
- `backend/app/ai/commerce_state.py`
- `backend/app/ai/conversation_engine.py`
- `backend/app/ai/router.py`
- `backend/app/ai/schemas.py`
- `backend/app/ai/semantic_intent.py`
- `backend/app/core/config.py`
- `backend/app/services/checkout_service.py`
- `backend/app/services/conversation_service.py`
- `backend/app/services/order_service.py`
- `backend/app/services/whatsapp_service.py`

Tests and documentation:

- `backend/tests/test_order_cancellation.py` (new)
- `backend/tests/test_order_cancellation_concurrency.py` (new)
- `backend/tests/test_order_cancellation_multimodal.py` (new)
- `backend/tests/test_cancellation_selection.py` (selection/display follow-up)
- `backend/tests/test_cod_concurrency.py` (explicit checkout marker in fixture)
- `backend/tests/test_multimodal_commerce.py` (failed-image consent expectations)
- `docs/step-7.6a-cancellation.md` (new)

Earlier uncommitted work remains in place.

## Focused validation

287 distinct focused tests passed across the final relevant run and subsequent
cancellation additions, with zero failures/skips. No complete backend suite was run.

| Module | Passing tests |
| --- | ---: |
| New cancellation conversation tests | 58 |
| New independent cancellation concurrency tests | 4 |
| New cancellation multimodal tests | 3 |
| COD checkout | 76 |
| Checkout lifecycle | 11 |
| COD concurrency/idempotency | 7 |
| Order API lifecycle | 7 |
| Inventory API | 1 |
| Commerce conversations | 30 |
| Multimodal commerce | 72 |
| Availability follow-ups | 18 |

The new tests cover all 35 requested categories, plus rollback on audit failure,
ownership revalidation, recent-order expiry, paid-order rejection, failed success-delivery
recovery and cancellation of one order from two owned conversations. Compilation,
AST parsing and whitespace validation passed for all 22 changed Python files.
`git diff --check` passed. One existing Starlette/AnyIO deprecation warning remains.

Test artifacts are `.validation/step76a-focused.xml` and
`.validation/step76a-cancellation-final.xml`. All OpenAI and WhatsApp boundaries were
mocked; test safeguards prohibit paid-provider HTTP calls. No real provider calls,
migration, commit, push or general order editing occurred.

## Manual WhatsApp E2E sequence

Use a customer with a recent confirmed COD order whose inventory decrement is audited.
Load the changed backend first and note the order number and current variant stock.

1. Send `non annulé l commande`. Expect the verified order number, item snapshots,
   historical totals and cancellation question. Order and inventory remain unchanged.
2. If several orders appear, send the matching list number, such as `1`, and verify
   the resulting exact order summary before proceeding.
3. Send `oui` immediately. Expect successful cancellation of that order. Verify
   status `cancelled`, exact stock restoration and one positive cancellation movement
   per restored variant in the inventory/order views.
4. Send `oui` again. Expect an already-cancelled response and no further stock change.
5. Send `kayn f stock?` while the preserved verified product focus is still recent.
   Expect fresh database availability. If the focus has expired or the cancelled order
   had several items, send an explicit product/variant availability question instead,
   such as `wach pantalon noir taille M kayn?` for that actual ordered variant.

These are instructions for a manual check; no live messages were sent during implementation.

## Real selection failure follow-up

The real development database was inspected read-only. The cancellation request
persisted three ordered candidate identities in `choosing`, and the successfully
delivered list carried the same token and candidates in outbound commerce state.
The customer copied the third complete row. The old parser recognized only bare
indices/limited ordinal words or a bare order number, so it returned no cancellation
result. Generic routing then entered the semantic catalog path. The persisted
generation attempt failed with category `unavailable`; finalization superseded the
cancellation checkpoint. This was a parsing/routing failure, not lost pending state.

`ORD-2026-000002` remained `confirmed`, with identical creation/update timestamps
and only its original `cod_order_created` debit. No cancellation credit was present.
The new parser was replayed against the real stored copied text and delivered list
without executing a turn or writing orders; it resolved displayed index 3.

Selection-shaped replies now enter the cancellation path from a trusted `choosing`
checkpoint before generic catalog interpretation. Resolution requires the latest
successfully delivered list, matching token/action/customer/conversation/candidate
order, matching request origin, and the 15-minute lifetime. Expired or undelivered
contexts cannot grant selection authority. Ordinary catalog questions still use
normal routing and supersede cancellation context.

Indices and supported natural index references resolve through the persisted
ordered candidate IDs. Bare customer-facing order numbers must match a freshly
verified owned candidate. Copied options must match a complete block from the
actual delivered prompt, including the old single-row format. Altered/conflicting
copies, UUIDs, foreign/non-presented numbers and invalid indices re-present only
the remaining eligible original candidates with a fresh selection token.

Selection never cancels. It rechecks ownership and eligibility, shows order snapshots
again and issues a fresh, distinct cancellation confirmation token. The existing
locked cancellation transaction, stock audit, idempotency and fulfillment-race
protection remain in place.

The customer-facing list uses this format (French example):

```text
Quelle commande souhaitez-vous annuler ?

1. Commande ORD-2026-000004
   - 1× Pantalon Classic — Bleu / M
   Total: 229.00 MAD | 30/09/2026

2. Commande ORD-2026-000003
   - 1× Pantalon Classic — Noir / M
   Total: 249.00 MAD | 29/09/2026
```

Multi-item orders use one quantity/product/variant snapshot line per previewed item.
Lists show at most three orders with three item lines each; confirmation previews
show at most six item lines. Additional items are explicitly indicated. Product
names are capped at 90 characters and optional variant labels at 55, with visible
ellipses. Missing variant labels are omitted. Totals use stored order totals, or
clearly labelled product subtotals when the total is unknown. No current catalog
names/prices or internal UUIDs enter this display.

This follow-up changed only:

- `backend/app/ai/order_cancellation.py`
- `backend/app/services/order_cancellation_service.py`
- `backend/app/ai/router.py`
- `backend/app/ai/conversation_engine.py`
- `backend/tests/test_cancellation_selection.py` (new)
- `docs/step-7.6a-cancellation.md`

Latest focused validation: **312 passed**, including 43 new selection/display
regressions, all 65 existing cancellation tests, 94 checkout/lifecycle/concurrency
tests, and relevant conversation, order, inventory and multimodal checks. The
report is `.validation/step76a-selection-focused.xml`. AST parsing, compilation
and whitespace checks passed for all five changed Python files; `git diff --check`
passed. No migration, full backend suite, real provider calls, historical development
order writes, commit, push, Step 8 or general order editing occurred.

Next manual WhatsApp check: send `non annulé l commande` to obtain a **new** list,
copy its complete third option, verify the resulting exact summary, send `oui`,
then repeat `oui`. Only the selected eligible order should be cancelled, with one
audited stock restoration. The older failed prompt is superseded and cannot be reused.
