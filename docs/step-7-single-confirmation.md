# Step 7 single-confirmation validation

## Incident and state transition

The prior investigation's persisted-turn findings were supplied in the resume
request. They were not independently re-read in this validation: the development
PostgreSQL service and Docker engine were stopped and were left stopped.

The cart retained an original offer time of approximately 20:33:58. The proposal
shown at approximately 20:44:55 still used that old timestamp. The first standalone
`oui`, at approximately 20:59:43, was only 14 minutes 48 seconds after the displayed
proposal, but 25 minutes 45 seconds after the timestamp used by checkout.

| Turn | State and previous behavior |
| --- | --- |
| `pantalon bleu taille M` | Availability response; the existing pending cart checkpoint remains available. Availability alone does not authorize purchase. |
| `oui bghet n commandeh` | Existing cart is re-presented as `awaiting_confirmation`; previously its original offer time was retained. |
| First `oui` | Loaded pending cart; recognized locally as a cart affirmative. The stale offer time triggered expiry, re-presenting the cart instead of collecting fields. |
| Second `oui` | Confirmed the newly refreshed version and advanced to missing checkout fields. |

The exact application branch is `app.ai.checkout.confirm`: when the cart is not
already confirmed and the answer is unrecognized **or** its offer age is outside
`0 <= age <= 900` seconds, checkout verifies and re-presents the proposal. This was
an offer-lifetime defect, not a failure to recognize the literal word `oui`.

## Preserved partial fix

The resumed tree already contained the application fix in `checkout.py`. A
re-presentation through that branch reads current products/variants, updates price
and stock, blocks unavailable or insufficient stock, increments the cart version,
sets `offered_at` to the current turn time, clears previous confirmation, calculates
totals, and synchronizes legacy purchase metadata before returning the offer.

The next timely affirmative confirms that same version. Finalization still
requires a successfully sent WhatsApp proposal for the same cart/version before
the affirmative, then locks and refreshes product/variant rows to verify activity,
price and stock. An unchanged cart advances to only missing checkout fields;
required fields must be complete before order creation. Price changes require a
new confirmation, insufficient stock blocks checkout, and genuinely expired
proposals still require renewed confirmation. No vocabulary-specific change or
checkout redesign was introduced.

During this work, a redundant second `totals()` call was removed. The regression
assertion expecting French `nom` in a Darija reply was corrected to `smiya`, and
tests were strengthened for durable checkpoint/version state, absence of repeated
proposals, exact missing-field prompts, and committed-order retry idempotency.
Those changes were subsequently included in the user's safety snapshot.

The ten single-confirmation cases cover the carried cart with a renewed window,
Darija/French/English purchase sequences, a Darija affirmative, price change,
stock change, actual expiry, availability-only affirmation, and committed-order
retry. Existing tests additionally cover provenance, post-commit recovery,
webhook idempotency, source-cart uniqueness and concurrent stock safety.

## Isolated validation environment

No running earlier dedicated test service was found. An isolated PostgreSQL 16
cluster was created under ignored `.validation/pgdata`, listening only on
`127.0.0.1:15432`, with database `confirmation_test`. No development connection
settings were reused for pytest. Before the final runs, SQL explicitly verified
the database name, listening address/port and Alembic revision `20260929_0005`.
Existing migrations were applied to this new test database only.

The development database was not reset, migrated, or modified. No orders,
messages, carts, catalog records or stock were removed from development. No real
WhatsApp sends or paid OpenAI calls occurred. No migration was added and no Step 8
work was performed.

## Validation results

- Focused single-confirmation module: **10 passed**.
- Step 7 sales flow: **71 passed**.
- COD checkout: **76 passed**.
- COD concurrency: **7 passed**.
- Checkout lifecycle: **11 passed**.
- Pending-cart transport: **1 passed**.
- Runtime readiness: **8 passed**.
- Combined focused/related run: **184 passed, 0 failed, 0 skipped**.
- Complete backend suite: **775 passed, 0 failed, 0 skipped** in 102.30 seconds.
- Identity comparison: all **765 baseline tests** collected at the start of the
  task (excluding the new single-confirmation module) remain present and passing;
  **10 additional cases**, no missing or unsuccessful baseline identities.
- Python compilation and AST parsing of 115 application/script/test files passed.
- Checkout readiness passed; Alembic reports head `20260929_0005` and no schema drift.
- `pip check` passed; no dependency changes were needed.
- `git diff --check` and explicit changed-file whitespace checks passed.

An additional in-memory mutation disabled only timestamp renewal without changing
any source file. The re-presentation regression failed at its fresh-timestamp
assertion as expected, establishing that the test detects the old lifetime defect.
This intentional mutation failure is separate from the passing final code suite.

Machine-readable test reports and the preserved baseline identity list are in
ignored `.validation/`. The sole warning in the related run is the existing
Starlette/AnyIO `BlockingPortal` deprecation warning.

The isolated test server was stopped after validation; its data and reports remain
available for reuse. No cleanup of development data is needed.

## Manual retest

Deploy/restart the backend with this code. No database cleanup or conversation
reset is required. Existing completed orders remain completed; use the current
pending purchase or explicitly request a new purchase if the current one has
already completed.

1. `pantalon bleu taille M` — expect availability information.
2. `oui bghet n commandeh` — expect the verified 229 MAD proposal (if that remains
   the current price) and `Nconfirmiw commande?`.
3. Within 15 minutes, `oui` — expect only missing name/city/address, with no repeat
   of the unchanged proposal and no order created prematurely.
4. Supply the requested missing fields — expect one order. Send `oui` again —
   expect the same order with no extra stock decrement.

The automated regression also checks confirmation approximately 14 minutes after
a re-presented proposal whose original timestamp is over 24 minutes old. A second
affirmative is unnecessary unless price changes or the current offer truly expires.
