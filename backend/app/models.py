from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    telegram_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan_type: Mapped[str] = mapped_column(String(32), default="Free")
    status: Mapped[str] = mapped_column(String(32), default="Active")
    language: Mapped[str] = mapped_column(String(16), default="en")
    preferred_tone: Mapped[str] = mapped_column(String(32), default="balanced")
    reply_style: Mapped[str] = mapped_column(String(32), default="concise")
    preferred_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_status: Mapped[str] = mapped_column(String(32), default="None")
    vip_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    vip_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    daily_usage_count: Mapped[int] = mapped_column(Integer, default=0)
    daily_usage_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active_request: Mapped[int] = mapped_column(Integer, default=0)
    active_request_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    abuse_score: Mapped[int] = mapped_column(Integer, default=0)
    verified_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_request_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duplicate_prompt_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")
    payment_transactions: Mapped[list["PaymentTransaction"]] = relationship(
        back_populates="user"
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_message_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User | None] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    saved_exchanges: Mapped[list["SavedExchange"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    sender_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class SavedExchange(Base):
    __tablename__ = "saved_exchanges"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="saved_exchanges")


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(64), index=True)
    plan_type: Mapped[str] = mapped_column(String(32), default="VIP")
    provider: Mapped[str] = mapped_column(String(64), default="demo")
    payment_method: Mapped[str] = mapped_column(String(64), default="demo")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    merchant_ref: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider_txn_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    qr_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    qr_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="Pending")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped[User | None] = relationship(back_populates="payment_transactions")


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    bot_name: Mapped[str] = mapped_column(String(100), default="Nova Assistant")
    bot_bio: Mapped[str] = mapped_column(
        String(255), default="A context-aware Telegram AI assistant."
    )
    system_prompt: Mapped[str] = mapped_column(
        Text,
        default=(
            "You are a concise, helpful Telegram assistant. "
            "Reply in a natural human tone, answer directly, and keep simple requests short. "
            "Do not use headings like Code Analysis or Result unless the user explicitly asks for a structured explanation. "
            "Ask one clarifying question only when necessary."
        ),
    )
    welcome_message: Mapped[str] = mapped_column(
        Text, default="Hello. I am ready to help."
    )
    default_model: Mapped[str] = mapped_column(String(100), default="llama-3.1-8b-instant")
    temperature: Mapped[float] = mapped_column(Float, default=0.6)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=512)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
