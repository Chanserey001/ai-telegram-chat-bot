from __future__ import annotations

from html import escape

from ..config import Settings
from ..crud import create_payment_transaction, get_payment_transaction_by_ref, update_payment_transaction
from ..models import PaymentTransaction, User
from ..schemas import PaymentAdminUpdate


PAYMENT_METHOD_LABELS = {
    "aba_payway": "ABA PayWay sandbox",
    "bakong_khqr": "Bakong KHQR demo",
    "khqr": "Bakong KHQR demo",
}


def build_payment_method_label(method: str) -> str:
    normalized = normalize_payment_method(method)
    return PAYMENT_METHOD_LABELS.get(normalized, normalized.replace("_", " ").title())


def normalize_payment_method(method: str) -> str:
    normalized = method.strip().lower()
    if normalized == "khqr":
        return "bakong_khqr"
    return normalized


def _configured_or_fallback(value: str | None, fallback: str) -> str:
    cleaned = (value or "").strip()
    return cleaned or fallback


def _build_bakong_khqr_demo_payload(settings: Settings, payment: PaymentTransaction) -> str:
    account_id = _configured_or_fallback(settings.bakong_khqr_account_id, "merchant@bank")
    merchant_name = _configured_or_fallback(settings.bakong_khqr_merchant_name, "Your Merchant Name")
    merchant_city = _configured_or_fallback(settings.bakong_khqr_merchant_city, "Phnom Penh")
    country_code = _configured_or_fallback(settings.bakong_khqr_country_code, "KH")
    merchant_id = _configured_or_fallback(settings.bakong_khqr_merchant_id, "00000001")
    acquiring_bank = _configured_or_fallback(settings.bakong_khqr_acquiring_bank, "Bakong member bank")
    store_label = _configured_or_fallback(settings.bakong_khqr_store_label, "VIP")
    terminal_label = _configured_or_fallback(settings.bakong_khqr_terminal_label, "BOT01")
    callback_url = _configured_or_fallback(
        settings.bakong_khqr_callback_url,
        f"{settings.app_base_url.rstrip('/')}/payments/{payment.merchant_ref}",
    )
    return (
        "BAKONG KHQR DEMO\n"
        f"Merchant name: {merchant_name}\n"
        f"Bakong account ID: {account_id}\n"
        f"Merchant ID: {merchant_id}\n"
        f"Acquiring bank: {acquiring_bank}\n"
        f"Merchant city: {merchant_city}\n"
        f"Country code: {country_code}\n"
        f"Store label: {store_label}\n"
        f"Terminal label: {terminal_label}\n"
        f"Amount: {payment.amount:.2f} {payment.currency}\n"
        f"Reference: {payment.merchant_ref}\n"
        f"Callback URL: {callback_url}"
    )


def _build_bakong_khqr_note(settings: Settings) -> str:
    merchant_name = _configured_or_fallback(settings.bakong_khqr_merchant_name, "your merchant name")
    account_id = _configured_or_fallback(settings.bakong_khqr_account_id, "merchant@bank")
    return (
        "Bakong KHQR demo flow. This page shows the merchant fields you will later replace with a real "
        "KHQR string or provider callback flow.\n\n"
        f"Current demo merchant profile: {merchant_name} / {account_id}.\n"
        "For now, use the demo complete button or approve the payment from the dashboard."
    )


def create_vip_payment(
    db,
    *,
    settings: Settings,
    user: User,
    payment_method: str,
) -> PaymentTransaction:
    normalized_method = normalize_payment_method(payment_method)
    payment = create_payment_transaction(
        db,
        user_id=user.id,
        telegram_chat_id=user.telegram_chat_id,
        amount=settings.vip_plan_price_usd,
        currency="USD",
        provider="aba_payway" if normalized_method == "aba_payway" else "bakong_khqr_demo",
        payment_method=normalized_method,
        plan_type="VIP",
    )
    payment.checkout_url = f"{settings.app_base_url.rstrip('/')}/payments/{payment.merchant_ref}"
    if normalized_method == "bakong_khqr":
        payment.qr_note = _build_bakong_khqr_note(settings)
        payment.qr_payload = _build_bakong_khqr_demo_payload(settings, payment)
    else:
        payment.qr_note = (
            "ABA PayWay sandbox demo flow. Open the payment page and complete the demo payment. "
            "Replace this with real sandbox checkout after merchant setup."
        )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def complete_demo_payment(db, *, settings: Settings, merchant_ref: str) -> PaymentTransaction:
    return update_payment_transaction(
        db,
        merchant_ref=merchant_ref,
        payload=PaymentAdminUpdate(status="Approved"),
        vip_duration_days=settings.vip_duration_days,
    )


def build_payment_page(payment: PaymentTransaction, settings: Settings) -> str:
    label = build_payment_method_label(payment.payment_method)
    status = escape(payment.status)
    note = escape(payment.qr_note or "")
    qr_payload = escape(payment.qr_payload or "")
    checkout_url = escape(payment.checkout_url or "")
    complete_action = f"/payments/{payment.merchant_ref}/complete-demo"
    bakong_mode = normalize_payment_method(payment.payment_method) == "bakong_khqr"
    intro = (
        "This page is designed to look like a Bakong KHQR merchant checkout for your study demo. "
        "The payload below is a structured demo profile, not a live QR settlement string yet."
        if bakong_mode
        else "This is a demo-friendly payment page for your Telegram AI assistant. The full live ABA sandbox "
        "checkout depends on merchant setup and whitelisting. This page lets you complete the demo flow now."
    )
    bakong_tips = ""
    if bakong_mode:
        bakong_tips = """
    <div class="tips">
      <h2>What you will replace later</h2>
      <ul>
        <li>Real Bakong account ID</li>
        <li>Real merchant name and merchant ID</li>
        <li>Member bank / acquiring bank name</li>
        <li>Real callback or verification URL</li>
        <li>A real KHQR string generated by Bakong SDK or your payment partner</li>
      </ul>
    </div>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>VIP Payment</title>
  <style>
    body {{
      margin: 0;
      font-family: Arial, sans-serif;
      background: linear-gradient(135deg, #f7efe0, #eef5ff);
      color: #1f2937;
    }}
    .wrap {{
      max-width: 720px;
      margin: 40px auto;
      background: white;
      border-radius: 18px;
      padding: 28px;
      box-shadow: 0 24px 70px rgba(15, 23, 42, 0.12);
    }}
    h1 {{
      margin-top: 0;
      font-size: 28px;
    }}
    .pill {{
      display: inline-block;
      padding: 6px 12px;
      border-radius: 999px;
      background: #e5edff;
      color: #2349b6;
      font-weight: 700;
      margin-bottom: 14px;
    }}
    .qr {{
      white-space: pre-wrap;
      background: #f8fafc;
      border: 1px solid #dbe2f0;
      border-radius: 14px;
      padding: 16px;
      font-family: Consolas, monospace;
      margin: 18px 0;
    }}
    .meta {{
      display: grid;
      grid-template-columns: 180px 1fr;
      gap: 10px;
      margin: 18px 0;
    }}
    .meta strong {{
      color: #0f172a;
    }}
    .actions {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 22px;
    }}
    .tips {{
      margin-top: 20px;
      background: #fff7ed;
      border: 1px solid #fed7aa;
      border-radius: 14px;
      padding: 16px;
    }}
    .tips h2 {{
      margin: 0 0 8px;
      font-size: 18px;
    }}
    .tips ul {{
      margin: 0;
      padding-left: 18px;
    }}
    .tips li {{
      margin: 6px 0;
    }}
    button, a {{
      border: none;
      border-radius: 12px;
      padding: 12px 18px;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
    }}
    button {{
      background: #1d4ed8;
      color: white;
    }}
    a {{
      background: #f3f4f6;
      color: #111827;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <span class="pill">{label}</span>
    <h1>VIP upgrade payment</h1>
    <p>{intro}</p>

    <div class="meta">
      <strong>Status</strong><span>{status}</span>
      <strong>Merchant ref</strong><span>{escape(payment.merchant_ref)}</span>
      <strong>Amount</strong><span>{payment.amount:.2f} {escape(payment.currency)}</span>
      <strong>Plan</strong><span>{escape(payment.plan_type)}</span>
      <strong>App URL</strong><span>{checkout_url}</span>
    </div>

    <p>{note}</p>
    <div class="qr">{qr_payload or "Open the demo checkout and complete payment from this page."}</div>
{bakong_tips}

    <div class="actions">
      <form method="post" action="{complete_action}">
        <button type="submit">Complete demo payment</button>
      </form>
      <a href="{checkout_url}">Refresh status</a>
    </div>
  </div>
</body>
</html>"""


def build_payment_success_page(payment: PaymentTransaction) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>VIP Activated</title>
  <style>
    body {{
      margin: 0;
      font-family: Arial, sans-serif;
      display: grid;
      place-items: center;
      min-height: 100vh;
      background: linear-gradient(135deg, #ecfdf5, #eef2ff);
      color: #064e3b;
    }}
    .card {{
      background: white;
      padding: 28px;
      border-radius: 18px;
      box-shadow: 0 24px 70px rgba(15, 23, 42, 0.12);
      max-width: 520px;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>VIP activated</h1>
    <p>Payment reference <strong>{escape(payment.merchant_ref)}</strong> was approved.</p>
    <p>You can return to Telegram and use /profile to confirm your VIP plan.</p>
  </div>
</body>
</html>"""
