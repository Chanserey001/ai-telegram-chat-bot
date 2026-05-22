from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.auth import router as auth_router
from .api.admin import router as admin_router
from .api.asr import router as asr_router
from .api.export import router as export_router
from .api.payments import router as payments_router
from .api.telegram import router as telegram_router
from .config import get_settings
from .crud import init_db_defaults
from .database import Base, SessionLocal, engine
from .services.exports import ensure_khmer_font
from .services.telegram_bot import create_telegram_application


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        init_db_defaults(db)
    finally:
        db.close()

    app.state.settings = settings
    app.state.telegram_application = None

    await ensure_khmer_font()

    if settings.bot_mode == "webhook" and settings.telegram_bot_token.strip():
        telegram_application = create_telegram_application(settings)
        if telegram_application is not None:
            await telegram_application.initialize()
            await telegram_application.start()
            app.state.telegram_application = telegram_application

    try:
        yield
    finally:
        telegram_application = getattr(app.state, "telegram_application", None)
        if telegram_application is not None:
            await telegram_application.stop()
            await telegram_application.shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(asr_router)
app.include_router(export_router)
app.include_router(payments_router)
app.include_router(telegram_router)


@app.get("/")
def root():
    return {
        "name": settings.app_name,
        "status": "ok",
        "botMode": settings.bot_mode,
    }


@app.get("/health")
def health():
    return {"ok": True}
