from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "assistant.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "AI Telegram Chat Bot Assistant"
    app_env: str = "development"
    app_base_url: str = "http://127.0.0.1:8000"
    public_dashboard_url: str = "http://localhost:5173"
    database_url: str = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
    bot_mode: str = "polling"
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    groq_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GROQ_API_KEY", "GEMINI_API_KEY"),
    )
    groq_model: str = Field(
        default="llama-3.1-8b-instant",
        validation_alias=AliasChoices("GROQ_MODEL", "GEMINI_MODEL"),
    )
    groq_transcription_model: str = "whisper-large-v3"
    self_hosted_asr_token: str = ""
    local_asr_model: str = "Tnaot/whisper-large-v3-khmer-ct2"
    local_asr_device: str = "cpu"
    local_asr_compute_type: str = "int8"
    local_asr_beam_size: int = 8
    local_asr_best_of: int = 5
    local_asr_patience: float = 2.0
    local_asr_temperature: float = 0.0
    local_asr_initial_prompt_km: str = "សូមសរសេរជាអក្សរខ្មែរឲ្យត្រឹមត្រូវ រក្សាទុកឈ្មោះ លេខ និងពាក្យបរទេសដែលបាននិយាយតាមដើម មិនត្រូវបកប្រែ។"
    local_asr_use_ffmpeg_preprocess: bool = True
    local_asr_ffmpeg_filters: str = "highpass=f=80,lowpass=f=7600"
    max_voice_file_mb: int = 20
    admin_api_token: str = ""
    dashboard_admin_username: str = "admin"
    dashboard_admin_password: str = "change_me"
    session_secret: str = "change_me_session_secret"
    session_expiry_hours: int = 12
    free_daily_limit: int = 40
    vip_daily_limit: int = 300
    vip_plan_price_usd: float = 3.0
    vip_duration_days: int = 30
    payment_mode: str = "demo"
    aba_payway_merchant_id: str = ""
    aba_payway_api_key: str = ""
    aba_payway_merchant_auth: str = ""
    aba_payway_base_url: str = ""
    aba_payway_return_url: str = ""
    aba_payway_continue_success_url: str = ""
    aba_payway_continue_cancel_url: str = ""
    bakong_khqr_account_id: str = ""
    bakong_khqr_merchant_name: str = ""
    bakong_khqr_merchant_city: str = "Phnom Penh"
    bakong_khqr_country_code: str = "KH"
    bakong_khqr_merchant_id: str = ""
    bakong_khqr_acquiring_bank: str = ""
    bakong_khqr_store_label: str = ""
    bakong_khqr_terminal_label: str = ""
    bakong_khqr_callback_url: str = ""
    verify_abuse_threshold: int = 5
    max_input_chars: int = 4000
    active_request_timeout_seconds: int = 300
    allowed_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173"
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def webhook_path(self) -> str:
        return "/telegram/webhook"


@lru_cache
def get_settings() -> Settings:
    return Settings()
