# Step 6: trusted context, alternatives and clarification recovery

This change addresses the September 28 real WhatsApp conversation. It stays in
Step 6: no checkout, orders, reservations, catalog migration, new tables, or
additional model-accessible catalog tools. The existing `search_products` and
`get_products` tool contracts remain unchanged.

## State and verification

The turn works on a deep candidate copy of saved state and keeps a trusted
snapshot. An exact verified result commits its focus and freshly read attribute
values. A normal miss rolls back candidate attributes/constraints while retaining
explicit budget intent and a bounded `attempted` transition. That transition
stores identity, changed/preserved/hard attributes and at most three alternative
references; it never stores prices or stock.

An exact miss preserves trusted focus and selection. Verified alternatives replace
`presented`, not focus or selection. A browsing or explicit selection response can
subsequently resolve an alternative. `target_ambiguous` prevents a retained focus
from silently answering a bare price/stock question about a new alternative list.
Processing failures restore the trusted snapshot. Proven inactive/deleted
references are removed from focus, selection, presentation, discussed references,
pending candidates and attempted alternatives as applicable; deleting a parent
invalidates all references to it, while deleting one variant leaves other valid
references intact.

An existing exact variant rejected by budget/availability is a `constraint_miss`,
not evidence of nonexistence. Zero stock remains a valid exact catalog result.

## Same-product alternatives

The adapter builds exact filters from changed attributes plus freshly verified
preserved attributes. If the exact combination is absent, it generates at most
three relaxation queries in deterministic order, retaining changed attributes and
hard constraints and relaxing only inherited attributes. There are no category
or product-name branches. The current schema supports only size/color; unsupported
dimensions remain requests, never verified facts or filters.

Each `get_products` call retains the existing six-variant bound. Candidates are
deduplicated and ranked by number of differing inherited attributes, availability,
price, and variant UUID. At most three are presented. The response identifies the
missing requested combination and explicitly labels each alternative's differing
attributes. Truncated alternative results include the existing more-variants
notice; ranking is among retrieved candidates, not an exhaustive catalog promise.

Current Noir/L + requested blue therefore first queries Bleu/L, then blue variants
of the same product. Bleu/M is presented at its freshly read price/stock, with
`size: L → M`. Noir/L remains focus and any existing selection stays unchanged.

Explicit hard-constraint vocabulary is deliberately bounded (`only`, `must`,
`uniquement`, `obligatoire`, `ghir`, `darori`). Ordinary contextual attributes can
be disclosed as differences in alternatives; hard constraints and budgets cannot
be silently relaxed. Broader natural-language policy interpretation is not added.

## Explanation relevance

Generic term definitions are local and do not attach product facts. An implicit
`mafhamtch` uses product context only when its source is recorded, matches focus,
is at most 15 minutes old, has at most three intervening turns, and the recent
explanation context still refers to a product. Confusion following a generic
definition repeats the definition rather than reviving older product facts.

Social messages preserve identities and the original commercial timestamp; they
increment the intervening-turn count instead of refreshing relevance. Legacy
metadata without relevance provenance clarifies conservatively. A bounded
explicit-target phrase, such as `chno kat3ni salat quantité dial pantalon noir L?`,
resolves the named target and freshly reads its facts. Other paraphrases may need
the existing semantic path or clarification. No extra relevance model call exists.

## Recommendations and pending operations

Recommendations can expand the relevant parent product, freshly recheck the
attempted exact combination, retrieve same-product alternatives, and re-read
previously presented alternative identities. Parent scope is bounded to three;
attempted-transition queries use one exact query plus at most three relaxations,
and at most three saved alternatives are re-read. Results are deduplicated before
the three-item display bound. Changed-attribute agreement, availability, price and
UUID determine recommendation order. Every mismatch with the attempted request is
disclosed. Recommendations never select or broaden to unrelated products.

Pending state preserves the original operation, at most three candidate identities,
creation time, remaining turns and existing selection authorization. Price/stock
answers resolve ordinals or names against fresh candidate DTOs first. Only a newly
named unresolved target falls back to search, retaining the original operation and
budget without carrying unrelated old variant attributes. The final price/stock
comes from a fresh detail read. Pending memory expires after two continuation turns
or 15 minutes; the original customer-message authorization check remains mandatory
for selection. A new topic replaces inappropriate pending context.

## Regression coverage and intentional expectation changes

`test_conversation_recovery.py` covers the actual failure path without inserting a
Noir/M selection before blue, one/multiple/no alternatives, ranking and bounds,
preserved focus/selection, explicit alternative selection and browsing, current
catalog changes, original pending operations, name/ordinal recovery, new named
targets, stale/legacy/social relevance, explicit explanation targets, hard/budget
constraints, expiry, inactive/deleted references and failure rollback.

Existing tests were intentionally updated where the approved behavior changed:

- A nonexistent sibling preserves the last verified focus instead of clearing it.
- Generic definitions contain no product facts, even with fresh focus.
- Contextual-explanation fixtures supply meaningful relevance provenance.
- Missing-variant messages use generic variant wording in every language.
- An absent exact combination can be followed by a separately labelled out-of-stock
  alternative; the absence assertion applies to the exact request, not that option.

All existing selection/security assertions remain. The full suite also covers
PostgreSQL, catalog, router/scope, WhatsApp pipeline and concurrency/superseded turns.
Both external network boundaries are mocked during validation.

Validation: **543 backend tests passed**, including **44 new regressions**, with
one existing Starlette/AnyIO deprecation warning. Compilation, normal repository
`git diff --check`, explicit whitespace checks covering untracked changed files,
and the read-only Alembic schema check passed. Alembic reported no new upgrade
operations. `pip check` still reports the pre-existing unsupported-platform wheel
metadata for greenlet, httptools, MarkupSafe, PyYAML, SQLAlchemy, watchfiles and
websockets; no dependencies or virtual-environment files were changed.

Files changed in this fix: `attribute_adapter.py`, `commerce_state.py`,
`conversation_engine.py`, `catalog_orchestrator.py`, `catalog_renderer.py`, and
`scope.py` under `backend/app/ai`; the new `test_conversation_recovery.py` plus
updates to `test_catalog_explanations.py`, `test_catalog_variant_followups.py`, and
`test_commerce_conversations.py` under `backend/tests`; this document and README.

## Manual WhatsApp retest

Ensure the backend is serving this version. Keep an old Noir/L context (older than
15 minutes), then send exactly:

1. `salam`
2. `chno kat3ni salat l quantité?` — generic definition, no old product facts.
3. `wach 3ndkom pantalon noir taille M?` — Noir/M, current price/stock.
4. `w taille L?` — Noir/L, zero stock; no substitution.
5. `mafhamtch` — recent Noir/L explanation.
6. `ah okay, w bleu?` — Bleu/L missing; Bleu/M explicitly an alternative with M
   disclosed, current 229 MAD/stock 3 in the unchanged development fixture.
7. `chno katnsa7ni?` — Bleu/M considered, mismatch disclosed, no selection.
8. `ch7al taman?` — precise candidate clarification retaining `price`.
9. `Pantalon Classic — Noir / M` — current price, no generic-search operation or
   implied selection.

No real WhatsApp messages or paid OpenAI calls are sent by this implementation's
validation. The manual retest remains a user action. The pre-existing in-flight
send race remains: text already being sent may arrive, but superseded responses
cannot overwrite newer commercial metadata. No queue or Step 7 work is introduced.
