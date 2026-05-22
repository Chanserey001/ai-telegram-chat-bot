from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..config import Settings
from ..crud import get_payment_transaction_by_ref
from ..dependencies import get_app_settings, get_db
from ..services.payments import build_payment_page, build_payment_success_page, complete_demo_payment


router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("/{merchant_ref}", response_class=HTMLResponse)
def payment_page(
    merchant_ref: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    payment = get_payment_transaction_by_ref(db, merchant_ref=merchant_ref)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found.")
    return HTMLResponse(build_payment_page(payment, settings))


@router.post("/{merchant_ref}/complete-demo", response_class=HTMLResponse)
def complete_payment_demo(
    merchant_ref: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    payment = complete_demo_payment(db, settings=settings, merchant_ref=merchant_ref)
    return HTMLResponse(build_payment_success_page(payment))
