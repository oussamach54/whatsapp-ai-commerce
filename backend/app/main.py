from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import httpx

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.services.common import ServiceError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    with httpx.Client() as client:
        app.state.whatsapp_http = client
        yield


app = FastAPI(
    lifespan=lifespan,
    title="WhatsApp AI Commerce API",
    version="0.1.0",
    description="Backend API foundation for WhatsApp AI Commerce.",
)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Return service health without accessing external dependencies."""
    return {"status": "ok"}



@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

app.include_router(api_router)
