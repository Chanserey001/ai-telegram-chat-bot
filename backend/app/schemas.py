from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OverviewStats(BaseModel):
    total_users: int = Field(serialization_alias="totalUsers")
    active_users: int = Field(serialization_alias="activeUsers")
    total_messages: int = Field(serialization_alias="totalMessages")
    total_conversations: int = Field(serialization_alias="totalConversations")
    error_count: int = Field(serialization_alias="errorCount")

    model_config = ConfigDict(populate_by_name=True)


class UserRead(BaseModel):
    id: int
    telegram_user_id: str
    username: str | None
    full_name: str | None
    plan_type: str
    status: str
    language: str
    preferred_tone: str
    reply_style: str
    preferred_model: str | None
    payment_status: str
    vip_started_at: datetime | None
    vip_expires_at: datetime | None
    daily_usage_count: int
    abuse_score: int
    verified_until: datetime | None
    active_request: int
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserAdminUpdate(BaseModel):
    plan_type: str = Field(pattern="^(Free|VIP)$")
    status: str = Field(pattern="^(Active|Blocked)$")
    preferred_model: str | None = None


class PaymentRead(BaseModel):
    id: int
    user_id: int | None
    telegram_chat_id: str
    plan_type: str
    provider: str
    payment_method: str
    amount: float
    currency: str
    merchant_ref: str
    provider_txn_id: str | None
    checkout_url: str | None
    qr_payload: str | None
    qr_note: str | None
    status: str
    paid_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentAdminUpdate(BaseModel):
    status: str = Field(pattern="^(Pending|AwaitingApproval|Approved|Rejected|Failed|Cancelled)$")
    provider_txn_id: str | None = None


class LogRead(BaseModel):
    id: int
    telegram_chat_id: str | None
    level: str
    event_type: str
    detail: str
    latency_ms: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BotSettingsBase(BaseModel):
    bot_name: str = Field(min_length=2, max_length=100)
    bot_bio: str = Field(min_length=2, max_length=255)
    system_prompt: str = Field(min_length=10, max_length=4000)
    welcome_message: str = Field(min_length=2, max_length=1000)
    default_model: str = Field(min_length=2, max_length=100)
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(ge=64, le=4096)


class BotSettingsUpdate(BotSettingsBase):
    pass


class BotSettingsRead(BotSettingsBase):
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=3, max_length=200)


class LoginResponse(BaseModel):
    access_token: str = Field(serialization_alias="accessToken")
    token_type: str = Field(default="bearer", serialization_alias="tokenType")
    username: str
    expires_in: int = Field(serialization_alias="expiresIn")

    model_config = ConfigDict(populate_by_name=True)


class SessionRead(BaseModel):
    username: str
    authenticated: bool = True
