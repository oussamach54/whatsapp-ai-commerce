# Step 6 conversational commerce

The subsequent [category-agnostic attribute refactor](step-6-generic-attributes.md)
removes category assumptions from these transitions and adds generic requested,
changed and preserved attribute memory. It supersedes the size/color-only
conversation abstractions described below, while preserving their V1 behavior.

This extension uses existing message JSONB metadata and the existing two catalog
tools. It adds no migration, table, catalog tool, checkout, reservation, or order
creation. Existing administrative order APIs are unchanged.

## Failure and architecture

The reported `je veux commander…` failure had two independent causes: the local
selection recognizer excluded this construction, and the planner's select guard
required a prior focus/presentation even for a named catalog query. Clarification
did not preserve the original intent. Acknowledgements had no commercial snapshot,
so the overlap guard treated the next follow-up as an intervening unanswered turn.
Ordinary factual rendering also replaced selection with null.

`conversation_engine.py` now owns bounded turn transitions around the existing
catalog planner. Understanding, facts, and authorization remain separate:

1. Local interpretation handles clear attributes, prices, stock questions,
   ordinal choices, named choices, corrections, cancellation and bounded context.
2. Ambiguous language uses the existing strict `ResponsePlan` plus catalog tool
   arguments as a structured semantic interpretation. Its optional operation enum
   improves observability; it cannot authorize an action. There is no additional
   interpretation network round-trip and no provider prose in customer replies.
3. `search_products` identifies candidates; `get_products` refreshes identities and
   current attributes, activity, price and stock. DTOs feed deterministic rendering.
4. Application code independently requires affirmative customer language, safe
   target resolution, and current verification before creating a selection.

The operation taxonomy groups discovery/search as `search`, greetings and
acknowledgements as `social`, and after-sales facts unsupported by the catalog as
`unsupported`. Other operations are details, variant, price, stock, compare,
recommend, explain, select, change, cancel and clarify. Existing scope telemetry
continues distinguishing support, unrelated requests and security decisions.

## Structured memory

Successful eligible WhatsApp outbound messages carry two separate contracts:

| Metadata | Contents |
|---|---|
| `catalog_refs` | Ordered presented IDs, current focus, explicit selection and its provenance |
| `commerce_state` | Version, operation, previous intent, response kind, language, bounded search constraints, at most three discussed references, optional pending clarification |
| `turn_status` | Whether a sent reply was superseded before persistence |

Constraints include size, color, budget, category and brand. A requested budget is
a constraint, not a remembered product price. No product price or stock is stored
in commercial state. The bounded natural-language history remains available for
tone and semantic interpretation; historical assistant prose never establishes an
identity, stock value, price, or selection.

Pending clarification contains an operation, missing target/variant/size/color,
at most two remaining response turns, and optionally the original customer message
ID. Selection recovery rereads that actual inbound message and rechecks affirmative
language, conversation ownership and a 15-minute age bound. A pending operation by
itself is insufficient authorization. Completed answers clear pending state;
social turns consume its lifetime and unrelated operations supersede it.

Control metadata is rejected at the public message-create API. Malformed state
falls back to an empty typed state. Only delivered, eligible application snapshots
before the inbound anchor are loaded. Social memory is carried after successful
delivery, so greetings and acknowledgements require no pre-send catalog lookup or
paid call.

## Behavior and safety

Named choices are catalog queries even with no previous reference. Candidate
uniqueness and current attributes are verified; ambiguous names or variants ask
for clarification. Named choice terms must match the verified product's
name/category/brand, not merely instructions in its description. Stock-inclusive
detail reads distinguish an absent variant from a real variant with zero stock.
An out-of-stock variant does not become a new verified variant selection.

Presented order survives reference-based browsing and corrections. A factual
color/size question changes focus without changing explicit selection. New named
targets can establish a new presentation. Comparisons number their verified
candidates and persist that presentation. Product-level choices remain distinct
from choosing a variant; an ordinal cannot silently select an unspecified variant.

Corrections require an existing selection and clear choice language. Negation,
quoted choices, hypotheticals, questions and examples cannot authorize selection.
If selection and browsing focus concern different products, an attribute-only
choice asks which product instead of silently changing the selected product.
Cancellation clears selection while preserving discussion context. Unknown action
language stays conservative; a semantic operation label cannot bypass the guard.

Explanations retain the existing fresh-read architecture and never select. Simple
variant replies are one factual line; unambiguous local price replies contain just
the current amount. Missing variants clear unsafe focus while retaining selection
and bounded discussed identities. Catalog/provider failures preserve usable prior
memory and use existing safe fallback text. Failed decision telemetry prevents a
new local selection from being published.

Recommendations and comparisons use current DTO prices, stock and requested size/
budget. Available candidates are shown first, ordered by price. Replies describe
verified differences and leave subjective preference to the customer; no quality,
gift suitability, promotion or delivery promises are invented. A newly stated
budget also applies to already discussed candidates. Unsupported currencies are
not converted. Discovery stays bounded; recommendations do not claim to identify
the cheapest item in the entire catalog.

Darija Latin, Arabic-script Darija, French, English and mixed style are supported.
Current language cues override earlier style; short neutral answers can inherit
style through bounded history. Catalog names are preserved.

## Concurrency and cost

Live inbound messages receive an application-owned PostgreSQL receipt timestamp.
History/reference ordering and supersession use this timestamp, falling back to
`created_at` for legacy messages. This avoids relying on provider timestamps or
random UUID order when provider timestamps tie. Inbound and outbound persistence
briefly lock the conversation row; no DB session spans an OpenAI or WhatsApp call.

State-bearing replies check for a newer inbound before sending. If a newer inbound
arrives during the send, the already-sent older reply is persisted as superseded,
excluded from history, and carries no commercial state. Thus a late older reply
cannot overwrite newer selection memory. Truly overlapping unanswered turns
continue to resolve conservatively instead of borrowing ambiguous references.

There is still a send-time race: an older text already in flight can appear on
WhatsApp after newer input. This is state protection, not strict distributed send
ordering or a durable job queue. Legacy rows without receipt metadata retain their
older ordering behavior. No delivery retry/outbox system is added.

Existing admission and spam limits remain. Common social, explanation, attribute,
price/stock, named-choice and clear ordinal paths need no paid calls. Unknown
paraphrases use the existing classifier/planner when needed. The planner still has
at most three generation attempts and two model-requested tool executions; history,
tool result sizes and the existing deadline remain bounded.

Telemetry adds only structured operation, resolution path, reference source,
focus-resolved, selection-intent, selection-changed, clarification state,
catalog-lookup-required and outcome fields to the existing inbound `ai_guard`.
Supersession logs contain only the inbound ID. No raw prompts, provider responses,
catalog descriptions, secrets or customer phone numbers are added to logs.

## Changed files

- `backend/app/ai/conversation_engine.py`: turn transitions and local resolution.
- `backend/app/ai/commerce_state.py`: bounded state and interpretation contracts.
- `backend/app/ai/catalog_schemas.py`: semantic operation field.
- `backend/app/ai/catalog_orchestrator.py`: wrapper, structured semantic context,
  attribute normalization and concise follow-ups.
- `backend/app/ai/catalog_renderer.py`: unavailable-variant selection protection.
- `backend/app/ai/router.py`: contextual routing and deferred social memory.
- `backend/app/ai/schemas.py`: social memory carry flag.
- `backend/app/ai/scope.py`, `backend/app/ai/replies.py`: language/social continuity.
- `backend/app/services/conversation_service.py`: metadata protection and receipt ordering.
- `backend/app/services/whatsapp_service.py`: state persistence and supersession checks.
- `backend/tests/test_commerce_conversations.py`: PostgreSQL conversation/pipeline regressions.
- `README.md` and this document: behavior, validation and remaining limits.

The working tree already contained earlier Step 5/6 changes, migrations and tests
before this work. Those are not new migrations or features of this extension.

## Validation

The pre-change backend baseline was **425 passing tests** against the migrated,
dedicated PostgreSQL test database. New regression tests exercise complete turns
through routing, orchestration, references, SQL catalog reads, rendering and
WhatsApp persistence, with mocked provider and sender boundaries. Coverage includes
the incident sequence, multilingual named queries, target/size clarification,
selection correction/cancel, unsafe model choices, price/stock/activity changes,
budgets, comparisons, ordinal order, social continuity, expired consent, language
switching, injection salvage, protected metadata and overlapping reply stages.

Focused conversation/orchestration/explanation validation passed 102 tests before
the final missing-product clarification case was added. Compilation,
`git diff --check`, an explicit trailing-whitespace check for new/untracked Python
files, and `alembic check` passed. Alembic reported no new upgrade operations.
The final backend suite passes **455 tests**: all original 425 plus 30 new
conversation regressions, against PostgreSQL. One existing Starlette/AnyIO
deprecation warning remains.

`pip check` did **not** pass: the existing Python 3.13 virtual environment contains
Python 3.14 wheel metadata for greenlet, httptools, MarkupSafe, PyYAML, SQLAlchemy,
watchfiles and websockets. Dependencies and the virtual environment were not
modified by this work. This environment issue remains to be repaired separately.

The test suite blocks the real OpenAI HTTP transport. No paid API request or live
WhatsApp send is used for validation.

## Manual WhatsApp E2E sequence

Use a development catalog with Pantalon Classic: Noir/M 249 MAD with stock,
Noir/L 249 MAD with zero stock, Bleu/M 229 MAD with stock; no Bleu/L. Wait for each
reply before sending the next message. Ensure test limits allow this sequence or
pace it to remain below the configured inbound limit.

1. `salam` — short greeting.
2. `wach 3ndkom pantalon noir taille M?` — current Noir/M facts.
3. `w taille L?` — Noir/L exists, stock zero.
4. `mafhamtch` — explanation; no selection.
5. `chno kat3ni salat l quantité?` — definition and fresh state.
6. `je veux commander un pantalon noir taille M` — verified Noir/M choice, explicitly no order.
7. `ah okay, w bleu?` — Bleu/M facts; Noir/M selection stays unchanged.
8. `chno katnsa7ni?` — only verified relevant differences.
9. `safi nakhod bleu` — verified Bleu/M selection.
10. `ch7al taman?` — current 229 MAD, or the actual current DB price.
11. `merci` — brief acknowledgement; selection/focus retained.
12. `la la bghit noir` — verified Noir/M correction.
13. `how much?` — current amount, English state/style.
14. `annule choix dyali` — selection cleared, no order.

Separately test a fresh conversation with `je veux celui-là` followed by
`Pantalon Classic noir` and `M`. Test `3ndi 200dh` for honest no-match behavior.
Change the development variant's price/stock between turns and ask again to verify
freshness. Send two rapid factual/choice messages to check safe supersession; a
clarification is acceptable for ambiguous overlap. Inspect metadata to confirm
that no stale reply changes selection and verify that no order was created.
