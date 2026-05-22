import math
import subprocess
import tempfile
from pathlib import Path

from app.config import Settings


class LocalAsrService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None

    def _initial_prompt(self, language: str) -> str | None:
        if language == "km":
            prompt = self.settings.local_asr_initial_prompt_km.strip()
            return prompt or None
        return None

    def _get_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "Local ASR is not installed. Install faster-whisper and its runtime dependencies first."
                ) from exc

            self._model = WhisperModel(
                self.settings.local_asr_model,
                device=self.settings.local_asr_device,
                compute_type=self.settings.local_asr_compute_type,
            )
        return self._model

    def _prepare_audio_path(self, temp_path: Path) -> Path:
        if not self.settings.local_asr_use_ffmpeg_preprocess:
            return temp_path

        processed_path = temp_path.with_suffix(".wav")
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(temp_path),
            "-ac",
            "1",
            "-ar",
            "16000",
        ]

        filters = self.settings.local_asr_ffmpeg_filters.strip()
        if filters:
            command.extend(["-af", filters])

        command.append(str(processed_path))

        try:
            subprocess.run(command, check=True, capture_output=True)
            return processed_path
        except FileNotFoundError:
            return temp_path
        except subprocess.CalledProcessError:
            processed_path.unlink(missing_ok=True)
            return temp_path

    def transcribe(self, audio_bytes: bytes, filename: str, language: str = "km") -> dict[str, float | str | None]:
        suffix = Path(filename).suffix or ".ogg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_path = Path(temp_file.name)
        prepared_path = self._prepare_audio_path(temp_path)

        try:
            segments, info = self._get_model().transcribe(
                str(prepared_path),
                language=language,
                task="transcribe",
                beam_size=max(1, self.settings.local_asr_beam_size),
                best_of=max(1, self.settings.local_asr_best_of),
                patience=max(1.0, self.settings.local_asr_patience),
                temperature=max(0.0, self.settings.local_asr_temperature),
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=self._initial_prompt(language),
            )
            segment_list = list(segments)
        finally:
            if prepared_path != temp_path:
                prepared_path.unlink(missing_ok=True)
            temp_path.unlink(missing_ok=True)

        text = " ".join(segment.text.strip() for segment in segment_list if segment.text.strip()).strip()
        confidences = [math.exp(float(segment.avg_logprob)) for segment in segment_list if segment.avg_logprob is not None]
        confidence = sum(confidences) / len(confidences) if confidences else None

        return {
            "text": text,
            "language": "Khmer" if language == "km" else language,
            "confidence": max(0.0, min(1.0, confidence)) if confidence is not None else None,
            "model": self.settings.local_asr_model,
        }


_local_asr_service: LocalAsrService | None = None


def get_local_asr_service(settings: Settings) -> LocalAsrService:
    global _local_asr_service
    if _local_asr_service is None:
        _local_asr_service = LocalAsrService(settings)
    return _local_asr_service
