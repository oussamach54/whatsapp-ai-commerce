# Category-agnostic Step 6

**Conversation engine reuse: YES.** The same product/variant/reference/selection
transitions now serve fashion, makeup, eyewear, electronics, perfume, furniture,
and general retail without category branches in the conversation engine.

**Current catalog capability: LIMITED.** The database still has only `size` and
`color` as structured variant attributes. Name, SKU, category, brand, active status,
price and stock remain verified catalog fields. A variant named `256GB` or `Nude`
has a verified **label**, not a verified storage or shade attribute. This refactor
does not manufacture missing schema capabilities.

No migration, new table, third catalog tool, tenancy change, checkout, order,
reservation or other Step 7 behavior was added. V1 remains one business per
database/deployment. The completed conversational commerce architecture remains
in place; this change refactors its attribute boundary.

## Audit and changes

The audit found a pants-only discovery shortcut, a local pants-to-pantalon
translation dictionary, bare numeric follow-ups automatically classified as size,
fixed size/color loops in turn transitions and recommendations, selection checks
listing individual colors and sizes, and rendering that printed missing size and
color on every product. Planner instructions also described variants primarily in
terms of those two fields.

Discovery now accepts a bounded generic product query. Budget, gift and more
complex requests retain the bounded semantic path. Product terms are not tied to
a clothing vocabulary. Scope and selection shortcuts operate on commercial
language, references and adapter-recognized attributes. The core does not branch
on product categories. Recommendations compare current DTO prices/stock and
adapter-verified constraints; the last changed dimension can vary while the other
constraints are preserved. Unknown constraints are not silently treated as met.

Rendering uses actual variant labels and only populated verified attributes. It
asks a generic variant question when the catalog has no structured dimensions.
More specific questions use attributes actually present in retrieved DTOs; existing
size/color wording remains valid for products with those fields.

## Attribute boundary

`CatalogAttributeAdapter` owns the concrete V1 field registry. Each supported key
has an application-owned DTO reader, normalizer, language aliases and extraction
boundary. Numeric ellipsis is explicitly enabled only for a suitable registered
field and requires a freshly read numeric value. The registry currently contains
only size and color; `pointure` is a supported linguistic alias for size.

The adapter provides verified attribute maps, allowlisted query filters,
normalization, exact variant matching, and preservation/change transitions. The
core consumes maps rather than accessing concrete attribute fields. Customer/model
keys never become arbitrary `getattr`, ORM column access or SQL. Unknown filter
keys are rejected. The existing strict `search_products` and `get_products`
contracts still expose only the fields the production database implements.

A separate recognition vocabulary identifies requests such as shade, frame color,
storage, volume, material, dimensions, scent, finish, compatibility, and weight.
Recognition is explicitly **not** registration as a verified catalog capability.
The planner can also propose bounded name/value requests for unfamiliar attributes.
These are checked against the same supported registry before any factual response
or selection. Provider prose is still never sent to customers.

Extending production support later requires a schema migration, validated API/DTO
fields, catalog query support, and registry entries. It does not require changing
focus, reference, pending clarification, consent, comparison or selection control
flow. Business-specific labels, units, allowed values and attribute applicability
belong in application-owned business configuration and catalog data.

## Memory and ambiguity

`commerce_state` keeps its compatible version-1 format and adds bounded maps:

| Field | Meaning |
|---|---|
| `requested_attributes` | Customer/model constraints; never evidence, including supported names |
| `changed_attributes` | Attributes currently being requested or changed; not verified facts |
| `preserved_attributes` | Unchanged attributes copied only from a fresh DTO during sibling resolution; re-read before factual use |
| `active_attribute` | The conversational dimension of an unambiguous request; may be unsupported |
| `unresolved_attribute_value` | A value whose attribute cannot safely be identified |

Maps allow at most eight keys, keys have a bounded identifier format, and values
are bounded to 64 characters. The existing `SearchProducts` constraints remain a
backward-compatible V1 query projection, not the only attribute memory. Legacy
snapshots without these new fields continue to load. Pending clarification now
supports a generic missing `attribute` and an optional attribute name; old
size/color pending labels remain accepted for metadata compatibility.

Unsupported requests preserve product focus and selection, retain the requested
attribute, and explain that the characteristic cannot be verified from the
catalog. They do not claim availability, absence, price or suitability of the
requested configuration. `shade nude` followed by `w rose?` stays a shade request;
it cannot silently become a color lookup. Explicit new product queries reset the
old attribute context. An unlabeled request remains unlabeled until evidence or
explicit clarification resolves it.

Bare `256`, `100`, `180`, or `42` does not universally mean size. A focused variant
with a fresh, uniquely eligible numeric size can support `42 -> 43`. A variant
label containing a number cannot. Units such as GB, ml, cm and g identify an
unverified requested dimension, not a size filter. Ambiguous values clarify.

## Behavior by domain

| Domain | Current behavior |
|---|---|
| Fashion | Existing size/color changes, current facts, consent and selection safety retained |
| Makeup | Shade requests retained as unverified; no shade or skin suitability inferred from names/descriptions |
| Eyewear | Actual structured color can be queried; frame color/shape are not silently equated with generic color |
| Electronics | Ordinals, labels, prices and stock work; storage requests remain unverified and never become size |
| Perfume | Ordinals, labels, prices and stock work; volume requests remain unverified |
| Shoes | Explicit pointure uses size; a bare numeric follow-up requires fresh compatible context |
| Furniture | Product/variant behavior works; dimensions/material require future schema support |
| General retail | Product/variant behavior works; weight/pack/flavor requests need verified fields before filtering |

`w l'autre?` resolves only when the presented identities and focus identify exactly
one other item. Ordinal selection and correction remain category-independent and
always refresh catalog facts. Budget recommendations retain the existing hard
budget guard and use only retrieved records. No category-specific quality or gift
suitability rules were introduced.

## Next schema evolution — recommendation only

For this project, prefer **`ProductVariant.attributes` JSONB with a typed,
application-owned attribute-definition registry**. Add product-level attributes
separately if attributes are invariant across a product's variants. The registry
should define immutable canonical keys, scope, type, canonical unit, allowed values,
aliases, localized labels, and category applicability. Models can propose requests
but cannot create definitions or decide their verification status.

| Concern | More fixed columns | JSONB plus definitions | Normalized attribute/value tables |
|---|---|---|---|
| Validation | Strong column types; many nullable columns | Typed application validation plus DB object/type checks; definitions control keys/units | Strong definition foreign keys; typed-value checks still needed |
| Indexing/search | Simple field indexes; schema changes per field | GIN for exact containment; targeted expression indexes for common numeric/range filters | Composite value indexes; multiple joins/intersections for compound filters |
| Variant uniqueness | Composite constraints grow with dimensions | Canonical normalized attribute object can form a product-scoped uniqueness key | Per-variant/key uniqueness is simple; combination uniqueness needs a canonical signature or equivalent enforcement |
| Filtering | Simple SQL but hardcoded dimensions | Validated canonical keys and bound values; never model-generated SQL | Flexible joins, with more query complexity |
| Extensibility | A migration for each new dimension | Add validated definitions without a new column per dimension | Add definitions and values; more tables and import logic |
| Size/color transition | Already represented | Backfill exact canonical values; compatibility projection while clients migrate | Backfill definition/value rows; compatibility reads require joins/projection |
| API ergonomics | Easy initially, grows indefinitely | One validated `attributes` object plus discoverable definitions | Can expose the same object, but storage translation is more involved |

JSONB fits the current small PostgreSQL/Pydantic service and its bounded catalog
queries without introducing an EAV query subsystem. It is not arbitrary schemaless
metadata: unknown keys, wrong types, units, and out-of-range values must fail admin
write validation. Enforce object structure in the DB and avoid storing speculative
model-derived facts there. Numeric values should use a definition's canonical unit
before equality, filtering and uniqueness checks.

A future migration should backfill existing size/color without changing their
meaning, audit duplicate variant combinations before adding uniqueness, retain
SKU uniqueness, and avoid imposing an empty-attributes uniqueness rule on legacy
label-only variants. Use compatibility reads/writes with a documented authoritative
source during rollout; retire old columns only after API and data checks pass.
For heavily shared facets, translations or complex analytics at a later scale,
normalized definitions/value tables may become preferable. None are created here.

## Validation and limits

The baseline entering this refactor was 455 passing backend tests. New PostgreSQL
fixtures cover fashion, makeup, eyewear, electronics, perfume, shoes, furniture
and general retail without adding attributes absent from the production schema.
Synthetic adapter-only tests demonstrate future key support; they do not enable
those keys in the production adapter.

Tests cover unsupported attribute continuity, bare numeric ambiguity, fresh numeric
context, populated-field rendering, category-independent discovery, ordinal/other
references, selection and fresh price, generic budget recommendations, semantic
requests, bounded memory and rejected arbitrary field access. Provider and
WhatsApp boundaries are mocked, and the real paid-provider transport is blocked.

Final validation: **499 backend tests passed** (all original 455 plus 44 new
generic-attribute tests) in 54.11 seconds. The focused new suite passed all 44.
The full run includes conversation, catalog/orchestration, variant, explanation,
selection, scope/router, admission/security, webhook/pipeline and PostgreSQL tests.
Compilation, `git diff --check`, explicit whitespace checks for new/untracked files,
and Alembic schema check passed; Alembic reported no new upgrade operations.
One existing Starlette/AnyIO deprecation warning remains.

`pip check` still fails on the pre-existing environment mismatch: Python 3.13 is
running with Python 3.14 wheel metadata for greenlet, httptools, MarkupSafe, PyYAML,
SQLAlchemy, watchfiles and websockets. No dependency or virtual-environment changes
were made as part of this refactor.

Remaining limits are deliberate: unsupported attributes cannot be verified or
used as filters; language recognition is bounded and unfamiliar paraphrases may
need semantic interpretation or clarification; business policy/knowledge and
attribute-definition administration are future configuration layers. The earlier
send-time race limitation remains: an older text already in flight can appear,
but cannot overwrite newer commercial state. No distributed queue was added.

Changed implementation files: `attribute_adapter.py`, `commerce_state.py`,
`conversation_engine.py`, `catalog_schemas.py`, `catalog_orchestrator.py`,
`catalog_renderer.py`, and `scope.py` under `backend/app/ai`. Tests are in
`backend/tests/test_generic_attributes.py`. Documentation changes are this report,
`docs/step-6-conversations.md`, and `README.md`. Production models, database
migrations, dependency declarations and existing tests were not modified by this
refactor.
