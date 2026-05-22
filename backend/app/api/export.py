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
    
    class Config:
        # Ensure proper UTF-8 encoding for Khmer text
        json_encoders = {
            str: lambda v: v.encode('utf-8', errors='replace').decode('utf-8') if isinstance(v, str) else v
        }


class PdfExportRequest(BaseModel):
    title: str
    messages: list[ExportMessage]
    
    class Config:
        json_encoders = {
            str: lambda v: v.encode('utf-8', errors='replace').decode('utf-8') if isinstance(v, str) else v
        }


@router.post("/pdf")
async def generate_pdf(
    body: PdfExportRequest,
    _: None = Depends(require_admin_token),
) -> Response:
    try:
        # Ensure all text is properly UTF-8 encoded
        messages_data = []
        for m in body.messages:
            msg_dict = m.model_dump()
            # Validate UTF-8 for each field
            for key in ['sender_type', 'content', 'created_at']:
                if key in msg_dict and isinstance(msg_dict[key], str):
                    try:
                        msg_dict[key].encode('utf-8').decode('utf-8')
                    except Exception:
                        msg_dict[key] = "[Encoding Error]"
            messages_data.append(msg_dict)
        
        pdf_bytes = await build_khmer_pdf_full(
            body.title,
            messages_data,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(exc)}") from exc

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=export.pdf"},
    )
