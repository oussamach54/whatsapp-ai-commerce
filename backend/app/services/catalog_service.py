"""Read-only catalog queries. Every operation owns and closes a short session."""
import unicodedata
from decimal import Decimal
from time import monotonic
from sqlalchemy import select, exists, or_, and_, case, func, text
from app.models import Product, ProductVariant, Message
from app.models.enums import MessageDirection, SenderType
from app.ai.catalog_schemas import CatalogResult, ProductDTO, VariantDTO


class CatalogError(Exception):
    pass


def clean(value, limit):
    if value is None:
        return None
    return " ".join("".join(c for c in value if not unicodedata.category(c).startswith("C")).split())[:limit]


def literal(column, value):
    value = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike("%" + value + "%", escape="\\")


class CatalogService:
    def __init__(self, sessions, settings, target):
        self.sessions, self.settings, self.target = sessions, settings, target
        self.deadline = None

    def product_by_slug(self, slug):
        """Application URL resolution, not an additional model-callable tool."""
        with self.sessions() as db:
            self.authorize(db)
            return self.execute(db, select(Product.id).where(Product.slug == slug,
                Product.is_active.is_(True))).scalar_one_or_none()

    def product_links(self, refs):
        from app.services.product_links import product_url
        if not self.settings.storefront_base_url:
            return []
        ids = list(dict.fromkeys(ref.product_id for ref in refs))[:3]
        with self.sessions() as db:
            self.authorize(db)
            slugs = dict(self.execute(db, select(Product.id, Product.slug).where(
                Product.id.in_(ids), Product.is_active.is_(True))).all())
        return [url for product_id in ids if product_id in slugs
                if (url := product_url(self.settings, slugs[product_id]))]

    def execute(self, db, statement):
        if self.deadline is not None:
            remaining = int((self.deadline - monotonic()) * 1000)
            if remaining <= 0:
                raise CatalogError("deadline")
            db.execute(select(func.set_config("statement_timeout", str(min(2000, remaining)), True)))
        return db.execute(statement)

    def authorize(self, db):
        db.execute(text("SET LOCAL statement_timeout = '2000ms'"))
        db.execute(text("SET LOCAL lock_timeout = '2000ms'"))
        phone = self.settings.whatsapp_phone_number_id
        if not phone or not phone.strip():
            raise CatalogError("scope_unavailable")
        row = self.execute(db, select(Message.metadata_).where(
            Message.id == self.target.inbound_id,
            Message.conversation_id == self.target.conversation_id,
            Message.direction == MessageDirection.INBOUND,
            Message.sender_type == SenderType.CUSTOMER)).one_or_none()
        if row is None or (row[0] or {}).get("phone_number_id") != phone:
            raise CatalogError("scope_mismatch")
        # Fail closed on legacy/accidentally mixed conversations as well.
        mixed = self.execute(db, select(exists().where(
            Message.conversation_id == self.target.conversation_id,
            Message.direction == MessageDirection.INBOUND,
            func.coalesce(Message.metadata_["phone_number_id"].astext, "") != phone))).scalar()
        if mixed:
            raise CatalogError("scope_mismatch")

    @staticmethod
    def variant_filters(args):
        filters = [ProductVariant.is_active.is_(True)]
        if args.max_price is not None:
            filters.append(ProductVariant.price <= Decimal(args.max_price))
        if args.in_stock_only:
            filters.append(ProductVariant.stock_quantity > 0)
        if args.size is not None:
            filters.append(ProductVariant.size == args.size)
        if args.color is not None:
            filters.append(ProductVariant.color == args.color)
        return filters

    def _products(self, db, statement, filters, description_limit, per_product, total=15):
        rows = self.execute(db, statement).all()
        products = []
        for row in rows:
            bound = min(per_product, total)
            variants = self.execute(db, select(
                ProductVariant.id, ProductVariant.product_id, ProductVariant.name, ProductVariant.sku,
                ProductVariant.price, ProductVariant.size, ProductVariant.color, ProductVariant.stock_quantity
            ).join(Product, Product.id == ProductVariant.product_id).where(ProductVariant.product_id == row.id, *filters).order_by(
                ProductVariant.price, ProductVariant.id).limit(bound + 1)).all()
            dtos = [VariantDTO(id=v.id, product_id=v.product_id, name=clean(v.name, 255),
                sku=clean(v.sku, 100), price=format(v.price, ".2f"), size=clean(v.size, 64),
                color=clean(v.color, 64), stock_quantity=v.stock_quantity,
                availability="in_stock" if v.stock_quantity > 0 else "out_of_stock") for v in variants[:bound]]
            total -= len(dtos)
            products.append(ProductDTO(id=row.id, name=clean(row.name, 255),
                description=clean(row.description, description_limit), brand=clean(row.brand, 255),
                category=clean(row.category, 255), variants=dtos, has_more_variants=len(variants) > bound))
        return products

    @staticmethod
    def columns(description_limit):
        return select(Product.id, Product.name, func.substr(Product.description, 1, description_limit).label("description"),
                      Product.brand, Product.category)

    def search(self, args):
        with self.sessions() as db:
            self.authorize(db)
            vf = self.variant_filters(args)
            filters = [Product.is_active.is_(True)]
            if args.category:
                filters.append(literal(Product.category, args.category))
            if args.brand:
                filters.append(literal(Product.brand, args.brand))
            # Every term and all hard filters must match one candidate variant.
            for term in args.terms:
                vf.append(or_(*(literal(c, term) for c in (
                    Product.name, Product.category, Product.brand, Product.description,
                    ProductVariant.name, ProductVariant.sku))))
            matching = exists(select(ProductVariant.id).where(ProductVariant.product_id == Product.id, *vf))
            # Discovery can still identify an active product with no active variants.
            constrained = bool(args.terms or args.max_price or args.size or args.color or args.in_stock_only)
            if constrained:
                filters.append(matching)
            rank = sum((case((exists(select(ProductVariant.id).where(
                                ProductVariant.product_id == Product.id, *vf,
                                func.lower(ProductVariant.sku) == t)), 200),
                             (func.lower(Product.name) == t, 100),
                             (literal(Product.name, t), 20), (literal(Product.category, t), 10), else_=0)
                        for t in args.terms), 0)
            statement = self.columns(200).where(*filters)
            if args.terms:
                statement = statement.order_by(rank.desc())
            statement = statement.order_by(Product.created_at, Product.id).limit(args.limit + 1)
            # Only bounded parent rows and bounded variant rows cross the DB boundary.
            parents = self.execute(db, statement).all()
            ids = [p.id for p in parents[:args.limit]]
            if not ids:
                return CatalogResult(status="not_found", currency=self.settings.catalog_currency, products=[])
            ordered = self.columns(200).where(Product.id.in_(ids)).order_by(
                case({pid: i for i, pid in enumerate(ids)}, value=Product.id))
            products = self._products(db, ordered, vf, 200, 3)
            return CatalogResult(status="found", currency=self.settings.catalog_currency,
                                 products=products, has_more=len(parents) > args.limit)

    def get(self, args):
        with self.sessions() as db:
            self.authorize(db)
            variant_ids = set(args.variant_ids)
            parents = self.execute(db, select(ProductVariant.id, ProductVariant.product_id).join(Product).where(
                ProductVariant.id.in_(variant_ids), ProductVariant.is_active.is_(True),
                Product.is_active.is_(True)).limit(6)).all() if variant_ids else []
            if {v.id for v in parents} != variant_ids:
                return CatalogResult(status="not_found", currency=self.settings.catalog_currency, products=[])
            product_ids = set(args.product_ids)
            if product_ids and any(v.product_id not in product_ids for v in parents):
                raise CatalogError("invalid_parent")
            product_ids.update(v.product_id for v in parents)
            if len(product_ids) > 3:
                raise CatalogError("too_many_products")
            vf = [ProductVariant.is_active.is_(True)]
            if variant_ids:
                vf.append(ProductVariant.id.in_(variant_ids))
            # Detail/existence lookup includes zero stock. Apply attributes in SQL
            # before the six-variant bound; a truncated list cannot prove absence.
            if args.size is not None:
                vf.append(ProductVariant.size == args.size)
            if args.color is not None:
                vf.append(ProductVariant.color == args.color)
            statement = self.columns(600).where(Product.id.in_(product_ids), Product.is_active.is_(True)).order_by(Product.id).limit(3)
            products = self._products(db, statement, vf, 600, 6, total=6)
            if {p.id for p in products} != product_ids:
                return CatalogResult(status="not_found", currency=self.settings.catalog_currency, products=[])
            return CatalogResult(status="found", currency=self.settings.catalog_currency, products=products)
