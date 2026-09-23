# Step 3 API and service layer

All domain endpoints are mounted under `/api`. Interactive documentation is at
`/docs`; the schema is at `/openapi.json`. `/health` remains available.

## Endpoints

| Resource | Methods and paths |
| --- | --- |
| Customers | POST/GET `/api/customers`; GET/PATCH `/api/customers/{customer_id}` |
| Products | POST/GET `/api/products`; GET/PATCH `/api/products/{product_id}` |
| Variants | POST/GET `/api/products/{product_id}/variants`; GET/PATCH `/api/variants/{variant_id}` |
| Images | POST `/api/products/{product_id}/images` |
| Orders | POST/GET `/api/orders`; GET `/api/orders/{order_id}`; PATCH `/api/orders/{order_id}/status`; PATCH `/api/orders/{order_id}/payment-status` |
| Conversations | POST/GET `/api/conversations`; GET `/api/conversations/{conversation_id}` |
| Messages | POST/GET `/api/conversations/{conversation_id}/messages` |
| Inventory | POST/GET `/api/inventory/movements`; GET `/api/inventory/variants/{variant_id}/movements` |

Lists return arrays, accept `limit` (default 50, range 1-100) and `offset`
(default 0, nonnegative), and sort by creation timestamp then UUID for stable
pagination. Conversation messages are fetched separately to bound response size.
POST returns 201; GET/PATCH returns 200. Missing resources return 404,
unique conflicts return 409, and invalid input or business constraints return 422.
Service errors return `{"detail": "..."}` without exposing SQL or credentials.

## Model alignment

Customers expose `phone_number`, `first_name`, `last_name`, `email`,
`preferred_language`, `notes`, and `is_blocked`. The model has no customer
`address`, `city`, or metadata fields; shipping details belong to orders.
Variants have `name`, `sku`, Decimal prices, `stock_quantity`, and `is_active`.
Inventory has signed `quantity_change`, `reason`, `reference_type`, and
`reference_id`, with no movement-type enum. Message metadata is exposed as
`metadata` and mapped to the ORM's `metadata_` attribute.
No model or migration changes are needed.

## Business operations

Each public write service owns one commit and rolls back on any exception.
Helpers do not commit. Orders validate their customer and all variants before
persistence, read current database prices, calculate Decimal totals, and copy
product name, variant name, SKU, and unit price snapshots. PostgreSQL generates
order numbers using the existing sequence. Unknown input fields, including
client prices or computed totals, are rejected. Monetary inputs are nonnegative,
limited to two decimal places and the existing NUMERIC(12,2) range. Decimal
amounts serialize as JSON strings. PATCH omits unchanged fields, allows null for
nullable columns, and rejects null for required columns.

Inventory movements lock the variant row and atomically update the existing
`stock_quantity` alongside movement insertion. Negative stock is rejected.
Stock remains on the variant; history is an audit trail, not another balance.
Variant create/update can set the current stock directly, so movement history
need not reconstruct all stock changes. Orders do not reserve or deduct stock:
no reservation or fulfillment policy was specified. Status updates validate the
existing enums; no additional transition graph is imposed.

Nested product, order, and conversation responses eagerly load relationships.
Images may reference only variants belonging to the same product. Customer
DELETE is omitted because customers have restricted order/conversation links.

## Services

- Customer: create_customer, get_customer, get_customer_by_phone, list_customers,
  update_customer.
- Product: create_product, get_product, list_products, update_product,
  create_variant, update_variant, get_variant, list_product_variants,
  create_product_image.
- Order: create_order, get_order, list_orders, update_order_status,
  update_payment_status.
- Conversation: create_conversation, get_conversation, list_conversations,
  add_message, list_messages.
- Inventory: create_inventory_movement, list_movements,
  get_variant_movement_history.

## Verification

Use the existing Python 3.13 virtual environment from `backend`:

```powershell
.venv/Scripts/python.exe -m compileall app tests
.venv/Scripts/python.exe -m pytest
.venv/Scripts/alembic.exe current
```

From the repository root run `docker compose config --quiet`.

PostgreSQL tests require explicit TEST_DATABASE_HOST, TEST_DATABASE_NAME,
TEST_DATABASE_USER, and TEST_DATABASE_PASSWORD environment variables;
TEST_DATABASE_PORT defaults to 5432. Use a database with the existing migration
applied. Tests fail explicitly when configuration is missing; there is no
SQLite or application-database fallback. Tests run inside outer transactions
with service commits isolated by savepoints. Rows are rolled back afterward,
but PostgreSQL sequences may advance. Never target production.

Tests cover customer CRUD/conflicts, product/variant/image operations, Decimal
precision, order snapshots and server prices, invalid quantities/variants,
injected persistence failure rollback, status changes, message ordering and
metadata, stock movement atomicity, pagination, health, and OpenAPI.
