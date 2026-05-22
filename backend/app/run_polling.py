from .config import get_settings
from .crud import init_db_defaults
from .database import Base, SessionLocal, engine
from .services.telegram_bot import create_telegram_application


def main() -> None:
    settings = get_settings()
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        init_db_defaults(db)
    finally:
        db.close()

    application = create_telegram_application(settings)
    if application is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Update backend/.env first.")

    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
