# Development WhatsApp test catalog

This utility is manual-only and is never invoked by startup, migrations, Docker,
or production code. Use a development database only, with `ENVIRONMENT=development`
and `CATALOG_CURRENCY=MAD`. Verify `backend/.env` points at development before running.

From the repository root in PowerShell:

```powershell
cd backend
.\.venv\Scripts\python.exe -m scripts.seed_dev_catalog --development
```

The explicit flag confirms the database is development; any configured environment
other than `development` is rejected before connecting. Do not point development
configuration at production. Credentials are loaded through existing Settings and
are never printed.

Only Pantalon Classic (`test-pantalon-classic`), Parfum Élégance
(`test-parfum-elegance`), Sac Élégant (`test-sac-elegant`) and their five specified
TEST-prefixed SKUs are created. Existing application creation services and attribute
normalization schemas are used. All products and variants are active, including the
zero-stock Noir / L variant. No descriptions or additional products are invented.

Existing matching slugs/SKUs are reused. All collisions are checked before writing;
different identity, ownership, price, stock or requested attributes cause refusal.
The command does not reset changes made during end-to-end testing. Unrelated records,
including extra variants on matching products, remain untouched. An outer database
transaction and savepoints make the CLI operation atomic despite CRUD service commits;
a transaction-scoped advisory lock serializes concurrent seed runs.

Expected first run: 3 products / 5 variants created. A second identical run creates
0 / 0. The command creates no customers, conversations, messages, orders, images,
or inventory movements and makes no OpenAI or WhatsApp calls.
