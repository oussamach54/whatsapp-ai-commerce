from fastapi import APIRouter
from app.api.routes import customers, products, orders, conversations, inventory, whatsapp

api_router = APIRouter(prefix="/api")
for routes in (customers, products, orders, conversations, inventory, whatsapp):
    api_router.include_router(routes.router)
