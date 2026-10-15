import uvicorn
from fastapi import FastAPI

from ugen_ai_gateway.api.routes import router
from ugen_ai_gateway.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.include_router(router)
    return app


app = create_app()


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=7632)
