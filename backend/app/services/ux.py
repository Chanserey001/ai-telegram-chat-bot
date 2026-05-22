from __future__ import annotations

from dataclasses import dataclass
from random import sample

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup


@dataclass(frozen=True)
class ActionDefinition:
    key: str
    label: str
    category: str
    instruction: str
    ask: str


ACTIONS: dict[str, ActionDefinition] = {
    "ask": ActionDefinition(
        key="ask",
        label="Ask anything",
        category="general",
        instruction="Answer the user's question clearly and helpfully.",
        ask="Send your question and I will answer it clearly.",
    ),
    "summarize": ActionDefinition(
        key="summarize",
        label="Summarize",
        category="study",
        instruction="Summarize the user's content into key points, action items, and a short takeaway.",
        ask="Send the text, notes, or message you want summarized.",
    ),
    "translate": ActionDefinition(
        key="translate",
        label="Translate",
        category="work",
        instruction="Translate the user's text naturally and preserve meaning. If target language is unclear, ask briefly once.",
        ask="Send the text you want translated and mention the target language if needed.",
    ),
    "rewrite": ActionDefinition(
        key="rewrite",
        label="Rewrite",
        category="content",
        instruction="Rewrite the user's text to be clearer, cleaner, and more polished while preserving intent.",
        ask="Send the text you want rewritten.",
    ),
    "brainstorm": ActionDefinition(
        key="brainstorm",
        label="Brainstorm",
        category="content",
        instruction="Generate practical ideas with variety. Group results and keep them actionable.",
        ask="Tell me the topic and goal, and I will brainstorm ideas.",
    ),
    "plan": ActionDefinition(
        key="plan",
        label="Make a plan",
        category="life",
        instruction="Create a practical step-by-step plan with timeline, priorities, and next actions.",
        ask="Tell me what you want to plan: study, work, travel, launch, or something else.",
    ),
    "code": ActionDefinition(
        key="code",
        label="Coding help",
        category="coding",
        instruction="Help with programming, debugging, and code explanation. Be concrete and technical.",
        ask="Send your code problem, error, or snippet and I will help debug it.",
    ),
}

ACTION_LABELS = {key: action.label for key, action in ACTIONS.items()}

CATEGORIES: dict[str, tuple[str, list[str]]] = {
    "study": ("Study", ["summarize", "ask"]),
    "work": ("Work", ["translate", "plan", "rewrite"]),
    "content": ("Content", ["brainstorm", "rewrite"]),
    "coding": ("Coding", ["code", "ask"]),
    "life": ("Life", ["plan", "brainstorm"]),
    "utilities": ("Utilities", ["summarize", "translate", "rewrite"]),
}

TONE_OPTIONS = {
    "balanced": "Balanced",
    "friendly": "Friendly",
    "professional": "Professional",
    "coach": "Coach",
}

STYLE_OPTIONS = {
    "concise": "Concise",
    "detailed": "Detailed",
    "bullet": "Bullet points",
}

MODEL_OPTIONS = {
    "groq/compound-mini": "Groq Compound Mini",
    "groq/compound": "Groq Compound",
    "allam-2-7b": "Allam 2 7B",
    "llama-3.1-8b-instant": "Groq Llama 3.1 8B Instant",
    "llama-3.3-70b-versatile": "Groq Llama 3.3 70B Versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct": "Meta Llama 4 Scout 17B",
    "qwen/qwen3-32b": "Qwen 3 32B",
    "openai/gpt-oss-20b": "Groq GPT-OSS 20B",
    "openai/gpt-oss-120b": "Groq GPT-OSS 120B",
    "gemini-2.5-flash": "Groq Llama 3.1 8B Instant",
    "gemini-2.5-pro": "Groq Llama 3.3 70B Versatile",
}


def get_daily_limit(plan_type: str, free_limit: int, vip_limit: int) -> int:
    return vip_limit if plan_type.lower() == "vip" else free_limit


def build_main_menu() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Study", callback_data="cat:study"),
            InlineKeyboardButton("Work", callback_data="cat:work"),
        ],
        [
            InlineKeyboardButton("Content", callback_data="cat:content"),
            InlineKeyboardButton("Coding", callback_data="cat:coding"),
        ],
        [
            InlineKeyboardButton("Life", callback_data="cat:life"),
            InlineKeyboardButton("Utilities", callback_data="cat:utilities"),
        ],
        [
            InlineKeyboardButton("Profile", callback_data="nav:profile"),
            InlineKeyboardButton("Settings", callback_data="nav:settings"),
        ],
        [
            InlineKeyboardButton("Upgrade VIP", callback_data="nav:upgrade"),
            InlineKeyboardButton("Payments", callback_data="nav:payments"),
        ],
        [
            InlineKeyboardButton("History", callback_data="nav:history"),
            InlineKeyboardButton("Help", callback_data="nav:help"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def build_category_menu(category_key: str) -> InlineKeyboardMarkup:
    _, action_keys = CATEGORIES[category_key]
    rows = []
    for action_key in action_keys:
        action = ACTIONS[action_key]
        rows.append([InlineKeyboardButton(action.label, callback_data=f"act:{category_key}:{action.key}")])
    rows.append([InlineKeyboardButton("Back to menu", callback_data="nav:menu")])
    return InlineKeyboardMarkup(rows)


def build_post_answer_menu() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Summarize", callback_data="act:summarize"),
            InlineKeyboardButton("Rewrite", callback_data="act:rewrite"),
        ],
        [
            InlineKeyboardButton("Translate", callback_data="act:translate"),
            InlineKeyboardButton("Brainstorm", callback_data="act:brainstorm"),
        ],
        [
            InlineKeyboardButton("Save as", callback_data="nav:save"),
            InlineKeyboardButton("Main menu", callback_data="nav:menu"),
        ],
        [InlineKeyboardButton("Stop", callback_data="nav:stop")],
    ]
    return InlineKeyboardMarkup(rows)


def build_save_scope_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Latest answer", callback_data="save_scope:latest")],
        [InlineKeyboardButton("Recent 5 exchanges", callback_data="save_scope:recent")],
        [InlineKeyboardButton("Current chat", callback_data="save_scope:chat")],
        [InlineKeyboardButton("Back", callback_data="nav:save_back")],
    ]
    return InlineKeyboardMarkup(rows)


def build_save_format_menu(scope: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Text", callback_data=f"savefmt:{scope}:text"),
            InlineKeyboardButton("PDF", callback_data=f"savefmt:{scope}:pdf"),
        ],
        [
            InlineKeyboardButton("Word", callback_data=f"savefmt:{scope}:word"),
            InlineKeyboardButton("Excel", callback_data=f"savefmt:{scope}:excel"),
        ],
        [InlineKeyboardButton("Back", callback_data="nav:save_scope_back")],
    ]
    return InlineKeyboardMarkup(rows)


def build_settings_menu() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Tone", callback_data="settings:tone"),
            InlineKeyboardButton("Reply style", callback_data="settings:style"),
        ],
        [InlineKeyboardButton("Model info", callback_data="settings:model")],
        [InlineKeyboardButton("Back to menu", callback_data="nav:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_profile_menu(can_upgrade: bool, has_pending_payment: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_upgrade:
        rows.append([InlineKeyboardButton("Upgrade to VIP", callback_data="nav:upgrade")])
    if has_pending_payment:
        rows.append([InlineKeyboardButton("Check payment status", callback_data="nav:payments")])
    rows.append([InlineKeyboardButton("Settings", callback_data="nav:settings")])
    rows.append([InlineKeyboardButton("Back to menu", callback_data="nav:menu")])
    return InlineKeyboardMarkup(rows)


def build_upgrade_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("ABA PayWay sandbox", callback_data="upgrade:aba_payway")],
        [InlineKeyboardButton("Bakong KHQR demo", callback_data="upgrade:bakong_khqr")],
        [InlineKeyboardButton("Back", callback_data="nav:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_payment_status_menu(checkout_url: str | None, merchant_ref: str) -> InlineKeyboardMarkup:
    rows = []
    if checkout_url:
        rows.append([InlineKeyboardButton("Open payment page", url=checkout_url)])
    rows.append([InlineKeyboardButton("Check payment status", callback_data=f"payment:check:{merchant_ref}")])
    rows.append([InlineKeyboardButton("Back to menu", callback_data="nav:menu")])
    return InlineKeyboardMarkup(rows)


def build_tone_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"tone:{key}")]
        for key, label in TONE_OPTIONS.items()
    ]
    rows.append([InlineKeyboardButton("Back", callback_data="nav:settings")])
    return InlineKeyboardMarkup(rows)


def build_style_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"style:{key}")]
        for key, label in STYLE_OPTIONS.items()
    ]
    rows.append([InlineKeyboardButton("Back", callback_data="nav:settings")])
    return InlineKeyboardMarkup(rows)


def build_model_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"model:{key}")]
        for key, label in MODEL_OPTIONS.items()
    ]
    rows.append([InlineKeyboardButton("Use dashboard default", callback_data="model:default")])
    rows.append([InlineKeyboardButton("Back", callback_data="nav:settings")])
    return InlineKeyboardMarkup(rows)


def build_verification_prompt(answer: int) -> InlineKeyboardMarkup:
    choices = sample([answer - 1, answer, answer + 1, answer + 2], 4)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(str(choices[0]), callback_data=f"verify:{choices[0]}"),
                InlineKeyboardButton(str(choices[1]), callback_data=f"verify:{choices[1]}"),
            ],
            [
                InlineKeyboardButton(str(choices[2]), callback_data=f"verify:{choices[2]}"),
                InlineKeyboardButton(str(choices[3]), callback_data=f"verify:{choices[3]}"),
            ],
        ]
    )


def build_bot_commands() -> list[BotCommand]:
    return [
        BotCommand("start", "Open the assistant and choose a workflow"),
        BotCommand("menu", "Show the main categories"),
        BotCommand("ask", "Ask a general question"),
        BotCommand("summarize", "Summarize text or notes"),
        BotCommand("translate", "Translate text"),
        BotCommand("rewrite", "Rewrite text cleanly"),
        BotCommand("brainstorm", "Generate ideas"),
        BotCommand("plan", "Make a step-by-step plan"),
        BotCommand("code", "Get coding help"),
        BotCommand("history", "See your recent conversation"),
        BotCommand("saved", "See saved AI and user exchanges"),
        BotCommand("profile", "See plan and usage"),
        BotCommand("upgrade", "Start the VIP upgrade payment flow"),
        BotCommand("settings", "Adjust tone, reply style, and model"),
        BotCommand("stop", "Stop the current task or AI generation"),
        BotCommand("new", "Start a fresh flow"),
        BotCommand("reset", "Reset current conversation"),
        BotCommand("verify", "Verify if your account is gated"),
        BotCommand("help", "How to use the bot"),
    ]


def build_action_prompt(action_key: str, preferred_tone: str, reply_style: str, text: str) -> str:
    action = ACTIONS[action_key]
    response_rules = (
        "Response rules:\n"
        "- Answer the request directly before any explanation.\n"
        "- For simple questions, use short natural sentences and avoid headings.\n"
        "- Do not use labels like Code Analysis, Explanation, Result, or Final Answer unless the user asked for a structured breakdown.\n"
        "- Avoid sounding like a textbook or generic AI assistant.\n"
        "- Use bullets only when the answer truly needs a list.\n"
        "- Use flat bullets only. Do not use nested bullets.\n"
        "- For long answers, keep the structure easy to scan with short sections and short flat bullets.\n"
    )
    if action_key == "code":
        response_rules += (
            "- If the user asks to fix or debug code, return the full corrected code first.\n"
            "- Keep the original structure and order whenever possible.\n"
            "- Make minimal edits instead of rewriting the whole solution.\n"
            "- Do not wrap the user's code in a new function, class, or demo runner unless the user asked for that.\n"
            "- Do not add many explanatory comments inside the code.\n"
            "- Keep existing comments only if they help identify the bug. Do not add decorative comments.\n"
            "- If the user pasted multiple buggy snippets, fix them in place instead of merging them into one new program.\n"
            "- After the corrected code, list the main fixes briefly in 3 to 6 plain bullets.\n"
            "- Do not use markdown bold in the fix list.\n"
            "- Do not stop after an opening code fence or partial snippet.\n"
            "- Keep explanations concise, but do not omit required fixed lines.\n"
        )
    if action_key == "plan":
        response_rules += (
            "- Organize long plans with a short opening line and flat bullet lists.\n"
            "- Avoid multi-level bullet indentation.\n"
            "- Keep each bullet practical and action-oriented.\n"
        )

    return (
        f"Workflow: {action.label}\n"
        f"Tone preference: {preferred_tone}\n"
        f"Reply style: {reply_style}\n"
        f"Special instruction: {action.instruction}\n\n"
        f"{response_rules}\n"
        f"User input:\n{text}"
    )


def resolve_model_label(model_name: str | None, fallback_model: str) -> str:
    if not model_name:
        return f"Dashboard default ({MODEL_OPTIONS.get(fallback_model, fallback_model)})"
    return MODEL_OPTIONS.get(model_name, model_name)


def history_preview(lines: list[str]) -> str:
    if not lines:
        return "No recent messages yet. Use /menu to start a guided task."
    return "\n".join(lines)
