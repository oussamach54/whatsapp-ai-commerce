"""Read-only deployment preflight. Never migrate, reset data, or call providers."""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import Numeric, String, Uuid, create_engine, inspect, text

from app.core.config import Settings


def readiness_problems(connection):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    heads = set(ScriptDirectory.from_config(config).get_heads())
    schema = inspect(connection)
    problems = []
    if not schema.has_table("alembic_version"):
        problems.append("Alembic revision is missing")
    elif set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars()) != heads:
        problems.append("Alembic revision is not the application head: " + ", ".join(sorted(heads)))
    if not schema.has_table("orders"):
        return problems + ["orders table is missing"]
    columns = {column["name"]: column for column in schema.get_columns("orders")}
    optional = ("source_cart_id", "source_conversation_id", "source_message_id", "shipping_full_name",
                "shipping_address_line", "shipping_city", "shipping_cost", "total")
    for name in optional:
        if name not in columns:
            problems.append(f"orders.{name} is missing")
        elif not columns[name]["nullable"]:
            problems.append(f"orders.{name} must allow NULL")
        if name in columns:
            kind = columns[name]["type"]
            if name.startswith("source_"):
                compatible = isinstance(kind, Uuid)
            elif name in ("shipping_cost", "total"):
                compatible = isinstance(kind, Numeric) and kind.precision == 12 and kind.scale == 2
            else:
                minimum = 500 if name == "shipping_address_line" else 255
                compatible = isinstance(kind, String) and (kind.length is None or kind.length >= minimum)
            if not compatible:
                problems.append(f"orders.{name} has an incompatible type")
    if not any(c["column_names"] == ["source_cart_id"] for c in schema.get_unique_constraints("orders")):
        problems.append("orders.source_cart_id unique constraint is missing")
    foreign_keys = schema.get_foreign_keys("orders")
    for name, table in (("source_conversation_id", "conversations"), ("source_message_id", "messages")):
        if not any(f["constrained_columns"] == [name] and f["referred_table"] == table
                   and f["referred_columns"] == ["id"] and f["options"].get("ondelete") == "RESTRICT"
                   for f in foreign_keys):
            problems.append(f"orders.{name} provenance foreign key is missing")
    return problems


def main():
    try:
        settings = Settings()  # Also validates checkout and delivery configuration.
    except ValidationError as exc:
        fields = sorted({".".join(map(str, error["loc"])) for error in exc.errors()})
        print("Checkout preflight failed: invalid configuration fields: " + ", ".join(fields))
        return 1
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            problems = readiness_problems(connection)
        if problems:
            print("Checkout preflight failed: " + "; ".join(problems))
            print("Apply python -m alembic upgrade head to the intended database before starting the backend.")
            return 1
        print("Checkout preflight passed: schema is at head; checkout/delivery configuration is valid.")
        return 0
    except Exception as exc:
        # Never print DB URLs, SQL parameters, raw exceptions, or configuration values.
        print("Checkout preflight failed: database verification unavailable (" + type(exc).__name__ + ").")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
