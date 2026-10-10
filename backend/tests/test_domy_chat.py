"""DOMY chat bridge: /api/v1/chat/* signs, limits and filters correctly.

DOMY is never contacted: every upstream call goes to an httpx.MockTransport
that records the request and answers like DOMY's webchat channel does.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from pathlib import Path

import httpx
import pytest

from app.domy_chat import DISPLAY_NAME, DomyChat

SECRET = "x" * 32  # nosec B105 - a test value, not a credential
UPSTREAM = "https://domy.test/domy-web"
VISITOR = "3f2a9c1e-7b4d-4e8a-9c21-5d6e7f809a1b"
CONV = "c_0123456789abcdef0123"
TOKEN = "tok_AbCdEfGhIjKlMnOpQrStUvWx"

ROOT = Path(__file__).resolve().parent.parent.parent


class Upstream:
    """Fake DOMY: records requests, answers send and poll."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.send_status = 200
        self.send_body: object = {"conversation_id": CONV, "webchat_token": TOKEN, "ok": True}
        self.poll_status = 200
        self.poll_body: object = {"messages": [], "status": "ai"}
        self.raise_exc: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_exc is not None:
            raise self.raise_exc
        if request.url.path.endswith("/webhook/webchat"):
            return httpx.Response(self.send_status, json=self.send_body)
        if request.url.path.endswith("/api/webchat/poll"):
            return httpx.Response(self.poll_status, json=self.poll_body)
        return httpx.Response(404, json={})


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def chat_harness(make_harness, upstream, clock):
    def _make(**overrides):
        settings = {"domy_chat_upstream_url": UPSTREAM, "domy_chat_secret": SECRET}
        settings.update(overrides)
        h = make_harness(**settings)
        h.app.state.domy_chat = DomyChat(
            h.settings,
            client=httpx.Client(transport=httpx.MockTransport(upstream)),
            clock=clock,
        )
        return h

    return _make


def _send(h, content="Giờ làm việc bên mình?", visitor=VISITOR, **headers):
    return h.client.post(
        "/api/v1/chat/messages", json={"visitor_id": visitor, "content": content}, headers=headers
    )


# --- off by default ------------------------------------------------------


def test_chat_is_off_without_url_and_secret(harness) -> None:
    r = harness.client.get("/api/v1/chat/status")
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    r = harness.client.post(
        "/api/v1/chat/messages", json={"visitor_id": VISITOR, "content": "xin chào"}
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "CHAT_DISABLED"
    assert harness.client.get("/api/v1/health").json()["checks"]["chat"] == {"domy": "off"}


def test_url_without_secret_stays_off(make_harness) -> None:
    h = make_harness(domy_chat_upstream_url=UPSTREAM)
    assert h.client.get("/api/v1/chat/status").json()["enabled"] is False


def test_health_reports_chat_on(chat_harness) -> None:
    h = chat_harness()
    assert h.client.get("/api/v1/health").json()["checks"]["chat"] == {"domy": "on"}
    assert h.client.get("/api/v1/chat/status").json() == {"enabled": True, "max_chars": 1000}


# --- send ------------------------------------------------------------------


def test_send_signs_the_exact_body_and_returns_only_conversation_and_token(
    chat_harness, upstream
) -> None:
    h = chat_harness()
    r = _send(h)
    assert r.status_code == 200
    assert r.json() == {"conversation_id": CONV, "token": TOKEN}
    assert r.headers["cache-control"] == "no-store"

    (req,) = upstream.requests
    assert str(req.url) == f"{UPSTREAM}/webhook/webchat"
    expected = hmac.new(SECRET.encode(), req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-Webchat-Signature"] == expected
    body = json.loads(req.content)
    assert body["visitor_id"] == "web-" + VISITOR
    assert body["content"] == "Giờ làm việc bên mình?"
    assert body["display_name"] == DISPLAY_NAME
    assert re.fullmatch(r"web-[0-9a-f]{32}", body["message_id"])
    # The secret never crosses to the visitor.
    assert SECRET not in r.text


def test_each_message_gets_a_fresh_message_id(chat_harness, upstream) -> None:
    h = chat_harness()
    _send(h, "một")
    _send(h, "hai")
    ids = {json.loads(r.content)["message_id"] for r in upstream.requests}
    assert len(ids) == 2


@pytest.mark.parametrize(
    "visitor,content",
    [
        ("short", "xin chào"),
        ("x" * 65, "xin chào"),
        ("có dấu không được 1234567890", "xin chào"),
        (VISITOR, ""),
        (VISITOR, "   "),
        (VISITOR, 123),
    ],
)
def test_invalid_input_is_refused_before_any_upstream_call(
    chat_harness, upstream, visitor, content
) -> None:
    h = chat_harness()
    r = h.client.post("/api/v1/chat/messages", json={"visitor_id": visitor, "content": content})
    assert r.status_code == 400
    assert upstream.requests == []


def test_too_long_message_says_so(chat_harness, upstream) -> None:
    h = chat_harness(domy_chat_max_chars=50)
    r = _send(h, "a" * 51)
    assert r.status_code == 400
    assert "dài quá" in r.json()["error"]["message"]
    assert upstream.requests == []


def test_non_json_body_is_400(chat_harness) -> None:
    h = chat_harness()
    r = h.client.post(
        "/api/v1/chat/messages", content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400


# --- limits ----------------------------------------------------------------


def test_per_minute_limit_answers_429_with_retry_after(chat_harness, upstream) -> None:
    h = chat_harness(domy_chat_messages_per_minute=2)
    assert _send(h).status_code == 200
    assert _send(h).status_code == 200
    r = _send(h)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    assert len(upstream.requests) == 2


def test_minute_limit_recovers_after_the_window(chat_harness, clock) -> None:
    h = chat_harness(domy_chat_messages_per_minute=1)
    assert _send(h).status_code == 200
    assert _send(h).status_code == 429
    clock.t += 61
    assert _send(h).status_code == 200


def test_daily_limit_per_visitor(chat_harness, clock) -> None:
    h = chat_harness(domy_chat_messages_per_visitor_day=2)
    for _ in range(2):
        assert _send(h).status_code == 200
        clock.t += 61
    r = _send(h)
    assert r.status_code == 429
    assert "hotline" in r.json()["error"]["message"]
    other = "9d1c2b3a-4e5f-4a6b-8c7d-0e1f2a3b4c5d"
    assert _send(h, visitor=other).status_code == 200


def test_daily_limit_per_ip_survives_a_new_visitor_id(chat_harness, clock) -> None:
    h = chat_harness(domy_chat_messages_per_ip_day=2, trust_proxy_headers=True)
    visitors = [f"{i:08d}-aaaa-bbbb-cccc-0123456789ab" for i in range(3)]
    ip = {"X-Forwarded-For": "203.0.113.7"}
    assert _send(h, visitor=visitors[0], **ip).status_code == 200
    assert _send(h, visitor=visitors[1], **ip).status_code == 200
    assert _send(h, visitor=visitors[2], **ip).status_code == 429
    assert _send(h, visitor=visitors[2], **{"X-Forwarded-For": "198.51.100.9"}).status_code == 200


def test_shared_upstream_budget(chat_harness) -> None:
    h = chat_harness(domy_chat_upstream_per_minute=1, trust_proxy_headers=True)
    assert _send(h, **{"X-Forwarded-For": "203.0.113.1"}).status_code == 200
    r = _send(h, **{"X-Forwarded-For": "203.0.113.2"})
    assert r.status_code == 429
    assert "đông khách" in r.json()["error"]["message"]


# --- upstream failures --------------------------------------------------------


@pytest.mark.parametrize("status", [401, 429, 500])
def test_upstream_refusal_is_503(chat_harness, upstream, status) -> None:
    upstream.send_status = status
    r = _send(chat_harness())
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "CHAT_UNAVAILABLE"


def test_upstream_unreachable_is_503(chat_harness, upstream) -> None:
    upstream.raise_exc = httpx.ConnectError("boom")
    assert _send(chat_harness()).status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {"conversation_id": CONV},
        {"webchat_token": TOKEN},
        {"conversation_id": "bad id with spaces", "webchat_token": TOKEN},
        ["not", "a", "dict"],
    ],
)
def test_upstream_body_without_usable_ids_is_503(chat_harness, upstream, body) -> None:
    upstream.send_body = body
    assert _send(chat_harness()).status_code == 503


# --- poll ----------------------------------------------------------------------


def _poll(h, after=""):
    return h.client.get(
        "/api/v1/chat/poll", params={"conversation_id": CONV, "token": TOKEN, "after": after}
    )


def test_poll_forwards_and_keeps_only_public_fields(chat_harness, upstream) -> None:
    upstream.poll_body = {
        "status": "human",
        "messages": [
            {
                "id": "m_1",
                "direction": "out",
                "sender_type": "ai",
                "content": "Dạ em chào anh/chị",
                "created_at": "2026-10-10T23:01:02.123+07:00",
                "customer_phone": "0900000000",
            },
            {
                "id": "m_2",
                "direction": "out",
                "sender_type": "agent",
                "content": "Em là NV",
                "created_at": "2026-10-10T23:02:00+07:00",
            },
            {
                "id": "m_3",
                "direction": "in",
                "sender_type": "customer",
                "content": "lộ ra",
                "created_at": "2026-10-10T23:03:00+07:00",
            },
            {"id": "m_4", "direction": "out", "sender_type": "ai", "content": "  "},
        ],
    }
    h = chat_harness()
    r = _poll(h, after="2026-10-10T23:00:00+07:00")
    assert r.status_code == 200
    assert r.json() == {
        "status": "human",
        "messages": [
            {
                "id": "m_1",
                "content": "Dạ em chào anh/chị",
                "created_at": "2026-10-10T23:01:02.123+07:00",
                "from": "ai",
            },
            {
                "id": "m_2",
                "content": "Em là NV",
                "created_at": "2026-10-10T23:02:00+07:00",
                "from": "staff",
            },
        ],
    }
    (req,) = upstream.requests
    assert req.url.path == "/domy-web/api/webchat/poll"
    assert req.url.params["conversation_id"] == CONV
    assert req.url.params["token"] == TOKEN
    assert req.url.params["after"] == "2026-10-10T23:00:00+07:00"
    assert "0900000000" not in r.text


def test_poll_faster_than_the_interval_does_not_reach_upstream(
    chat_harness, upstream, clock
) -> None:
    h = chat_harness()
    assert _poll(h).status_code == 200
    clock.t += 1
    r = _poll(h)
    assert r.status_code == 200
    assert r.json()["messages"] == []
    assert r.json()["retry_after"] >= 1
    assert len(upstream.requests) == 1
    clock.t += 3
    _poll(h)
    assert len(upstream.requests) == 2


def test_poll_with_a_rejected_token_is_403(chat_harness, upstream) -> None:
    upstream.poll_status = 403
    r = _poll(chat_harness())
    assert r.status_code == 403
    assert "hết hạn" in r.json()["error"]["message"]


@pytest.mark.parametrize(
    "params",
    [
        {"conversation_id": "../etc", "token": TOKEN},
        {"conversation_id": CONV, "token": "short"},
        {"conversation_id": CONV, "token": TOKEN, "after": "1; drop table"},
        {"token": TOKEN},
    ],
)
def test_poll_validates_before_calling_upstream(chat_harness, upstream, params) -> None:
    r = chat_harness().client.get("/api/v1/chat/poll", params=params)
    assert r.status_code in (400, 403)
    assert upstream.requests == []


def test_poll_upstream_down_is_503(chat_harness, upstream) -> None:
    upstream.poll_status = 502
    assert _poll(chat_harness()).status_code == 503


# --- secrets stay out of logs and out of the repository ----------------------------


def test_neither_secret_nor_poll_token_is_logged(chat_harness, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    h = chat_harness()
    _send(h)
    _poll(h)
    logged = caplog.text
    # Guard the guard: httpx must have logged the poll URL, or the two
    # assertions below would pass on an empty capture.
    assert "webchat/poll" in logged
    assert SECRET not in logged
    assert TOKEN not in logged


def test_compose_passes_every_chat_setting() -> None:
    from app.config import Settings

    wanted = {n.upper() for n in Settings.model_fields if n.startswith("domy_chat_")}
    text = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    passed = set(re.findall(r"(DOMY_CHAT_[A-Z0-9_]+)\s*:", text))
    assert wanted and not (wanted - passed), f"compose does not pass {sorted(wanted - passed)}"
    assert "DOMY_CHAT_SECRET: ${DOMY_CHAT_SECRET:-}" in text
