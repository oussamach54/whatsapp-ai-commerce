# Step 6: natural intent, verified facts, safe purchase confirmation

This continues the existing structured catalog planner and `Turn` conversation
engine. It does not introduce another conversation architecture.

## Resume audit

The interrupted working tree already contained the intent contract, semantic
adapter, business-source interface, purchase state and confirmation rendering.
Integration had not been validated, and there were no semantic conversation
regressions. The interrupted targeted run had three obsolete wording assertions.
Most Step 6 files were already untracked, so Git could not provide a complete diff
against the previously validated 543-test baseline. Existing migration files and
unrelated working-tree edits were preserved.

## Architecture and behavior

`SalesRouter` retains scope, admission and history protections. Familiar safe
shortcuts and the existing bounded structured planner feed the same `Turn`.
`CustomerIntent` carries only a bounded interpretation: intent, references,
requested attributes, quantity, budget, language, speech act and policy topic.
`semantic_intent.py` translates this into existing search, detail, comparison,
clarification and confirmation operations. It owns no persistence or tool loop.
The legacy response-plan contract remains compatible with existing callers;
only one interpretation executes per turn.

Catalog services establish product identity, actual structured attributes,
existence, current MAD price and stock. Model text and prior assistant prose never
establish those facts. Database authorization remains application-controlled.
Unknown attributes clarify. Noir/L with zero stock is distinct from missing
Bleu/L; alternatives disclose changed attributes in ordinary language.

A semantic purchase request resolves a verified exact variant and asks for
confirmation. Unresolved requests use the existing pending clarification state,
including quantity. Legacy explicit selection remains a browsing preference;
only `purchase.status == confirmed` records a confirmed purchase intent.

An offer stores its quoted unit price solely as a consent snapshot. On `oui` (or
another locally allowlisted affirmative), the application checks the offer's
expiry and customer-message provenance, authorizes the read, and fetches the
product and exact variant again. A model confirmation label cannot authorize
confirmation. Offers expire after 15 minutes.

- Unchanged price and sufficient stock: persist confirmed intent metadata.
- Changed price: disclose the current price, replace the quote, and ask again.
- Zero or insufficient stock: clear the offer and explain unavailability.
- Inactive/deleted identity: invalidate the target and clear the offer.
- Decline: clear the offer. No order, payment or reservation is touched.

Recommendations and price comparisons use bounded, freshly verified catalog
candidates. Budgets and explicit supported attributes constrain candidates;
subjective quality, popularity and suitability are not asserted. Stored semantic
preferences are context, not verified product properties.

`business_knowledge.py` is retained as a small application-owned extension point:
`lookup` can eventually return a sourced, verified business answer. Its current
provider returns no answer for every topic. Discounts, delivery prices/times,
returns, warranties, payment and negotiation policies are therefore not invented.
The response states that information needs team confirmation; it does not claim
that a handoff has happened.

Removed duplicate standalone attribute/explanation implementations and the
orchestrator's redundant initial routing. Purchase clarification now also has one
owner in `Turn`, including when a semantic request continues through local turns.

## Regression fixes and validation

The resumed tests exposed a lexical route consuming `je veux le noir en M` as a
product-name search. Incompletely parsed attribute-name queries now reach the
semantic planner. Review also corrected explicit recommendation filters, exact
comparison targets, quantity persistence through clarification, final offer
budget revalidation, and language-consistent rollback after semantic failure.
Three old assertions were updated for natural alternatives and verified price
differences; identity, selection and safety assertions remain.

`test_natural_sales.py` adds 38 cases covering all requested phrases, mixed
languages, verified purchase proposals, confirmation reads, price/stock changes,
inactive products, quantity, expiry, recommendations, unknown policies and unsafe
speech acts. Tests use PostgreSQL and the real pipeline with model outputs mocked
and WhatsApp sends mocked. The global network guard blocks real SDK requests.
One new test initially reached the unmocked scope-classifier boundary; the guard
blocked it before network access, and that test now mocks the classifier too.

Previous validated baseline: **543 tests**. Final full backend suite: **581
passed**, including 38 new cases, in 63.13 seconds. The only test warning is the
existing Starlette/AnyIO deprecation. Compilation, AST parsing, normal Git
whitespace checks and explicit whitespace checks for untracked files passed.
Read-only Alembic verification reported no new upgrade operations.

`pip check` has the same pre-existing unsupported-platform metadata for greenlet
3.5.5, httptools 0.8.0, MarkupSafe 3.0.3, PyYAML 6.0.3, SQLAlchemy 2.0.52,
watchfiles 1.2.0 and websockets 17.1. Dependencies were not changed.

Files in this change: `backend/app/ai/{catalog_schemas,commerce_state,
semantic_intent,business_knowledge,catalog_orchestrator,conversation_engine,
catalog_renderer,router}.py`; `backend/tests/test_natural_sales.py`,
`test_commerce_conversations.py`, `test_conversation_recovery.py`; this document
and README.

## Real WhatsApp sequence to run next

Run against the updated development backend and a fresh conversation. First
verify the fixture through the existing catalog administration: Pantalon Classic
Noir/M 249 MAD with stock 5, Noir/L 249 MAD with stock 0, Bleu/M 229 MAD with
stock 3, and no Bleu/L. If actual data differs, expect its current facts instead.
The controlled price/stock changes below are manual development-only actions;
this implementation did not perform them on the live catalog.

1. `salam bghet n commandé pantalon noir taille M` → verified Noir/M offer.
2. `oui` → fresh read, confirmed intent, explicit absence of an order.
3. `bleu f taille M kyn ?` → Bleu/M availability and current price.
4. `bleu M kayn?` → same exact variant.
5. `vous avez le bleu en M ?` → same exact variant.
6. `is blue medium available?` → same exact variant in English.
7. `blue taille M kayn?` → same exact variant with mixed language.
8. `je veux le noir en M` → current Noir/M quote and confirmation question.
9. Before replying, manually change Noir/M price to 259 MAD. Send `oui` → show
   259 MAD and ask again; the intent must remain awaiting confirmation.
10. `oui` → fresh read and confirmed intent at 259 MAD. Restore price to 249 MAD.
11. `I want the black medium` → fresh Noir/M offer.
12. Before replying, manually set Noir/M stock to 0. Send `oui` → unavailable,
    no confirmation. Restore stock to 5.
13. `nakhod bleu M` → verified Bleu/M offer; `oui` → fresh read and confirmation.
14. `vous avez le noir en L ?` → Noir/L exists but is out of stock.
15. `vous avez le bleu en L ?` → exact combination absent; any alternatives must
    explicitly disclose changed attributes.
16. Send separately: `chno katnsa7ni?`, `3ndi 240dh chno katnsa7ni?`,
    `que me conseillez-vous ?`, `I have a 240 MAD budget`. Every listed option must
    be verified; after the budget is stated, none may cost more than 240 MAD.
17. Send separately: `ila khdit 3 tn9ess lia taman?`, `livraison l Casa ch7al?`,
    `n9der nrj3 produit?`. Expect honest lack of verified policy information.

Inspect stored metadata after each confirmation branch: target, quoted price,
status and confirmation provenance. Confirm order count is unchanged throughout.

## Limits and unchanged scope

Mocked semantic plans validate integration and deterministic safety, not the
deployed model's real-language accuracy. Real WhatsApp/model E2E remains manual.
Only currently supported size/color fields can establish structured attributes.
Confirmed intent reserves no stock; later transactional work must revalidate.
The pre-existing in-flight send race remains: an already-sending response can
arrive, but superseded replies cannot overwrite newer commercial metadata.

No migration or JSONB migration was created or applied by this continuation.
No Step 7, AI order creation, checkout, payment, reservation, real WhatsApp send
or paid model call was performed.
