from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..dependencies import require_admin_token
from ..services.exports import build_khmer_pdf_full

router = APIRouter(prefix="/api/export", tags=["export"])


class ExportMessage(BaseModel):
    sender_type: str
    content: str
    created_at: str = ""


class PdfExportRequest(BaseModel):
    title: str
    messages: list[ExportMessage]


@router.post("/pdf")
async def generate_pdf(
    body: PdfExportRequest,
    _: None = Depends(require_admin_token),
) -> Response:
    try:
        pdf_bytes = await build_khmer_pdf_full(
            body.title,
            [m.model_dump() for m in body.messages],
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="PDF generation failed") from exc

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=export.pdf"},
    )
