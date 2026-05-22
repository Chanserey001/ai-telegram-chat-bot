import math
import re
import logging
import asyncio
from typing import Any, List

import httpx

from ..config import Settings
from ..models import BotSettings

logger = logging.getLogger(__name__)

LEGACY_MODEL_MAP = {
    "gemini-2.5-flash": "llama-3.1-8b-instant",
    "gemini-2.5-pro": "llama-3.3-70b-versatile",
}

# Model fallback chain to try if a specific model hits limits
MODEL_FALLBACKS = {
    "llama-3.3-70b-versatile": "llama-3.1-70b-versatile",
    "llama-3.1-70b-versatile": "llama-3.1-8b-instant",
    "llama3-70b-8192": "llama3-8b-8192",
}


class GroqService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Parse multiple API keys
        self.api_keys = [
            k.strip() 
            for k in self.settings.groq_api_key.split(",") 
            if k.strip() and k.strip() not in {"", "fill_me", "your_key_here", "change_me"}
        ]
        self._current_key_index = 0

    def _get_next_key(self) -> str | None:
        if not self.api_keys:
            return None
        key = self.api_keys[self._current_key_index]
        self._current_key_index = (self._current_key_index + 1) % len(self.api_keys)
        return key

    @staticmethod
    def _build_system_instruction(bot_settings: BotSettings, action_key: str | None) -> str:
        runtime_rules = (
            "Telegram runtime rules:\n"
            "- Sound natural and human, not academic or robotic.\n"
            "- Answer first. Keep simple requests to 1 to 4 short sentences.\n"
            "- Do not start with filler such as 'Okay, let's solve this', 'Let's analyze', or similar meta commentary.\n"
            "- Do not add headings such as 'Code Analysis', 'Explanation', 'Result', or 'Final Answer' unless the user explicitly asks for a structured format.\n"
            "- Avoid self-reference as an AI model.\n"
            "- Prefer plain text over heavy markdown.\n"
            "- Do not use markdown emphasis such as **bold**, *italic*, or decorative formatting in the final reply.\n"
            "- Use flat bullets only. Do not use nested bullet indentation.\n"
            "- If the user asks for code output or a direct fact, give the answer plainly and add only a brief reason if useful."
        )
        if action_key == "code":
            runtime_rules += (
                "\nCode workflow rules:\n"
                "- If the user gives buggy code and asks for a fix, return the full corrected code first.\n"
                "- Preserve the original structure and line order unless a restructure is required to fix the bug.\n"
                "- Prefer minimal edits over rewriting the entire code.\n"
                "- Do not wrap the user's snippets inside a new function, class, or test harness unless they asked for that.\n"
                "- Do not fill the code with tutorial-style comments.\n"
                "- Keep the code clean and runnable, with only minimal necessary comments.\n"
                "- After the corrected code, add a short fix list with only the main changes.\n"
                "- Use plain bullets, not markdown bold.\n"
                "- Do not cut off the answer after an opening code fence.\n"
                "- Keep the explanation shorter than the corrected code."
            )
        return f"{bot_settings.system_prompt.strip()}\n\n{runtime_rules}"

    @staticmethod
    def _strip_plain_text_markdown(text: str) -> str:
        cleaned = text
        cleaned = re.sub(r"(?m)^\s*\*\s+", "- ", cleaned)
        cleaned = re.sub(r"(?m)^\s*\d+\.\s+", "- ", cleaned)
        cleaned = re.sub(r"(?m)^\s+[-*]\s+", "- ", cleaned)
        cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
        cleaned = re.sub(r"(?<!\*)\*([^\n*]+)\*(?!\*)", r"\1", cleaned)
        cleaned = re.sub(r"(?<!_)_([^\n_]+)_(?!_)", r"\1", cleaned)
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @classmethod
    def _sanitize_response(cls, text: str) -> str:
        cleaned = text.strip()
        replacements = [
            (r"^\s*(Okay|Sure|Certainly)[,!.]?\s+(let'?s|here(?:'s| is))\s+(?:solve|analyze|break down|walk through).*\n+", ""),
            (r"^\s*Let'?s\s+(?:solve|analyze|break down|walk through).*\n+", ""),
            (r"(?im)^\s*\*?\*?(Code Analysis|Analysis|Explanation|Result|Final Answer)\*?\*?\s*:?\s*$\n?", ""),
            (r"(?im)^\s*When this code is executed, the output will be:\s*\n?", ""),
            (r"(?im)^\s*The output (?:will be|is):\s*\n?", ""),
            (r"(?im)^\s*Here'?s (?:the )?(?:analysis|result|breakdown):\s*\n?", ""),
        ]

        for pattern, replacement in replacements:
            cleaned = re.sub(pattern, replacement, cleaned)

        if cleaned.count("```") % 2 == 1:
            cleaned = f"{cleaned}\n```"

        parts = re.split(r"(```.*?```)", cleaned, flags=re.DOTALL)
        normalized_parts: list[str] = []
        for part in parts:
            if not part:
                continue
            if part.startswith("```") and part.endswith("```"):
                normalized_parts.append(part.strip())
            else:
                normalized_parts.append(cls._strip_plain_text_markdown(part))

        cleaned = "\n\n".join(part for part in normalized_parts if part).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if not cleaned:
            return "I could not generate a response right now."
        return cleaned

    @staticmethod
    def _resolve_max_output_tokens(
        bot_settings: BotSettings,
        action_key: str | None,
        user_input: str,
    ) -> int:
        configured = max(256, int(bot_settings.max_output_tokens))
        if action_key == "code":
            if len(user_input) >= 1800:
                return min(4096, max(configured, 2600))
            if len(user_input) >= 900:
                return min(4096, max(configured, 1800))
            return min(4096, max(configured, 1200))
        if len(user_input) >= 1800:
            return min(4096, max(configured, 1200))
        return min(4096, configured)

    @staticmethod
    def _normalize_model_name(model_name: str | None, fallback_model: str) -> str:
        if not model_name or not model_name.strip():
            return fallback_model
        cleaned = model_name.strip()
        return LEGACY_MODEL_MAP.get(cleaned, cleaned)

    @staticmethod
    def _normalize_message_role(role: str) -> str:
        return "assistant" if role == "model" else role

    @staticmethod
    def _extract_text_content(content: str | list[dict[str, str]] | None) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "\n".join(
                item.get("text", "").strip()
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ).strip()
        return ""

    @staticmethod
    def _confidence_from_segments(segments: list[dict] | None) -> float | None:
        if not segments:
            return None

        weighted_score = 0.0
        total_weight = 0.0
        for segment in segments:
            avg_logprob = segment.get("avg_logprob")
            if not isinstance(avg_logprob, (int, float)):
                continue
            start = segment.get("start")
            end = segment.get("end")
            duration = end - start if isinstance(start, (int, float)) and isinstance(end, (int, float)) else 1.0
            weight = max(float(duration), 0.1)
            weighted_score += math.exp(float(avg_logprob)) * weight
            total_weight += weight

        if total_weight == 0:
            return None
        return max(0.0, min(1.0, weighted_score / total_weight))

    @staticmethod
    def _normalize_language(language: str | None) -> str | None:
        if not language:
            return None
        normalized = language.strip().lower()
        labels = {
            "en": "English",
            "eng": "English",
            "english": "English",
            "km": "Khmer",
            "kh": "Khmer",
            "khm": "Khmer",
            "khmer": "Khmer",
            "central khmer": "Khmer",
        }
        return labels.get(normalized, language.strip().title())

    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        *,
        filename: str,
        content_type: str,
    ) -> dict[str, float | str | None]:
        if not self.api_keys:
            raise RuntimeError("The assistant is not configured yet. Add a Groq API key in the backend settings.")

        last_error = None
        for _ in range(len(self.api_keys)):
            api_key = self._get_next_key()
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    response = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        data={
                            "model": self.settings.groq_transcription_model,
                            "response_format": "verbose_json",
                            "temperature": "0",
                        },
                        files={"file": (filename, audio_bytes, content_type)},
                    )
                    
                    if response.status_code == 429:
                        logger.warning(f"Groq API key rate limited, trying next key...")
                        continue
                        
                    response.raise_for_status()
                    data = response.json()
                    
                    text = str(data.get("text") or "").strip()
                    language = self._normalize_language(data.get("language"))
                    confidence = self._confidence_from_segments(data.get("segments"))
                    return {
                        "text": text,
                        "language": language,
                        "confidence": confidence,
                        "model": self.settings.groq_transcription_model,
                    }
            except Exception as e:
                last_error = e
                logger.error(f"Groq transcription error: {e}")
                continue
                
        raise last_error or RuntimeError("All Groq API keys failed for transcription.")

    async def generate_reply(
        self,
        bot_settings: BotSettings,
        history: list[dict[str, str]],
        *,
        preferred_model: str | None = None,
        action_key: str | None = None,
        user_input: str = "",
    ) -> dict[str, int | str]:
        if not self.api_keys:
            return {
                "text": "The assistant is not configured yet. Add a Groq API key in the backend settings.",
                "token_usage": 0,
                "model": bot_settings.default_model,
            }

        original_model = self._normalize_model_name(
            preferred_model or bot_settings.default_model,
            self.settings.groq_model,
        )
        
        current_model = original_model
        messages = [
            {"role": "system", "content": self._build_system_instruction(bot_settings, action_key)},
            *[
                {
                    "role": self._normalize_message_role(item["role"]),
                    "content": item["text"],
                }
                for item in history
            ],
        ]

        last_error_text = "I could not generate a response right now."
        
        # Try rotation and model fallback
        # Total attempts: Keys * Fallbacks
        for _key_attempt in range(len(self.api_keys)):
            api_key = self._get_next_key()
            
            # Reset model to original for each new key attempt
            current_model = original_model
            
            while True:
                try:
                    async with httpx.AsyncClient(timeout=45) as client:
                        response = await client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": current_model,
                                "messages": messages,
                                "temperature": bot_settings.temperature,
                                "max_completion_tokens": self._resolve_max_output_tokens(
                                    bot_settings,
                                    action_key,
                                    user_input,
                                ),
                            },
                        )
                        
                        if response.status_code == 429:
                            # If rate limited, try next model first, then next key
                            if current_model in MODEL_FALLBACKS:
                                next_m = MODEL_FALLBACKS[current_model]
                                logger.warning(f"Model {current_model} limited. Falling back to {next_m}")
                                current_model = next_m
                                continue
                            else:
                                logger.warning("Rate limited and no more model fallbacks for this key.")
                                break # Exit inner while, try next key
                        
                        response.raise_for_status()
                        data = response.json()

                        text = self._sanitize_response(
                            self._extract_text_content(
                                data.get("choices", [{}])[0].get("message", {}).get("content")
                            )
                        )
                        token_usage = data.get("usage", {}).get("total_tokens", 0)
                        return {"text": text, "token_usage": token_usage, "model": current_model}
                        
                except Exception as e:
                    logger.error(f"Groq generate_reply error with {current_model}: {e}")
                    last_error_text = f"Error: {str(e)}"
                    break # Try next key

        return {"text": last_error_text, "token_usage": 0, "model": current_model}
