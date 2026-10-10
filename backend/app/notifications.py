"""Staff notification of new registrations, via the Zalo Bot API.

Off unless BOTH ``ZALO_BOT_TOKEN`` and ``ZALO_NOTIFY_CHAT_ID`` are set.

The API is Telegram-shaped: ``POST https://bot-api.zapps.me/bot<TOKEN>/sendMessage``
with ``{"chat_id": ..., "text": ...}``, answered ``{"ok": true, "result": ...}``
or ``{"ok": false, "error_code": ..., "description": ...}``.

Three rules this module keeps:

- **It never breaks a registration.** It runs after the response is sent
  (a FastAPI background task) and swallows every error.
- **It never logs the token.** The token is part of the URL. httpx logs that
  URL and its exceptions quote it, so the token is registered with the log
  scrubber and only the exception CLASS is logged here.
- **It never carries the password.** The message is built from an explicit
  allow-list of lead fields; nothing else reaches it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from .config import Settings
from .logging_filters import register_secret

logger = logging.getLogger("viporder.notifications")

ZALO_BOT_API = "https://bot-api.zapps.me"
TIMEOUT_SECONDS = 5.0
# Zalo truncates beyond 2000 characters; stay well under it.
MAX_TEXT = 1800

VN_TZ = timezone(timedelta(hours=7))

OUTCOME_LABELS = {
    201: "✅ Đã tạo tài khoản",
    202: "⚠️ CẦN GỌI LẠI — chưa tạo được mã khách hàng",
    409: "❌ Không tạo được (số đã có tài khoản)",
    422: "❌ Không tạo được (thông tin bị từ chối)",
}

# The same labels as the <select> on index.html.
SERVICE_LABELS = {
    "transport": "Vận chuyển Trung - Việt",
    "official_import": "Nhập khẩu chính ngạch",
    "customs": "Khai báo hải quan",
    "order": "Order 1688 / Taobao / Pinduoduo",
}


def build_registration_text(info: dict, status_code: int, body: dict) -> str:
    """The message staff read. ``info`` holds only allow-listed lead fields."""
    lines = [
        "🆕 Khách đăng ký mới trên viporder.com.vn",
        OUTCOME_LABELS.get(status_code, f"Kết quả: {status_code}"),
        f"Tên: {info.get('full_name') or '-'}",
        f"SĐT: {info.get('phone') or '-'}",
    ]
    if info.get("email"):
        lines.append(f"Email: {info['email']}")
    if info.get("province"):
        lines.append(f"Tỉnh/TP: {info['province']}")
    service = info.get("service_interest")
    if service:
        lines.append(f"Quan tâm: {SERVICE_LABELS.get(service, service)}")
    code = body.get("external_customer_code") if isinstance(body, dict) else None
    if code:
        lines.append(f"Mã KH: {code}")
    lines.append(f"Lúc: {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}")
    lead_id = body.get("lead_id") if isinstance(body, dict) else None
    if lead_id:
        lines.append(f"Lead: {lead_id}")
    return "\n".join(lines)[:MAX_TEXT]


class ZaloNotifier:
    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._token = settings.zalo_bot_token
        self._chat_id = settings.zalo_notify_chat_id
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, text: str) -> bool:
        """Send one message. Returns whether Zalo accepted it; never raises."""
        if not self.enabled:
            return False
        # httpx logs every request URL at INFO, and the token is IN the URL.
        # Registering it (again, each time: the registry is a bounded LRU shared
        # with customer passwords) makes the log scrubber replace it.
        register_secret(self._token)
        url = f"{ZALO_BOT_API}/bot{self._token}/sendMessage"
        try:
            if self._client is not None:
                response = self._client.post(url, json={"chat_id": self._chat_id, "text": text})
            else:
                with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                    response = client.post(url, json={"chat_id": self._chat_id, "text": text})
        except Exception as exc:  # noqa: BLE001 - a notification must never raise
            logger.warning("zalo notification failed: %s", type(exc).__name__)
            return False
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code == 200 and isinstance(payload, dict) and payload.get("ok"):
            logger.info("zalo notification sent")
            return True
        error_code = payload.get("error_code") if isinstance(payload, dict) else None
        logger.warning(
            "zalo notification refused http_status=%s error_code=%s",
            response.status_code,
            error_code,
        )
        return False

    def notify_registration(self, info: dict, status_code: int, body: dict) -> bool:
        try:
            text = build_registration_text(info, status_code, body)
        except Exception as exc:  # noqa: BLE001
            logger.warning("zalo notification not built: %s", type(exc).__name__)
            return False
        return self.send(text)
