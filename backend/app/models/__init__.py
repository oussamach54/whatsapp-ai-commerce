"""ORM model registry imported by Alembic before reading ``Base.metadata``."""

from app.models.conversation import Conversation, Message
from app.models.ai_budget import AIBudget
from app.models.customer import Customer
from app.models.inventory import InventoryMovement
from app.models.order import Order, OrderItem
from app.models.product import Product, ProductImage, ProductVariant

__all__ = [
    "AIBudget",
    "Conversation",
    "Customer",
    "InventoryMovement",
    "Message",
    "Order",
    "OrderItem",
    "Product",
    "ProductImage",
    "ProductVariant",
]
