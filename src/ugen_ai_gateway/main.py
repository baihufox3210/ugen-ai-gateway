from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from ugen_ai_gateway.api.auth_routes import router as auth_router
from ugen_ai_gateway.api.openai_routes import router as openai_router
from ugen_ai_gateway.api.routes import router
from ugen_ai_gateway.config import get_settings

API_PREFIX = "/api"


class StripApiPrefix:
    """Serve the API under /api as well, the path the dashboard calls, so the
    same build works behind the Vite dev proxy and from this server."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            path: str = scope["path"]
            if path == API_PREFIX or path.startswith(API_PREFIX + "/"):
                scope = {**scope, "path": path[len(API_PREFIX) :] or "/"}
        await self.app(scope, receive, send)


def _static_dir() -> Path | None:
    configured = get_settings().static_dir
    path = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    )
    return path if (path / "index.html").is_file() else None


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(openai_router)
    if static := _static_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="dashboard")
    app.add_middleware(StripApiPrefix)
    return app


app = create_app()


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=7632)
