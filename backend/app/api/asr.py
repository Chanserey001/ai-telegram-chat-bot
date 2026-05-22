from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.dependencies import get_app_settings
from app.services.local_asr import get_local_asr_service


router = APIRouter(prefix="/asr", tags=["asr"])


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form("km"),
    authorization: str = Header(default=""),
    settings: Settings = Depends(get_app_settings),
):
    expected_token = settings.self_hosted_asr_token.strip()
    if expected_token:
        provided = authorization.removeprefix("Bearer ").strip()
        if provided != expected_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ASR token.")

    audio_bytes = await file.read()
    max_bytes = settings.max_voice_file_mb * 1024 * 1024
    if len(audio_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Audio file is larger than {settings.max_voice_file_mb} MB.",
        )

    service = get_local_asr_service(settings)
    try:
        return await run_in_threadpool(service.transcribe, audio_bytes, file.filename or "telegram-voice.ogg", language)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
