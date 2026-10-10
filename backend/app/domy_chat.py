"""Bridge between the public site's chat widget and DOMY, the VIP Order assistant.

DOMY (the AI Customer 360 service) already has a webchat channel:

- ``POST {upstream}/webhook/webchat`` with ``{visitor_id, message_id, content,
  display_name}``, signed ``X-Webchat-Signature: hex(HMAC-SHA256(body, secret))``,
  answered ``{conversation_id, webchat_token, ...}``;
- ``GET {upstream}/api/webchat/poll?conversation_id&token&after``, answered
  ``{messages: [{id, direction, sender_type, content, created_at}], status}``.

The browser cannot hold the signing secret, so it talks to THIS backend, which
signs and forwards. Four rules this module keeps:

- **The secret never leaves the server.** It signs; it is not sent, logged, or
  echoed. The per-conversation poll token is a credential too (it lets anyone
  read that conversation) and is registered with the log scrubber, because
  httpx logs the poll URL that carries it.
- **Limits are enforced here, per visitor and per client IP.** DOMY itself
  limits per source IP, and every website visitor reaches DOMY from this
  server's IP, so DOMY cannot tell visitors apart. A shared upstream budget
  keeps the total under DOMY's own ceiling so one busy page cannot lock
  everyone out.
- **Only what the widget needs crosses back.** Outgoing messages, their time,
  and who wrote them (assistant or staff). Nothing else from DOMY's records.
- **Failures are reported, never swallowed.** A refused or unreachable upstream
  raises :class:`ChatUnavailable`; the router turns that into a 503 the widget
  shows to the visitor.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings
from .logging_filters import register_secret

logger = logging.getLogger("viporder.domy_chat")

VN_TZ = timezone(timedelta(hours=7))

#: Shown to DOMY staff as the visitor's name, so website conversations are told
#: apart from the operations portal's own test chat (both use the webchat channel).
DISPLAY_NAME = "Khách web viporder.com.vn"
#: Prefix on the visitor id sent upstream, for the same reason.
VISITOR_PREFIX = "web-"

# Identifier shapes. Everything that reaches the upstream URL or body is checked
# against an explicit character set first; nothing is passed through unchecked.
VISITOR_RE = re.compile(r"^[A-Za-z0-9-]{16,64}$")
CONVERSATION_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,200}$")
AFTER_RE = re.compile(r"^[0-9T:. +\-Z]{0,40}$")

#: Seconds between two upstream polls of the same conversation. A faster widget
#: is answered with an empty batch rather than costing the shared budget.
MIN_POLL_INTERVAL = 2.5

SENDER_LABELS = {"ai": "ai", "agent": "staff"}


class ChatUnavailable(Exception):
    """DOMY did not answer usefully (network error, non-2xx, malformed body)."""


class ChatLimited(Exception):
    """A limit was hit. ``scope`` says which one, ``retry_after`` when to retry."""

    def __init__(self, scope: str, retry_after: int) -> None:
        super().__init__(scope)
        self.scope = scope
        self.retry_after = retry_after


class InvalidChatInput(ValueError):
    """An identifier or message failed validation; ``field`` names it."""

    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class SlidingWindow:
    """Thread-safe count of events per key inside a trailing window."""

    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float]) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def take(self, key: str) -> int:
        """Record one event; return 0, or the seconds to wait if over the limit."""
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, int(self.window - (now - hits[0])) + 1)
            hits.append(now)
            return 0


class DailyCounter:
    """Thread-safe count per key per Vietnam calendar day. Old days are dropped."""

    def __init__(self, limit: int, today: Callable[[], str]) -> None:
        self.limit = limit
        self._today = today
        self._day = ""
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> bool:
        day = self._today()
        with self._lock:
            if day != self._day:
                self._day = day
                self._counts = {}
            used = self._counts.get(key, 0)
            if used >= self.limit:
                return False
            self._counts[key] = used + 1
            return True


def _seconds_to_vn_midnight() -> int:
    now = datetime.now(VN_TZ)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


class DomyChat:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] | None = None,
    ) -> None:
        self._upstream = settings.domy_chat_upstream_url
        self._secret = settings.domy_chat_secret
        self._timeout = settings.domy_chat_timeout_seconds
        self.max_chars = settings.domy_chat_max_chars
        self._client = client
        self._clock = clock
        today = today or (lambda: datetime.now(VN_TZ).strftime("%Y-%m-%d"))
        self._per_minute = SlidingWindow(settings.domy_chat_messages_per_minute, 60, clock)
        self._visitor_day = DailyCounter(settings.domy_chat_messages_per_visitor_day, today)
        self._ip_day = DailyCounter(settings.domy_chat_messages_per_ip_day, today)
        self._upstream_budget = SlidingWindow(settings.domy_chat_upstream_per_minute, 60, clock)
        self._last_poll: dict[str, float] = {}
        self._poll_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._upstream and self._secret)

    # --- validation -------------------------------------------------------

    def clean_message(self, visitor_id: object, content: object) -> tuple[str, str]:
        if not isinstance(visitor_id, str) or not VISITOR_RE.match(visitor_id):
            raise InvalidChatInput("visitor_id")
        if not isinstance(content, str):
            raise InvalidChatInput("content")
        text = content.strip()
        if not text or len(text) > self.max_chars:
            raise InvalidChatInput("content")
        return visitor_id, text

    @staticmethod
    def clean_poll(conversation_id: object, token: object, after: object) -> tuple[str, str, str]:
        if not isinstance(conversation_id, str) or not CONVERSATION_RE.match(conversation_id):
            raise InvalidChatInput("conversation_id")
        if not isinstance(token, str) or not TOKEN_RE.match(token):
            raise InvalidChatInput("token")
        after = after if isinstance(after, str) else ""
        if not AFTER_RE.match(after):
            raise InvalidChatInput("after")
        return conversation_id, token, after

    # --- limits -----------------------------------------------------------

    def _charge_send(self, visitor_id: str, client_ip: str) -> None:
        wait = self._per_minute.take(client_ip)
        if wait:
            raise ChatLimited("minute", wait)
        if not self._visitor_day.take(visitor_id) or not self._ip_day.take(client_ip):
            raise ChatLimited("day", _seconds_to_vn_midnight())
        wait = self._upstream_budget.take("all")
        if wait:
            raise ChatLimited("busy", wait)

    # --- upstream ---------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            if self._client is not None:
                return self._client.request(method, url, **kwargs)
            with httpx.Client(timeout=self._timeout) as client:
                return client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning("domy chat upstream unreachable: %s", type(exc).__name__)
            raise ChatUnavailable(type(exc).__name__) from exc

    def send(self, visitor_id: str, content: str, client_ip: str) -> dict[str, str]:
        """Forward one visitor message. Returns ``{conversation_id, token}``."""
        self._charge_send(visitor_id, client_ip)
        body = json.dumps(
            {
                "visitor_id": VISITOR_PREFIX + visitor_id,
                "message_id": "web-" + uuid.uuid4().hex,
                "content": content,
                "display_name": DISPLAY_NAME,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        response = self._request(
            "POST",
            f"{self._upstream}/webhook/webchat",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Webchat-Signature": sign(self._secret, body),
            },
        )
        if response.status_code != 200:
            logger.warning("domy chat send refused http_status=%s", response.status_code)
            raise ChatUnavailable(f"http {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ChatUnavailable("non-JSON") from exc
        conversation_id = data.get("conversation_id") if isinstance(data, dict) else None
        token = data.get("webchat_token") if isinstance(data, dict) else None
        if not (
            isinstance(conversation_id, str)
            and CONVERSATION_RE.match(conversation_id)
            and isinstance(token, str)
            and TOKEN_RE.match(token)
        ):
            logger.warning("domy chat send: upstream body has no usable conversation/token")
            raise ChatUnavailable("bad body")
        register_secret(token)
        logger.info("domy chat message forwarded")
        return {"conversation_id": conversation_id, "token": token}

    def poll(self, conversation_id: str, token: str, after: str) -> dict[str, Any]:
        """New outgoing messages since ``after``. Throttled per conversation."""
        now = self._clock()
        with self._poll_lock:
            last = self._last_poll.get(conversation_id)
            if last is not None and now - last < MIN_POLL_INTERVAL:
                return {"messages": [], "status": None, "retry_after": 3}
            if len(self._last_poll) > 5000:
                cutoff = now - 600
                self._last_poll = {k: v for k, v in self._last_poll.items() if v > cutoff}
            self._last_poll[conversation_id] = now
        if self._upstream_budget.take("all"):
            return {"messages": [], "status": None, "retry_after": 5}
        register_secret(token)
        response = self._request(
            "GET",
            f"{self._upstream}/api/webchat/poll",
            params={"conversation_id": conversation_id, "token": token, "after": after},
        )
        if response.status_code == 403:
            raise InvalidChatInput("token")
        if response.status_code != 200:
            logger.warning("domy chat poll refused http_status=%s", response.status_code)
            raise ChatUnavailable(f"http {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ChatUnavailable("non-JSON") from exc
        if not isinstance(data, dict):
            raise ChatUnavailable("bad body")
        return {"messages": _public_messages(data.get("messages")), "status": _status(data)}


def _status(data: dict) -> str | None:
    status = data.get("status")
    return status if status in ("ai", "human", "closed") else None


def _public_messages(raw: object) -> list[dict[str, str]]:
    """Allow-list: id, text, time and author kind of OUTGOING messages only."""
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:50]:
        if not isinstance(item, dict) or item.get("direction", "out") != "out":
            continue
        content = item.get("content")
        created = item.get("created_at")
        if not isinstance(content, str) or not content.strip():
            continue
        out.append(
            {
                "id": str(item.get("id") or "")[:80],
                "content": content[:4000],
                "created_at": str(created or "")[:40],
                "from": SENDER_LABELS.get(str(item.get("sender_type") or ""), "ai"),
            }
        )
    return out
