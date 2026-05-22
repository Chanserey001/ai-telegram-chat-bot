from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timedelta
from random import randint
from time import perf_counter

from telegram import InlineKeyboardMarkup, InputFile, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..config import Settings
from ..crud import (
    add_message,
    create_log,
    get_bot_settings,
    get_conversation_messages,
    get_latest_pending_payment,
    get_or_create_conversation,
    get_payment_transaction_by_ref,
    get_recent_history,
    reset_conversation_history,
    save_content,
    set_active_request,
    set_verification_status,
    list_saved_exchanges,
    update_usage_and_risk,
    update_user_preferences,
    upsert_telegram_user,
)
from ..database import SessionLocal
from ..models import User
from .groq import GroqService
from .exports import build_content_export
from .payments import build_payment_method_label, create_vip_payment, normalize_payment_method
from .ux import (
    ACTIONS,
    ACTION_LABELS,
    CATEGORIES,
    MODEL_OPTIONS,
    STYLE_OPTIONS,
    TONE_OPTIONS,
    build_action_prompt,
    build_bot_commands,
    build_category_menu,
    build_main_menu,
    build_payment_status_menu,
    build_post_answer_menu,
    build_profile_menu,
    build_save_scope_menu,
    build_save_format_menu,
    build_settings_menu,
    build_style_menu,
    build_tone_menu,
    build_upgrade_menu,
    build_verification_prompt,
    get_daily_limit,
    history_preview,
    resolve_model_label,
)


def _build_full_name(update: Update) -> str | None:
    user = update.effective_user
    if not user:
        return None
    parts = [user.first_name, user.last_name]
    name = " ".join(part for part in parts if part).strip()
    return name or None


def _get_db_factory(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["session_factory"]


def _get_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["app_settings"]


def _get_active_jobs(context: ContextTypes.DEFAULT_TYPE) -> dict[str, asyncio.Task]:
    return context.application.bot_data.setdefault("active_jobs", {})


def _get_active_action(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("active_action", "ask")


def _get_active_category(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.get("active_category")


def _set_active_action(context: ContextTypes.DEFAULT_TYPE, action_key: str, category_key: str | None = None) -> None:
    context.user_data["active_action"] = action_key
    context.user_data["active_category"] = category_key


def _normalize_model_name(model_name: str | None) -> str | None:
    if model_name is None:
        return None
    normalized = model_name.strip()
    return normalized or None


def _resolve_reply_model(user: User, fallback_model: str, settings: Settings) -> str:
    return (
        _normalize_model_name(user.preferred_model)
        or _normalize_model_name(fallback_model)
        or settings.groq_model
    )


def _looks_like_code_request(text: str) -> bool:
    stripped = text.strip()
    lower_text = stripped.lower()
    code_markers = (
        "def ",
        "class ",
        "import ",
        "from ",
        "return ",
        "print(",
        "while ",
        "for ",
        "if ",
        "elif ",
        "else:",
        "try:",
        "except",
        "{",
        "};",
        "console.log",
        "function ",
        "public ",
        "private ",
        "# bug",
        "syntaxerror",
        "typeerror",
        "traceback",
    )
    keyword_hits = sum(1 for marker in code_markers if marker in lower_text)
    code_lines = sum(
        1
        for line in stripped.splitlines()
        if re.search(r"^\s*(def |class |if |elif |else:|for |while |try:|except|return |print\(|import |from )", line)
    )
    punctuation_hits = sum(stripped.count(token) for token in ("{", "}", "(", ")", ":", "=", "[", "]"))
    return (
        keyword_hits >= 2
        or code_lines >= 3
        or (len(stripped) > 500 and punctuation_hits >= 20)
        or "```" in stripped
    )


def _resolve_effective_action(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    requested_action: str | None,
) -> str:
    active_action = requested_action or _get_active_action(context)
    if active_action == "ask" and _looks_like_code_request(text):
        return "code"
    return active_action


def _workflow_input_mismatch(action_key: str, category_key: str | None, text: str) -> str | None:
    text_is_code = _looks_like_code_request(text)
    if category_key == "coding" and not text_is_code:
        return (
            "Coding workflow is still active.\n\n"
            "Send code, an error message, or a programming problem, or use /menu to choose another workflow."
        )
    if category_key in {"study", "work", "content", "life", "utilities"} and text_is_code:
        category_label = CATEGORIES[category_key][0]
        return (
            f"{category_label} workflow is still active, but your message looks like code.\n\n"
            "That category will not process code. Choose Coding instead, or send input that matches the selected workflow."
        )
    if action_key == "code" and not text_is_code:
        return (
            "Coding help is still active.\n\n"
            "Send a code snippet, error message, or bug description, or use /menu to choose another workflow."
        )
    if action_key in {"summarize", "rewrite", "translate", "brainstorm", "plan"} and text_is_code:
        return (
            f"{ACTION_LABELS.get(action_key, action_key.title())} workflow is still active, but your message looks like code.\n\n"
            "That workflow will not process code. Choose Coding instead, or send content that matches the selected workflow."
        )
    return None


def _get_or_init_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user:
        return None

    db_factory = _get_db_factory(context)
    db = db_factory()
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    user_row = upsert_telegram_user(
        db,
        telegram_user_id=str(user.id),
        telegram_chat_id=chat_id,
        username=user.username,
        full_name=_build_full_name(update),
        language=user.language_code or "en",
    )
    conversation = get_or_create_conversation(db, chat_id=chat_id, user_id=user_row.id)
    return db, chat_id, user_row, conversation


def _usage_count_for_today(user: User, now: datetime) -> int:
    if user.daily_usage_date is None or user.daily_usage_date.date() != now.date():
        return 0
    return user.daily_usage_count


def _is_verified(user: User, now: datetime) -> bool:
    return bool(user.verified_until and user.verified_until > now)


def _is_active_request_locked(user: User, settings: Settings, now: datetime) -> bool:
    if not user.active_request:
        return False
    if not user.active_request_started_at:
        return True
    return (now - user.active_request_started_at).total_seconds() < settings.active_request_timeout_seconds


def _estimate_abuse_increment(user: User, text: str, now: datetime) -> int:
    abuse_increment = 0
    prompt_hash = hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()

    if user.last_request_at and (now - user.last_request_at).total_seconds() < 8:
        abuse_increment += 1
    if user.last_prompt_hash == prompt_hash:
        abuse_increment += 1 if user.duplicate_prompt_count < 2 else 2
    if len(text) > 2000:
        abuse_increment += 1
    return abuse_increment


def _build_profile_text(user: User, settings: Settings, fallback_model: str) -> str:
    now = datetime.utcnow()
    daily_limit = get_daily_limit(user.plan_type, settings.free_daily_limit, settings.vip_daily_limit)
    usage = _usage_count_for_today(user, now)
    verified = "Verified" if _is_verified(user, now) else "Standard"
    return (
        f"Profile\n"
        f"Plan: {user.plan_type}\n"
        f"Status: {user.status}\n"
        f"Payment status: {user.payment_status}\n"
        f"Usage today: {usage}/{daily_limit}\n"
        f"Current workflow: {user.reply_style.title()} replies, {user.preferred_tone} tone\n"
        f"Model: {resolve_model_label(user.preferred_model, fallback_model)}\n"
        f"Trust state: {verified}\n"
        f"Abuse score: {user.abuse_score}"
    )


def _build_help_text() -> str:
    return (
        "How to use the assistant\n"
        "1. Use /menu and pick a category.\n"
        "2. Tap a workflow like Summarize, Rewrite, Plan, or Coding help.\n"
        "3. Send your text or question.\n\n"
        "Core commands\n"
        "/start /menu /profile /upgrade /settings /history /new /reset /stop\n\n"
        "AI commands\n"
        "/ask /summarize /translate /rewrite /brainstorm /plan /code\n\n"
        "Settings\n"
        "Use /settings to change tone and reply style.\n\n"
        "VIP\n"
        "Use /upgrade to start the VIP payment flow. The dashboard can also approve pending payments.\n\n"
        "Protection\n"
        "You can keep chatting normally, but only one answer is generated at a time. Use /stop if you want to cancel the current request. "
        "Suspicious behavior may trigger a quick verification check."
    )


def _build_saved_transcript_title(scope: str) -> str:
    labels = {
        "latest": "latest-exchange",
        "recent": "recent-5-exchanges",
        "chat": "current-chat",
    }
    return labels.get(scope, "saved-conversation")


def _build_saved_transcript_content(scope: str, messages: list) -> str:
    if not messages:
        return ""
    if scope == "latest":
        assistant_message = None
        user_message = None
        for message in reversed(messages):
            if assistant_message is None and message.sender_type == "assistant":
                assistant_message = message
                continue
            if assistant_message is not None and message.sender_type == "user":
                user_message = message
                break
        if user_message and assistant_message:
            return f"User: {user_message.content.strip()}\n\nAI: {assistant_message.content.strip()}"
        return ""

    lines: list[str] = []
    for message in messages:
        speaker = "User" if message.sender_type == "user" else "AI"
        lines.append(f"{speaker}: {message.content.strip()}")
    return "\n\n".join(lines)


async def _send_message(
    update: Update,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    parts = _split_telegram_message(text)
    if update.callback_query:
        for index, part in enumerate(parts):
            await update.callback_query.message.reply_text(
                part,
                reply_markup=reply_markup if index == len(parts) - 1 else None,
            )
    elif update.effective_message:
        for index, part in enumerate(parts):
            await update.effective_message.reply_text(
                part,
                reply_markup=reply_markup if index == len(parts) - 1 else None,
            )


def _split_telegram_message(text: str, limit: int = 3600) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current_lines: list[str] = []
    current_length = 0
    open_fence: str | None = None

    def flush_chunk() -> None:
        nonlocal current_lines, current_length
        if not current_lines:
            return
        chunk = "".join(current_lines).rstrip()
        if open_fence is not None:
            chunk = f"{chunk}\n```"
        chunks.append(chunk)
        current_lines = []
        current_length = 0
        if open_fence is not None:
            reopened = f"```{open_fence}\n" if open_fence else "```\n"
            current_lines.append(reopened)
            current_length = len(reopened)

    for raw_line in text.splitlines(keepends=True):
        line = raw_line
        while len(line) > limit:
            segment = line[:limit]
            line = line[limit:]
            if current_length + len(segment) > limit and current_lines:
                flush_chunk()
            current_lines.append(segment)
            current_length += len(segment)
            flush_chunk()

        if current_length + len(line) > limit and current_lines:
            flush_chunk()

        current_lines.append(line)
        current_length += len(line)

        stripped = line.strip()
        if stripped.startswith("```"):
            if open_fence is None:
                open_fence = stripped[3:].strip()
            else:
                open_fence = None

    if current_lines:
        chunk = "".join(current_lines).rstrip()
        if open_fence is not None:
            chunk = f"{chunk}\n```"
        chunks.append(chunk)

    normalized_chunks = [chunk for chunk in chunks if chunk.strip()]
    empty_code_block_pattern = re.compile(r"(?:\n|^)```[^\n]*\n```$", re.DOTALL)
    for index in range(len(normalized_chunks) - 1):
        current = normalized_chunks[index]
        next_chunk = normalized_chunks[index + 1]
        if empty_code_block_pattern.search(current) and next_chunk.lstrip().startswith("```"):
            normalized_chunks[index] = empty_code_block_pattern.sub("", current).rstrip()
    return [chunk for chunk in normalized_chunks if chunk.strip()]


def _detect_transcript_language(text: str, model_language: str | None) -> str:
    has_khmer = re.search(r"[\u1780-\u17ff]", text) is not None
    has_latin = re.search(r"[A-Za-z]", text) is not None
    if has_khmer and has_latin:
        return "Khmer + English"
    if has_khmer:
        return "Khmer"
    if has_latin:
        return "English"
    return model_language or "Unknown"


def _format_voice_detection(transcription: dict[str, float | str | None]) -> str:
    text = str(transcription.get("text") or "").strip()
    model_language = transcription.get("language")
    detected_language = _detect_transcript_language(
        text,
        model_language if isinstance(model_language, str) else None,
    )
    confidence = transcription.get("confidence")
    if isinstance(confidence, (int, float)):
        accuracy_line = f"Estimated transcription confidence: {confidence * 100:.0f}%"
    else:
        accuracy_line = "Estimated transcription confidence: not returned by the speech model"

    return (
        "Voice detected\n"
        f"Language: {detected_language}\n"
        f"{accuracy_line}\n"
        f"Transcript: {text}"
    )


def _normalize_code_summary(summary: str) -> str:
    cleaned = summary.strip()
    replacements = [
        (r"(?im)^\s*here(?:'s| is) a (?:list of the )?(?:bugs?|changes?) and (?:their )?fixes:\s*", "Main fixes\n"),
        (r"(?im)^\s*main fixes\s*:?\s*", "Main fixes\n"),
        (r"(?im)^\s*fixes\s*:?\s*", "Main fixes\n"),
        (r"(?im)^\s*here are the main fixes applied:\s*", ""),
        (r"(?im)^\s*the main fixes are:\s*", ""),
    ]
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned)
    cleaned = re.sub(r"(?m)^\s*\*\s+\*\*(.+?)\*\*\s*$", r"- \1", cleaned)
    cleaned = re.sub(r"(?m)^\s*\*\s+\*\*Fix:\*\*\s*", "- ", cleaned)
    cleaned = re.sub(r"(?m)^\s*\*\s+\*\*(.+?)\*\*\s*:\s*", r"- \1: ", cleaned)
    cleaned = re.sub(r"(?m)^\s*\*\s+", "- ", cleaned)
    cleaned = re.sub(r"(?m)^\s{2,}[-*]\s+", "- ", cleaned)
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        return ""
    if not cleaned.lower().startswith("main fixes"):
        cleaned = f"Main fixes\n{cleaned}"
    return cleaned


def _format_code_reply_for_telegram(text: str) -> list[str]:
    cleaned = text.strip()
    code_block_pattern = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
    match = code_block_pattern.search(cleaned)
    if not match:
        return [cleaned]

    code_block = match.group(0).strip()
    before = cleaned[: match.start()].strip()
    after = cleaned[match.end() :].strip()

    generic_intro_pattern = re.compile(
        r"(?is)^(here(?:'s| is)\s+the\s+(?:corrected|fixed).{0,120}?code(?::|\.)?\s*)$"
    )
    if before and not generic_intro_pattern.match(before):
        code_message = f"{before}\n\n{code_block}"
    else:
        code_message = f"Fixed code\n\n{code_block}"

    messages = [code_message.strip()]
    if after:
        summary = _normalize_code_summary(after)
        if summary:
            messages.append(summary)
    return messages


async def _show_verification_challenge(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user: User,
    chat_id: str,
) -> None:
    left = randint(2, 8)
    right = randint(1, 4)
    answer = left + right
    context.user_data["verify_answer"] = answer
    context.user_data["verify_expires"] = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
    create_log(
        db,
        level="info",
        event_type="user.verify_requested",
        detail=f"Verification challenge issued to user {user.telegram_user_id}.",
        telegram_chat_id=chat_id,
    )
    await _send_message(
        update,
        f"Quick check before we continue: what is {left} + {right}?",
        reply_markup=build_verification_prompt(answer),
    )


async def _record_system_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
    reply_text: str,
    event_type: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    state = _get_or_init_chat(update, context)
    if state is None:
        return

    db, chat_id, user, conversation = state
    try:
        settings = get_bot_settings(db)
        add_message(db, conversation_id=conversation.id, sender_type="user", content=user_text)
        add_message(
            db,
            conversation_id=conversation.id,
            sender_type="assistant",
            content=reply_text,
            model_name=settings.default_model,
            token_usage=0,
        )
        create_log(db, level="info", event_type=event_type, detail=reply_text, telegram_chat_id=chat_id)
        await _send_message(update, reply_text, reply_markup=reply_markup)
    finally:
        db.close()


async def _send_ai_reply(
    update: Update,
    text: str,
    *,
    action_key: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    messages = _format_code_reply_for_telegram(text) if action_key == "code" else [text]
    for index, message in enumerate(messages):
        await _send_message(
            update,
            message,
            reply_markup=reply_markup if index == len(messages) - 1 else None,
        )


async def _activate_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action_key: str,
    source: str,
) -> None:
    _set_active_action(context, action_key)
    action = ACTIONS[action_key]
    await _record_system_reply(
        update,
        context,
        f"{source}:{action_key}",
        f"{action.label} mode is active.\n{action.ask}",
        f"workflow.{action_key}",
        reply_markup=build_post_answer_menu(),
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = SessionLocal()
    try:
        settings = get_bot_settings(db)
        welcome = (
            f"{settings.welcome_message}\n\n"
            "Choose a category to begin. I work best when you pick a workflow first."
        )
    finally:
        db.close()

    _set_active_action(context, "ask")
    await _record_system_reply(
        update,
        context,
        "/start",
        welcome,
        "command.start",
        reply_markup=build_main_menu(),
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _record_system_reply(
        update,
        context,
        "/menu",
        "Choose the kind of help you want.",
        "command.menu",
        reply_markup=build_main_menu(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _record_system_reply(
        update,
        context,
        "/help",
        _build_help_text(),
        "command.help",
        reply_markup=build_main_menu(),
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _record_system_reply(
        update,
        context,
        "/settings",
        "Adjust your tone and reply style. Model access is controlled from the dashboard.",
        "command.settings",
        reply_markup=build_settings_menu(),
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_or_init_chat(update, context)
    if state is None:
        return

    db, chat_id, user, _ = state
    try:
        _set_active_action(context, "ask")
        active_task = _get_active_jobs(context).get(chat_id)
        if active_task and not active_task.done() and active_task is not asyncio.current_task():
            active_task.cancel()
            create_log(
                db,
                level="info",
                event_type="command.stop",
                detail=f"Cancelled active request for chat {chat_id}.",
                telegram_chat_id=chat_id,
            )
            if user.active_request:
                set_active_request(db, user_id=user.id, active=False)
            await _send_message(
                update,
                "Stopped the current AI request. You can send a new message or use /menu to start again.",
                reply_markup=build_main_menu(),
            )
            return

        create_log(
            db,
            level="info",
            event_type="command.stop",
            detail=f"Reset workflow for chat {chat_id}.",
            telegram_chat_id=chat_id,
        )
        await _send_message(
            update,
            "Stopped the current workflow. Send a new message or use /menu to choose another task.",
            reply_markup=build_main_menu(),
        )
    finally:
        db.close()


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_or_init_chat(update, context)
    if state is None:
        return

    db, _, user, _ = state
    try:
        bot_settings = get_bot_settings(db)
        profile_text = _build_profile_text(user, _get_settings(context), bot_settings.default_model)
        pending_payment = get_latest_pending_payment(db, user_id=user.id)
        await _record_system_reply(
            update,
            context,
            "/profile",
            profile_text,
            "command.profile",
            reply_markup=build_profile_menu(user.plan_type != "VIP", pending_payment is not None),
        )
    finally:
        db.close()


async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_or_init_chat(update, context)
    if state is None:
        return

    db, chat_id, user, _ = state
    try:
        settings = _get_settings(context)
        if user.plan_type == "VIP":
            await _send_message(
                update,
                "You already have VIP access. Use /profile to review your plan.",
                reply_markup=build_profile_menu(False, False),
            )
            return

        pending_payment = get_latest_pending_payment(db, user_id=user.id)
        if pending_payment is not None:
            await _send_message(
                update,
                f"You already have a pending VIP payment.\n"
                f"Reference: {pending_payment.merchant_ref}\n"
                f"Method: {build_payment_method_label(pending_payment.payment_method)}\n"
                f"Amount: {pending_payment.amount:.2f} {pending_payment.currency}",
                reply_markup=build_payment_status_menu(
                    pending_payment.checkout_url,
                    pending_payment.merchant_ref,
                ),
            )
            return

        create_log(
            db,
            level="info",
            event_type="command.upgrade",
            detail=f"Opened VIP upgrade flow for chat {chat_id}.",
            telegram_chat_id=chat_id,
        )
        await _send_message(
            update,
            f"VIP upgrade\n"
            f"- Price: {settings.vip_plan_price_usd:.2f} USD\n"
            f"- Duration: {settings.vip_duration_days} days\n"
            f"- Use this for your project demo to unlock VIP limits.\n\n"
            f"Choose a payment method.",
            reply_markup=build_upgrade_menu(),
        )
    finally:
        db.close()


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_or_init_chat(update, context)
    if state is None:
        return

    db, chat_id, _, conversation = state
    try:
        history = get_recent_history(db, conversation_id=conversation.id, limit=8)
        lines = []
        for item in history:
            speaker = "You" if item["role"] == "user" else "Bot"
            lines.append(f"{speaker}: {item['text'][:140]}")
        create_log(db, level="info", event_type="command.history", detail="Viewed history.", telegram_chat_id=chat_id)
        await _send_message(update, history_preview(lines), reply_markup=build_post_answer_menu())
    finally:
        db.close()


async def saved_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_or_init_chat(update, context)
    if state is None:
        return

    db, chat_id, user, _ = state
    try:
        saved_items = list_saved_exchanges(db, user_id=user.id, limit=6)
        if not saved_items:
            await _send_message(
                update,
                "No saved exchanges yet. Use the Save button after a reply to keep a User / AI exchange.",
                reply_markup=build_post_answer_menu(),
            )
            return

        lines = []
        for item in saved_items:
            snippet = item.content.replace("\n", " ")[:110]
            lines.append(f"- {snippet}")
        create_log(db, level="info", event_type="command.saved", detail="Viewed saved exchanges.", telegram_chat_id=chat_id)
        await _send_message(update, "Saved exchanges\n" + "\n".join(lines), reply_markup=build_post_answer_menu())
    finally:
        db.close()


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_or_init_chat(update, context)
    if state is None:
        return

    db, chat_id, _, conversation = state
    try:
        reset_conversation_history(db, conversation_id=conversation.id)
        _set_active_action(context, "ask")
        create_log(db, level="info", event_type="command.new", detail="Started a fresh flow.", telegram_chat_id=chat_id)
        await _send_message(
            update,
            "Fresh start ready. Pick a workflow or send a new question.",
            reply_markup=build_main_menu(),
        )
    finally:
        db.close()


async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_or_init_chat(update, context)
    if state is None:
        return

    db, chat_id, user, _ = state
    try:
        if _is_verified(user, datetime.utcnow()):
            await _send_message(update, "You are already verified for now.")
            return
        await _show_verification_challenge(update, context, db, user, chat_id)
    finally:
        db.close()


async def action_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    parts = update.message.text.split(maxsplit=1)
    command = parts[0].lstrip("/")
    if command not in ACTIONS:
        return

    _set_active_action(context, command)
    if len(parts) > 1 and parts[1].strip():
        await _process_user_prompt(update, context, parts[1].strip(), action_key=command)
        return

    await _activate_action(update, context, command, "command")


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return

    await query.answer()
    state = _get_or_init_chat(update, context)
    if state is None:
        return

    db, chat_id, user, conversation = state
    try:
        data = query.data
        if data.startswith("cat:"):
            category_key = data.split(":", 1)[1]
            if category_key in CATEGORIES:
                label, _ = CATEGORIES[category_key]
                await query.edit_message_text(
                    f"{label} tools\nPick one action to continue.",
                    reply_markup=build_category_menu(category_key),
                )
                create_log(db, level="info", event_type="ui.category", detail=f"Opened {category_key}.", telegram_chat_id=chat_id)
            return

        if data.startswith("act:"):
            parts = data.split(":")
            if len(parts) == 3:
                _, category_key, action_key = parts
            else:
                _, action_key = parts
                category_key = None
            if action_key in ACTIONS:
                _set_active_action(context, action_key, category_key)
                detail = f"Selected {action_key}."
                if category_key:
                    detail = f"Selected {action_key} from {category_key}."
                create_log(db, level="info", event_type="ui.action", detail=detail, telegram_chat_id=chat_id)
                
                # Use reply_text instead of edit_message_text to keep the AI response visible
                await query.message.reply_text(
                    f"{ACTIONS[action_key].label} mode is active.\n{ACTIONS[action_key].ask}",
                    reply_markup=build_post_answer_menu(),
                )
            return

        if data == "nav:menu":
            await query.edit_message_text(
                "Choose the kind of help you want.",
                reply_markup=build_main_menu(),
            )
            return

        if data == "nav:stop":
            _set_active_action(context, "ask", None)
            active_task = _get_active_jobs(context).get(chat_id)
            if active_task and not active_task.done() and active_task is not asyncio.current_task():
                active_task.cancel()
                create_log(
                    db,
                    level="info",
                    event_type="ui.stop",
                    detail=f"Cancelled active request for chat {chat_id}.",
                    telegram_chat_id=chat_id,
                )
                if user.active_request:
                    set_active_request(db, user_id=user.id, active=False)
                await query.edit_message_text(
                    "Stopped the current AI request.",
                    reply_markup=build_main_menu(),
                )
                return

            await query.edit_message_text(
                "Stopped the current workflow.",
                reply_markup=build_main_menu(),
            )
            return

        if data == "nav:history":
            history = get_recent_history(db, conversation_id=conversation.id, limit=8)
            lines = []
            for item in history:
                speaker = "You" if item["role"] == "user" else "Bot"
                lines.append(f"{speaker}: {item['text'][:140]}")
            
            # Use reply_text instead of edit_message_text
            await query.message.reply_text(
                history_preview(lines),
                reply_markup=build_post_answer_menu(),
            )
            return

        if data == "nav:upgrade":
            settings = _get_settings(context)
            pending_payment = get_latest_pending_payment(db, user_id=user.id)
            if user.plan_type == "VIP":
                await query.edit_message_text(
                    "You already have VIP access.",
                    reply_markup=build_profile_menu(False, pending_payment is not None),
                )
                return
            if pending_payment is not None:
                await query.edit_message_text(
                    f"Pending VIP payment\n"
                    f"Reference: {pending_payment.merchant_ref}\n"
                    f"Method: {build_payment_method_label(pending_payment.payment_method)}\n"
                    f"Amount: {pending_payment.amount:.2f} {pending_payment.currency}",
                    reply_markup=build_payment_status_menu(
                        pending_payment.checkout_url,
                        pending_payment.merchant_ref,
                    ),
                )
                return
            await query.edit_message_text(
                f"VIP upgrade\n"
                f"- Price: {settings.vip_plan_price_usd:.2f} USD\n"
                f"- Duration: {settings.vip_duration_days} days\n"
                f"- Choose a payment method.",
                reply_markup=build_upgrade_menu(),
            )
            return

        if data == "nav:payments":
            pending_payment = get_latest_pending_payment(db, user_id=user.id)
            if pending_payment is None:
                await query.edit_message_text(
                    "You do not have any pending VIP payment right now.",
                    reply_markup=build_profile_menu(user.plan_type != "VIP", False),
                )
                return
            await query.edit_message_text(
                f"Payment status\n"
                f"Reference: {pending_payment.merchant_ref}\n"
                f"Method: {build_payment_method_label(pending_payment.payment_method)}\n"
                f"Status: {pending_payment.status}\n"
                f"Amount: {pending_payment.amount:.2f} {pending_payment.currency}",
                reply_markup=build_payment_status_menu(
                    pending_payment.checkout_url,
                    pending_payment.merchant_ref,
                ),
            )
            return

        if data.startswith("upgrade:"):
            payment_method = data.split(":", 1)[1]
            payment_method = normalize_payment_method(payment_method)
            if payment_method not in {"aba_payway", "bakong_khqr"}:
                await query.edit_message_text(
                    "That payment method is not supported.",
                    reply_markup=build_main_menu(),
                )
                return
            payment = create_vip_payment(
                db,
                settings=_get_settings(context),
                user=user,
                payment_method=payment_method,
            )
            create_log(
                db,
                level="info",
                event_type="payment.created",
                detail=f"Created {payment_method} payment {payment.merchant_ref}.",
                telegram_chat_id=chat_id,
            )
            await query.edit_message_text(
                f"VIP payment created\n"
                f"Reference: {payment.merchant_ref}\n"
                f"Method: {build_payment_method_label(payment.payment_method)}\n"
                f"Amount: {payment.amount:.2f} {payment.currency}\n"
                f"Status: {payment.status}",
                reply_markup=build_payment_status_menu(payment.checkout_url, payment.merchant_ref),
            )
            return

        if data.startswith("payment:check:"):
            merchant_ref = data.split(":", 2)[2]
            payment = get_payment_transaction_by_ref(db, merchant_ref=merchant_ref)
            if payment is None or payment.user_id != user.id:
                await query.edit_message_text(
                    "Payment not found.",
                    reply_markup=build_main_menu(),
                )
                return
            refreshed_user = db.get(User, user.id) or user
            await query.edit_message_text(
                f"Payment status\n"
                f"Reference: {payment.merchant_ref}\n"
                f"Method: {build_payment_method_label(payment.payment_method)}\n"
                f"Status: {payment.status}\n"
                f"Current plan: {refreshed_user.plan_type}",
                reply_markup=build_payment_status_menu(payment.checkout_url, payment.merchant_ref),
            )
            return

        if data == "nav:save":
            await query.edit_message_reply_markup(
                reply_markup=build_save_scope_menu(),
            )
            return

        if data == "nav:save_back":
            await query.edit_message_reply_markup(
                reply_markup=build_post_answer_menu(),
            )
            return

        if data == "nav:save_scope_back":
            await query.edit_message_reply_markup(
                reply_markup=build_save_scope_menu(),
            )
            return

        if data.startswith("save_scope:"):
            scope = data.split(":", 1)[1]
            if scope not in {"latest", "recent", "chat"}:
                await query.answer(
                    text="That save option is not supported.",
                    show_alert=True,
                )
                return
            await query.edit_message_reply_markup(
                reply_markup=build_save_format_menu(scope),
            )
            return

        if data.startswith("savefmt:"):
            _, scope, file_format = data.split(":", 2)
            if file_format not in {"text", "pdf", "word", "excel"}:
                await query.answer(
                    text="That export format is not supported.",
                    show_alert=True,
                )
                return

            await context.bot.send_chat_action(chat_id=chat_id, action="upload_document")

            if scope == "latest":
                messages = get_conversation_messages(db, conversation_id=conversation.id, limit=12)
            elif scope == "recent":
                messages = get_conversation_messages(db, conversation_id=conversation.id, limit=10)
            else:
                messages = get_conversation_messages(db, conversation_id=conversation.id)

            content = _build_saved_transcript_content(scope, messages)
            if not content:
                await query.answer(
                    text="There is not enough conversation history to save yet.",
                    show_alert=True,
                )
                return

            title = _build_saved_transcript_title(scope)
            try:
                saved = save_content(
                    db,
                    conversation_id=conversation.id,
                    user_id=user.id,
                    title=title,
                    content=content,
                )
                filename, file_content, _ = build_content_export(
                    file_format,
                    title,
                    content,
                )
                create_log(
                    db,
                    level="info",
                    event_type="ui.save_exchange",
                    detail=f"Saved {scope} conversation {saved.id} as {file_format}.",
                    telegram_chat_id=chat_id,
                )
                await query.message.reply_document(
                    document=InputFile(file_content, filename=filename),
                    caption=f"Saved {scope} conversation as {file_format.title()}",
                )
            except RuntimeError as exc:
                create_log(
                    db,
                    level="warning",
                    event_type="ui.save_export_pdf_unavailable",
                    detail=str(exc),
                    telegram_chat_id=chat_id,
                )
                if file_format == "pdf":
                    fallback_name, fallback_content, _ = build_content_export(
                        "word",
                        title,
                        content,
                    )
                    await query.message.reply_document(
                        document=InputFile(fallback_content, filename=fallback_name),
                        caption=f"PDF renderer unavailable. Saved {scope} conversation as Word instead.",
                    )
                else:
                    await query.message.reply_text(
                        "Export failed. Please try again later."
                    )
            except (UnicodeEncodeError, UnicodeDecodeError, ValueError) as exc:
                create_log(
                    db,
                    level="warning",
                    event_type="ui.save_export_encoding",
                    detail=str(exc),
                    telegram_chat_id=chat_id,
                )
                await query.message.reply_text(
                    "That export could not be generated in the selected format. "
                    "Please try Word or Text instead."
                )
            except Exception as exc:
                create_log(
                    db,
                    level="error",
                    event_type="ui.save_export_failed",
                    detail=str(exc),
                    telegram_chat_id=chat_id,
                )
                await query.message.reply_text(
                    "Export failed. Please try again or choose a different format."
                )
            finally:
                try:
                    await query.edit_message_reply_markup(
                        reply_markup=build_post_answer_menu(),
                    )
                except Exception:
                    pass
            return

        if data == "nav:help":
            await query.edit_message_text(
                _build_help_text(),
                reply_markup=build_main_menu(),
            )
            return

        if data == "nav:settings":
            await query.edit_message_text(
                "Adjust your tone and reply style. Model access is controlled from the dashboard.",
                reply_markup=build_settings_menu(),
            )
            return

        if data == "nav:profile":
            bot_settings = get_bot_settings(db)
            pending_payment = get_latest_pending_payment(db, user_id=user.id)
            await query.edit_message_text(
                _build_profile_text(user, _get_settings(context), bot_settings.default_model),
                reply_markup=build_profile_menu(user.plan_type != "VIP", pending_payment is not None),
            )
            return

        if data == "settings:tone":
            await query.edit_message_text("Choose your preferred tone.", reply_markup=build_tone_menu())
            return

        if data == "settings:style":
            await query.edit_message_text("Choose your preferred reply style.", reply_markup=build_style_menu())
            return

        if data == "settings:model":
            bot_settings = get_bot_settings(db)
            await query.edit_message_text(
                f"Model access is locked in Telegram.\n"
                f"Current model: {resolve_model_label(user.preferred_model, bot_settings.default_model)}\n"
                f"Use the dashboard to assign a Groq model or keep the dashboard default for this user.",
                reply_markup=build_settings_menu(),
            )
            return

        if data.startswith("tone:"):
            tone_key = data.split(":", 1)[1]
            if tone_key in TONE_OPTIONS:
                user = update_user_preferences(db, user_id=user.id, preferred_tone=tone_key)
                await query.edit_message_text(
                    f"Tone updated to {TONE_OPTIONS[tone_key]}.",
                    reply_markup=build_settings_menu(),
                )
            return

        if data.startswith("style:"):
            style_key = data.split(":", 1)[1]
            if style_key in STYLE_OPTIONS:
                user = update_user_preferences(db, user_id=user.id, reply_style=style_key)
                await query.edit_message_text(
                    f"Reply style updated to {STYLE_OPTIONS[style_key]}.",
                    reply_markup=build_settings_menu(),
                )
            return

        if data.startswith("model:"):
            bot_settings = get_bot_settings(db)
            await query.edit_message_text(
                f"Model changes are disabled in Telegram.\n"
                f"Current model: {resolve_model_label(user.preferred_model, bot_settings.default_model)}\n"
                f"Ask the admin to change it in the dashboard if needed.",
                reply_markup=build_settings_menu(),
            )
            return

        if data.startswith("verify:"):
            selected = int(data.split(":", 1)[1])
            answer = context.user_data.get("verify_answer")
            expires = context.user_data.get("verify_expires")
            if answer is None or expires is None or datetime.fromisoformat(expires) < datetime.utcnow():
                await query.edit_message_text("Verification expired. Use /verify to request a new one.")
                return

            if selected == answer:
                verified_until = datetime.utcnow() + timedelta(hours=24)
                set_verification_status(
                    db,
                    user_id=user.id,
                    verified_until=verified_until,
                    abuse_score=max(0, user.abuse_score - 3),
                )
                context.user_data.pop("verify_answer", None)
                context.user_data.pop("verify_expires", None)
                await query.edit_message_text(
                    "Verification complete. You can continue using the assistant.",
                    reply_markup=build_post_answer_menu(),
                )
            else:
                updated_user = set_verification_status(
                    db,
                    user_id=user.id,
                    verified_until=None,
                    abuse_score=user.abuse_score + 1,
                )
                await query.edit_message_text("That answer was not correct. Use /verify to try again.")
                create_log(
                    db,
                    level="info",
                    event_type="user.verify_failed",
                    detail="Verification answer was incorrect.",
                    telegram_chat_id=chat_id,
                )
                if updated_user.abuse_score > _get_settings(context).verify_abuse_threshold + 2:
                    updated_user.status = "Blocked"
                    db.add(updated_user)
                    db.commit()
            return
    finally:
        db.close()


async def _process_user_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    action_key: str | None = None,
) -> None:
    started_at = perf_counter()
    state = _get_or_init_chat(update, context)
    if state is None:
        return

    db, chat_id, user, conversation = state
    settings = _get_settings(context)
    active_jobs = _get_active_jobs(context)
    now = datetime.utcnow()
    prompt_hash = hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()
    lock_acquired = False
    current_task = asyncio.current_task()

    try:
        if user.status.lower() == "blocked":
            await update.effective_message.reply_text(
                "Your access is temporarily blocked. Contact support if you believe this is a mistake."
            )
            return

        if len(text) > settings.max_input_chars:
            create_log(
                db,
                level="info",
                event_type="telegram.input_rejected",
                detail=f"Input exceeded {settings.max_input_chars} characters.",
                telegram_chat_id=chat_id,
            )
            await update.effective_message.reply_text(
                f"That message is too long. Please keep it under {settings.max_input_chars} characters."
            )
            return

        if _is_active_request_locked(user, settings, now):
            await update.effective_message.reply_text(
                "I am still working on your previous request. Send the next one after this answer arrives."
            )
            return

        selected_action = action_key or _get_active_action(context)
        selected_category = _get_active_category(context)
        mismatch_message = _workflow_input_mismatch(selected_action, selected_category, text)
        if mismatch_message is not None:
            create_log(
                db,
                level="info",
                event_type=f"telegram.workflow_mismatch.{selected_action}",
                detail=f"Rejected mismatched input while {selected_action} workflow was active.",
                telegram_chat_id=chat_id,
            )
            await update.effective_message.reply_text(
                mismatch_message,
                reply_markup=build_post_answer_menu(),
            )
            return

        projected_abuse_score = user.abuse_score + _estimate_abuse_increment(user, text, now)
        if not _is_verified(user, now) and projected_abuse_score >= settings.verify_abuse_threshold:
            update_usage_and_risk(
                db,
                user_id=user.id,
                prompt_hash=prompt_hash,
                now=now,
                increment_usage=False,
                abuse_increment=max(1, projected_abuse_score - user.abuse_score),
            )
            refreshed_user = db.get(User, user.id)
            if refreshed_user is not None:
                user = refreshed_user
            await _show_verification_challenge(update, context, db, user, chat_id)
            return

        daily_limit = get_daily_limit(user.plan_type, settings.free_daily_limit, settings.vip_daily_limit)
        if _usage_count_for_today(user, now) >= daily_limit:
            await update.effective_message.reply_text(
                f"You reached today's limit for the {user.plan_type} plan. Use /profile to check usage or upgrade your plan."
            )
            create_log(
                db,
                level="info",
                event_type="telegram.limit_reached",
                detail=f"Daily limit reached for user {user.telegram_user_id}.",
                telegram_chat_id=chat_id,
            )
            return

        user = update_usage_and_risk(
            db,
            user_id=user.id,
            prompt_hash=prompt_hash,
            now=now,
            increment_usage=True,
            abuse_increment=max(0, projected_abuse_score - user.abuse_score),
        )
        set_active_request(db, user_id=user.id, active=True, started_at=now)
        lock_acquired = True
        if current_task is not None:
            active_jobs[chat_id] = current_task

        active_action = _resolve_effective_action(context, text, action_key)
        _set_active_action(context, active_action)
        bot_settings = get_bot_settings(db)
        effective_reply_style = user.reply_style
        if active_action == "code" and len(text) > 700 and effective_reply_style == "concise":
            effective_reply_style = "detailed"
        prompt_text = build_action_prompt(active_action, user.preferred_tone, effective_reply_style, text)
        add_message(db, conversation_id=conversation.id, sender_type="user", content=prompt_text)
        history = get_recent_history(db, conversation_id=conversation.id)
        preferred_model = _resolve_reply_model(user, bot_settings.default_model, settings)
        reply_result = await GroqService(settings).generate_reply(
            bot_settings,
            history,
            preferred_model=preferred_model,
            action_key=active_action,
            user_input=text,
        )

        reply_text = str(reply_result["text"])
        add_message(
            db,
            conversation_id=conversation.id,
            sender_type="assistant",
            content=reply_text,
            model_name=str(reply_result["model"]),
            token_usage=int(reply_result["token_usage"]),
        )
        create_log(
            db,
            level="info",
            event_type=f"telegram.reply.{active_action}",
            detail=f"Responded to chat {chat_id} using model {reply_result['model']}.",
            telegram_chat_id=chat_id,
            latency_ms=int((perf_counter() - started_at) * 1000),
        )
        await _send_ai_reply(
            update,
            reply_text,
            action_key=active_action,
            reply_markup=build_post_answer_menu(),
        )
    except asyncio.CancelledError:
        create_log(
            db,
            level="info",
            event_type="telegram.reply_cancelled",
            detail=f"Cancelled active request for chat {chat_id}.",
            telegram_chat_id=chat_id,
            latency_ms=int((perf_counter() - started_at) * 1000),
        )
    except Exception as exc:
        rate_limited = getattr(getattr(exc, "response", None), "status_code", None) == 429
        create_log(
            db,
            level="error",
            event_type="telegram.reply_failed",
            detail=str(exc),
            telegram_chat_id=chat_id,
            latency_ms=int((perf_counter() - started_at) * 1000),
        )
        if rate_limited:
            await _send_message(
                update,
                "The AI service is temporarily rate-limited. Please wait a moment and send your message again.",
                reply_markup=build_post_answer_menu(),
            )
        else:
            await _send_message(update, "I ran into a temporary problem. Please try again in a moment.")
    finally:
        registered_task = active_jobs.get(chat_id)
        if registered_task is current_task:
            active_jobs.pop(chat_id, None)
        refreshed_user = db.get(User, user.id)
        if lock_acquired and refreshed_user is not None and refreshed_user.active_request:
            set_active_request(db, user_id=user.id, active=False)
        db.close()


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_chat or not update.effective_user:
        return

    text = (update.effective_message.text or "").strip()
    if not text:
        return

    await _process_user_prompt(update, context, text)


async def voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_chat or not update.effective_user:
        return

    voice = update.effective_message.voice
    if voice is None:
        return

    settings = _get_settings(context)
    max_bytes = settings.max_voice_file_mb * 1024 * 1024
    if voice.file_size and voice.file_size > max_bytes:
        await _send_message(
            update,
            f"That voice message is too large. Please keep voice notes under {settings.max_voice_file_mb} MB.",
        )
        return

    started_at = perf_counter()
    state = _get_or_init_chat(update, context)
    db = None
    chat_id = str(update.effective_chat.id)
    try:
        if state is not None:
            db, chat_id, _, _ = state

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        telegram_file = await context.bot.get_file(voice.file_id)
        audio_bytes = bytes(await telegram_file.download_as_bytearray())
        if len(audio_bytes) > max_bytes:
            await _send_message(
                update,
                f"That voice message is too large. Please keep voice notes under {settings.max_voice_file_mb} MB.",
            )
            return

        transcription = await GroqService(settings).transcribe_audio(
            audio_bytes,
            filename="telegram-voice.ogg",
            content_type="audio/ogg",
        )
        transcript = str(transcription.get("text") or "").strip()
        if not transcript:
            await _send_message(update, "I detected a voice message, but I could not transcribe clear speech from it.")
            return

        if db is not None:
            create_log(
                db,
                level="info",
                event_type="telegram.voice_transcribed",
                detail=f"Transcribed voice message with {transcription.get('model')}.",
                telegram_chat_id=chat_id,
                latency_ms=int((perf_counter() - started_at) * 1000),
            )
            db.close()
            db = None
        await _send_message(update, _format_voice_detection(transcription))
        await _process_user_prompt(update, context, transcript)
    except Exception as exc:
        if db is not None:
            create_log(
                db,
                level="error",
                event_type="telegram.voice_failed",
                detail=str(exc),
                telegram_chat_id=chat_id,
                latency_ms=int((perf_counter() - started_at) * 1000),
            )
        await _send_message(update, "I could not process that voice message. Please try again or send the text.")
    finally:
        if db is not None:
            db.close()


async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands(build_bot_commands())


def create_telegram_application(settings: Settings) -> Application | None:
    if not settings.telegram_bot_token.strip():
        return None

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(True)
        .post_init(_post_init)
        .build()
    )
    application.bot_data["session_factory"] = SessionLocal
    application.bot_data["app_settings"] = settings

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("upgrade", upgrade_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("saved", saved_command))
    application.add_handler(CommandHandler(["new", "reset"], new_command))
    application.add_handler(CommandHandler("verify", verify_command))
    application.add_handler(
        CommandHandler(["ask", "summarize", "translate", "rewrite", "brainstorm", "plan", "code"], action_command)
    )
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(MessageHandler(filters.VOICE, voice_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    return application
