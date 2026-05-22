from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from .models import BotSettings, Conversation, LogEntry, Message, PaymentTransaction, SavedExchange, User
from .schemas import OverviewStats, PaymentAdminUpdate


_UNSET = object()
_LEGACY_MODEL_MAP = {
    "gemini-2.5-flash": "llama-3.1-8b-instant",
    "gemini-2.5-pro": "llama-3.3-70b-versatile",
}


def _clean_model_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return _LEGACY_MODEL_MAP.get(cleaned, cleaned)


def _migrate_legacy_models(db: Session) -> None:
    settings = db.scalar(select(BotSettings).where(BotSettings.id == 1))
    changed = False

    if settings is not None:
        normalized_default = _clean_model_value(settings.default_model)
        if normalized_default and normalized_default != settings.default_model:
            settings.default_model = normalized_default
            db.add(settings)
            changed = True

    users = db.scalars(select(User).where(User.preferred_model.is_not(None))).all()
    for user in users:
        normalized_model = _clean_model_value(user.preferred_model)
        if normalized_model != user.preferred_model:
            user.preferred_model = normalized_model
            user.updated_at = datetime.utcnow()
            db.add(user)
            changed = True

    if changed:
        db.commit()


def init_db_defaults(db: Session) -> None:
    ensure_runtime_schema(db)
    settings = db.scalar(select(BotSettings).where(BotSettings.id == 1))
    if settings is None:
        db.add(BotSettings(id=1))
        db.commit()
    _migrate_legacy_models(db)


def ensure_runtime_schema(db: Session) -> None:
    if db.bind is None or db.bind.dialect.name != "sqlite":
        return

    columns = {
        row[1]
        for row in db.execute(text("PRAGMA table_info(users)")).fetchall()
    }
    required_columns = {
        "preferred_tone": "ALTER TABLE users ADD COLUMN preferred_tone VARCHAR(32) DEFAULT 'balanced'",
        "reply_style": "ALTER TABLE users ADD COLUMN reply_style VARCHAR(32) DEFAULT 'concise'",
        "preferred_model": "ALTER TABLE users ADD COLUMN preferred_model VARCHAR(100)",
        "payment_status": "ALTER TABLE users ADD COLUMN payment_status VARCHAR(32) DEFAULT 'None'",
        "vip_started_at": "ALTER TABLE users ADD COLUMN vip_started_at DATETIME",
        "vip_expires_at": "ALTER TABLE users ADD COLUMN vip_expires_at DATETIME",
        "daily_usage_count": "ALTER TABLE users ADD COLUMN daily_usage_count INTEGER DEFAULT 0",
        "daily_usage_date": "ALTER TABLE users ADD COLUMN daily_usage_date DATETIME",
        "active_request": "ALTER TABLE users ADD COLUMN active_request INTEGER DEFAULT 0",
        "active_request_started_at": "ALTER TABLE users ADD COLUMN active_request_started_at DATETIME",
        "abuse_score": "ALTER TABLE users ADD COLUMN abuse_score INTEGER DEFAULT 0",
        "verified_until": "ALTER TABLE users ADD COLUMN verified_until DATETIME",
        "last_request_at": "ALTER TABLE users ADD COLUMN last_request_at DATETIME",
        "last_prompt_hash": "ALTER TABLE users ADD COLUMN last_prompt_hash VARCHAR(64)",
        "duplicate_prompt_count": "ALTER TABLE users ADD COLUMN duplicate_prompt_count INTEGER DEFAULT 0",
    }

    for column_name, statement in required_columns.items():
        if column_name not in columns:
            db.execute(text(statement))

    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS payment_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                telegram_chat_id VARCHAR(64) NOT NULL,
                plan_type VARCHAR(32) DEFAULT 'VIP',
                provider VARCHAR(64) DEFAULT 'demo',
                payment_method VARCHAR(64) DEFAULT 'demo',
                amount FLOAT DEFAULT 0,
                currency VARCHAR(8) DEFAULT 'USD',
                merchant_ref VARCHAR(128) UNIQUE,
                provider_txn_id VARCHAR(128),
                checkout_url TEXT,
                qr_payload TEXT,
                qr_note TEXT,
                status VARCHAR(32) DEFAULT 'Pending',
                paid_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
    )

    db.commit()


def get_bot_settings(db: Session) -> BotSettings:
    settings = db.scalar(select(BotSettings).where(BotSettings.id == 1))
    if settings is None:
        settings = BotSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def update_bot_settings(db: Session, payload) -> BotSettings:
    settings = get_bot_settings(db)
    for key, value in payload.model_dump().items():
        if key == "default_model":
            value = _clean_model_value(value) or settings.default_model
        setattr(settings, key, value)
    settings.updated_at = datetime.utcnow()
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def upsert_telegram_user(
    db: Session,
    *,
    telegram_user_id: str,
    telegram_chat_id: str,
    username: str | None,
    full_name: str | None,
    language: str,
) -> User:
    user = db.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    if user is None:
        user = User(
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            username=username,
            full_name=full_name,
            language=language,
        )
    else:
        user.telegram_chat_id = telegram_chat_id
        user.username = username
        user.full_name = full_name
        user.language = language
        user.preferred_tone = user.preferred_tone or "balanced"
        user.reply_style = user.reply_style or "concise"
        user.updated_at = datetime.utcnow()

    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_or_create_conversation(db: Session, *, chat_id: str, user_id: int | None) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(Conversation.telegram_chat_id == chat_id)
    )
    if conversation is None:
        conversation = Conversation(telegram_chat_id=chat_id, user_id=user_id)
    else:
        conversation.user_id = user_id
        conversation.last_message_at = datetime.utcnow()

    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def add_message(
    db: Session,
    *,
    conversation_id: int,
    sender_type: str,
    content: str,
    model_name: str | None = None,
    token_usage: int = 0,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        sender_type=sender_type,
        content=content,
        model_name=model_name,
        token_usage=token_usage,
    )
    db.add(message)

    conversation = db.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.last_message_at = datetime.utcnow()
        db.add(conversation)

    db.commit()
    db.refresh(message)
    return message


def get_recent_history(db: Session, *, conversation_id: int, limit: int = 12) -> list[dict[str, str]]:
    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    ).all()

    history: list[dict[str, str]] = []
    for message in reversed(messages):
        role = "user" if message.sender_type == "user" else "assistant"
        history.append({"role": role, "text": message.content})
    return history


def get_conversation_messages(
    db: Session,
    *,
    conversation_id: int,
    limit: int | None = None,
) -> list[Message]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return db.scalars(stmt).all()


def get_latest_exchange(db: Session, *, conversation_id: int) -> tuple[Message, Message] | None:
    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(10)
    ).all()
    assistant_message: Message | None = None
    user_message: Message | None = None

    for message in messages:
        if assistant_message is None and message.sender_type == "assistant":
            assistant_message = message
            continue
        if assistant_message is not None and message.sender_type == "user":
            user_message = message
            break

    if user_message is None or assistant_message is None:
        return None
    return user_message, assistant_message


def save_exchange(
    db: Session,
    *,
    conversation_id: int,
    user_id: int | None,
    user_text: str,
    assistant_text: str,
) -> SavedExchange:
    title_seed = user_text.strip().replace("\n", " ")
    title = title_seed[:80] if title_seed else "Saved exchange"
    content = f"User: {user_text.strip()}\nAI: {assistant_text.strip()}"
    saved = SavedExchange(
        conversation_id=conversation_id,
        user_id=user_id,
        title=title,
        content=content,
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)
    return saved


def save_content(
    db: Session,
    *,
    conversation_id: int,
    user_id: int | None,
    title: str,
    content: str,
) -> SavedExchange:
    saved = SavedExchange(
        conversation_id=conversation_id,
        user_id=user_id,
        title=title[:255] or "Saved exchange",
        content=content,
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)
    return saved


def list_saved_exchanges(db: Session, *, user_id: int, limit: int = 8) -> list[SavedExchange]:
    return db.scalars(
        select(SavedExchange)
        .where(SavedExchange.user_id == user_id)
        .order_by(SavedExchange.created_at.desc())
        .limit(limit)
    ).all()


def create_payment_transaction(
    db: Session,
    *,
    user_id: int | None,
    telegram_chat_id: str,
    amount: float,
    currency: str,
    provider: str,
    payment_method: str,
    plan_type: str = "VIP",
    checkout_url: str | None = None,
    qr_payload: str | None = None,
    qr_note: str | None = None,
) -> PaymentTransaction:
    merchant_ref = f"VIP-{datetime.utcnow():%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    payment = PaymentTransaction(
        user_id=user_id,
        telegram_chat_id=telegram_chat_id,
        plan_type=plan_type,
        provider=provider,
        payment_method=payment_method,
        amount=amount,
        currency=currency,
        merchant_ref=merchant_ref,
        checkout_url=checkout_url,
        qr_payload=qr_payload,
        qr_note=qr_note,
        status="Pending",
    )
    db.add(payment)
    if user_id is not None:
        user = db.get(User, user_id)
        if user is not None:
            user.payment_status = "Pending"
            user.updated_at = datetime.utcnow()
            db.add(user)
    db.commit()
    db.refresh(payment)
    return payment


def list_payment_transactions(db: Session, *, limit: int = 100) -> list[PaymentTransaction]:
    return db.scalars(
        select(PaymentTransaction).order_by(PaymentTransaction.created_at.desc()).limit(limit)
    ).all()


def get_payment_transaction_by_ref(db: Session, *, merchant_ref: str) -> PaymentTransaction | None:
    return db.scalar(select(PaymentTransaction).where(PaymentTransaction.merchant_ref == merchant_ref))


def get_latest_pending_payment(db: Session, *, user_id: int) -> PaymentTransaction | None:
    return db.scalar(
        select(PaymentTransaction)
        .where(PaymentTransaction.user_id == user_id)
        .where(PaymentTransaction.status.in_(["Pending", "AwaitingApproval"]))
        .order_by(PaymentTransaction.created_at.desc())
    )


def update_payment_transaction(
    db: Session,
    *,
    merchant_ref: str,
    payload: PaymentAdminUpdate,
    vip_duration_days: int,
) -> PaymentTransaction:
    payment = get_payment_transaction_by_ref(db, merchant_ref=merchant_ref)
    if payment is None:
        raise ValueError("Payment transaction not found.")

    payment.status = payload.status
    payment.provider_txn_id = payload.provider_txn_id
    payment.updated_at = datetime.utcnow()

    user = db.get(User, payment.user_id) if payment.user_id is not None else None
    if user is not None:
        if payload.status == "Approved":
            now = datetime.utcnow()
            user.plan_type = "VIP"
            user.payment_status = "Approved"
            user.vip_started_at = now
            user.vip_expires_at = now + timedelta(days=vip_duration_days)
            payment.paid_at = now
        elif payload.status in {"Rejected", "Failed", "Cancelled"}:
            user.payment_status = payload.status
        elif payload.status in {"Pending", "AwaitingApproval"}:
            user.payment_status = payload.status
        user.updated_at = datetime.utcnow()
        db.add(user)

    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def reset_conversation_history(db: Session, *, conversation_id: int) -> None:
    messages = db.scalars(
        select(Message).where(Message.conversation_id == conversation_id)
    ).all()
    for message in messages:
        db.delete(message)
    conversation = db.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.summary = None
        conversation.last_message_at = datetime.utcnow()
        db.add(conversation)
    db.commit()


def create_log(
    db: Session,
    *,
    level: str,
    event_type: str,
    detail: str,
    telegram_chat_id: str | None = None,
    latency_ms: int = 0,
) -> LogEntry:
    entry = LogEntry(
        telegram_chat_id=telegram_chat_id,
        level=level,
        event_type=event_type,
        detail=detail,
        latency_ms=latency_ms,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def get_overview_stats(db: Session) -> OverviewStats:
    total_users = db.scalar(select(func.count()).select_from(User)) or 0
    active_since = datetime.utcnow() - timedelta(days=1)
    active_users = (
        db.scalar(select(func.count()).select_from(User).where(User.updated_at >= active_since))
        or 0
    )
    total_messages = db.scalar(select(func.count()).select_from(Message)) or 0
    total_conversations = db.scalar(select(func.count()).select_from(Conversation)) or 0
    error_count = (
        db.scalar(select(func.count()).select_from(LogEntry).where(LogEntry.level == "error"))
        or 0
    )

    return OverviewStats(
        total_users=total_users,
        active_users=active_users,
        total_messages=total_messages,
        total_conversations=total_conversations,
        error_count=error_count,
    )


def list_users(db: Session) -> list[User]:
    return db.scalars(select(User).order_by(User.updated_at.desc()).limit(100)).all()


def list_logs(
    db: Session,
    *,
    level: str | None = None,
    event_query: str | None = None,
    chat_query: str | None = None,
    limit: int = 200,
) -> list[LogEntry]:
    stmt = select(LogEntry)

    if level:
        stmt = stmt.where(LogEntry.level == level)
    if event_query:
        like = f"%{event_query}%"
        stmt = stmt.where(
            or_(
                LogEntry.event_type.ilike(like),
                LogEntry.detail.ilike(like),
            )
        )
    if chat_query:
        stmt = stmt.where(LogEntry.telegram_chat_id.ilike(f"%{chat_query}%"))

    return db.scalars(
        stmt.order_by(LogEntry.created_at.desc()).limit(max(20, min(limit, 500)))
    ).all()


def update_user_preferences(
    db: Session,
    *,
    user_id: int,
    preferred_tone: str | None = None,
    reply_style: str | None = None,
    preferred_model: str | None | object = _UNSET,
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("User not found.")

    if preferred_tone is not None:
        user.preferred_tone = preferred_tone
    if reply_style is not None:
        user.reply_style = reply_style
    if preferred_model is not _UNSET:
        user.preferred_model = _clean_model_value(preferred_model if isinstance(preferred_model, str) or preferred_model is None else None)

    user.updated_at = datetime.utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user_admin_settings(
    db: Session,
    *,
    user_id: int,
    plan_type: str,
    status: str,
    preferred_model: str | None,
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("User not found.")

    user.plan_type = plan_type
    user.status = status
    user.preferred_model = _clean_model_value(preferred_model)
    if plan_type == "VIP":
        now = datetime.utcnow()
        user.payment_status = "Approved"
        user.vip_started_at = now
        user.vip_expires_at = now + timedelta(days=30)
    else:
        user.payment_status = "None"
        user.vip_started_at = None
        user.vip_expires_at = None
    user.updated_at = datetime.utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_active_request(
    db: Session,
    *,
    user_id: int,
    active: bool,
    started_at: datetime | None = None,
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("User not found.")

    user.active_request = 1 if active else 0
    user.active_request_started_at = started_at if active else None
    user.updated_at = datetime.utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_usage_and_risk(
    db: Session,
    *,
    user_id: int,
    prompt_hash: str,
    now: datetime,
    increment_usage: bool,
    abuse_increment: int = 0,
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("User not found.")

    if user.daily_usage_date is None or user.daily_usage_date.date() != now.date():
        user.daily_usage_date = now
        user.daily_usage_count = 0

    if user.last_prompt_hash == prompt_hash:
        user.duplicate_prompt_count += 1
    else:
        user.last_prompt_hash = prompt_hash
        user.duplicate_prompt_count = 1

    if increment_usage:
        user.daily_usage_count += 1

    user.abuse_score = max(0, user.abuse_score + abuse_increment)
    user.last_request_at = now
    user.updated_at = now
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_verification_status(
    db: Session,
    *,
    user_id: int,
    verified_until: datetime | None,
    abuse_score: int | None = None,
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("User not found.")

    user.verified_until = verified_until
    if abuse_score is not None:
        user.abuse_score = abuse_score
    user.updated_at = datetime.utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
