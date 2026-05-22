from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..crud import (
    get_bot_settings,
    get_overview_stats,
    list_payment_transactions,
    list_logs,
    list_users,
    update_bot_settings,
    update_payment_transaction,
    update_user_admin_settings,
)
from ..dependencies import get_db, require_admin_token
from ..schemas import (
    BotSettingsRead,
    BotSettingsUpdate,
    LogRead,
    OverviewStats,
    PaymentAdminUpdate,
    PaymentRead,
    UserAdminUpdate,
    UserRead,
)
from ..config import get_settings


router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/overview", response_model=OverviewStats, dependencies=[Depends(require_admin_token)])
def overview(db: Session = Depends(get_db)):
    return get_overview_stats(db)


@router.get("/users", dependencies=[Depends(require_admin_token)])
def users(db: Session = Depends(get_db)):
    return {"users": [UserRead.model_validate(item) for item in list_users(db)]}


@router.patch("/users/{user_id}", dependencies=[Depends(require_admin_token)])
def update_user(user_id: int, payload: UserAdminUpdate, db: Session = Depends(get_db)):
    try:
        user = update_user_admin_settings(
            db,
            user_id=user_id,
            plan_type=payload.plan_type,
            status=payload.status,
            preferred_model=payload.preferred_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return UserRead.model_validate(user)


@router.get("/logs", dependencies=[Depends(require_admin_token)])
def logs(
    db: Session = Depends(get_db),
    level: str | None = Query(default=None),
    q: str | None = Query(default=None),
    chat_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=20, le=500),
):
    return {
        "logs": [
            LogRead.model_validate(item)
            for item in list_logs(db, level=level, event_query=q, chat_query=chat_id, limit=limit)
        ]
    }


@router.get("/payments", dependencies=[Depends(require_admin_token)])
def payments(db: Session = Depends(get_db)):
    return {"payments": [PaymentRead.model_validate(item) for item in list_payment_transactions(db)]}


@router.patch("/payments/{merchant_ref}", dependencies=[Depends(require_admin_token)])
def update_payment(
    merchant_ref: str,
    payload: PaymentAdminUpdate,
    db: Session = Depends(get_db),
):
    try:
        payment = update_payment_transaction(
            db,
            merchant_ref=merchant_ref,
            payload=payload,
            vip_duration_days=get_settings().vip_duration_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PaymentRead.model_validate(payment)


@router.get("/settings", response_model=BotSettingsRead, dependencies=[Depends(require_admin_token)])
def read_settings(db: Session = Depends(get_db)):
    return get_bot_settings(db)


@router.put("/settings", response_model=BotSettingsRead, dependencies=[Depends(require_admin_token)])
def write_settings(payload: BotSettingsUpdate, db: Session = Depends(get_db)):
    return update_bot_settings(db, payload)
