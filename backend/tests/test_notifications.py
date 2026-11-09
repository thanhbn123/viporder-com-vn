"""Staff Zalo notification of new registrations."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.notifications import ZaloNotifier, build_registration_text
from tests.conftest import DEFAULT_PASSWORD, payload

TOKEN = "123456:test-bot-token-value"  # nosec B105 - a test value, not a credential
CHAT = "chat-abc"


class Recorder:
    def __init__(self, status: int = 200, body: object | None = None, exc: Exception | None = None):
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = {"ok": True, "result": {}} if body is None else body
        self.exc = exc

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return httpx.Response(self.status, json=self.body)


def _arm(harness, recorder: Recorder) -> None:  # type: ignore[no-untyped-def]
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    harness.app.state.notifier = ZaloNotifier(harness.settings, client=client)


def _sent(recorder: Recorder) -> list[dict]:
    return [json.loads(r.content) for r in recorder.requests]


def test_off_by_default(harness) -> None:  # type: ignore[no-untyped-def]
    assert harness.app.state.notifier.enabled is False
    health = harness.client.get("/api/v1/health").json()
    assert health["checks"]["notifications"] == {"zalo": "off"}


def test_needs_both_token_and_chat(make_settings) -> None:  # type: ignore[no-untyped-def]
    assert not make_settings(zalo_bot_token=TOKEN).zalo_notify_enabled
    assert not make_settings(zalo_notify_chat_id=CHAT).zalo_notify_enabled
    assert make_settings(zalo_bot_token=f" {TOKEN} ", zalo_notify_chat_id=CHAT).zalo_notify_enabled


def test_success_sends_one_message_without_the_password(make_harness) -> None:  # type: ignore[no-untyped-def]
    harness = make_harness(zalo_bot_token=TOKEN, zalo_notify_chat_id=CHAT)
    recorder = Recorder()
    _arm(harness, recorder)

    response = harness.post_registration()
    assert response.status_code == 201

    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert str(request.url) == f"https://bot-api.zapps.me/bot{TOKEN}/sendMessage"
    sent = _sent(recorder)[0]
    assert sent["chat_id"] == CHAT
    text = sent["text"]
    assert "Đã tạo tài khoản" in text
    assert "0912 345 678" in text or "0912345678" in text
    assert "Bắc Ninh" in text
    assert "Vận chuyển Trung - Việt" in text
    assert DEFAULT_PASSWORD not in request.content.decode()
    assert health_on(harness)


def health_on(harness) -> bool:  # type: ignore[no-untyped-def]
    return harness.client.get("/api/v1/health").json()["checks"]["notifications"] == {"zalo": "on"}


def test_pending_is_flagged_for_a_call_back(make_harness) -> None:  # type: ignore[no-untyped-def]
    harness = make_harness(
        zalo_bot_token=TOKEN, zalo_notify_chat_id=CHAT, mock_provider_behaviour="unavailable"
    )
    recorder = Recorder()
    _arm(harness, recorder)

    assert harness.post_registration().status_code == 202
    assert "CẦN GỌI LẠI" in _sent(recorder)[0]["text"]


def test_a_replay_does_not_notify_twice(make_harness) -> None:  # type: ignore[no-untyped-def]
    harness = make_harness(zalo_bot_token=TOKEN, zalo_notify_chat_id=CHAT)
    recorder = Recorder()
    _arm(harness, recorder)

    headers = {"Idempotency-Key": "notify-replay-key-0001"}
    first = harness.post_registration(headers=headers)
    second = harness.post_registration(headers=headers)
    assert first.status_code == second.status_code == 201
    assert len(recorder.requests) == 1


def test_a_refused_duplicate_does_not_notify(make_harness) -> None:  # type: ignore[no-untyped-def]
    harness = make_harness(zalo_bot_token=TOKEN, zalo_notify_chat_id=CHAT)
    recorder = Recorder()
    _arm(harness, recorder)

    assert harness.post_registration().status_code == 201
    assert harness.post_registration(payload(email="b@example.com")).status_code == 409
    assert len(recorder.requests) == 1


@pytest.mark.parametrize(
    "recorder",
    [
        Recorder(exc=httpx.ConnectError(f"cannot reach https://bot-api.zapps.me/bot{TOKEN}")),
        Recorder(status=401, body={"ok": False, "error_code": 401, "description": "bad"}),
        Recorder(status=200, body="not json"),
    ],
    ids=["network", "refused", "garbage"],
)
def test_a_failing_zalo_never_breaks_registration_or_logs_the_token(  # type: ignore[no-untyped-def]
    make_harness, recorder, caplog
) -> None:
    harness = make_harness(zalo_bot_token=TOKEN, zalo_notify_chat_id=CHAT)
    _arm(harness, recorder)

    with caplog.at_level(logging.INFO):
        response = harness.post_registration()

    assert response.status_code == 201
    assert len(recorder.requests) == 1
    assert "zalo notification" in caplog.text
    assert "bot-api.zapps.me" in caplog.text or recorder.exc is not None
    assert TOKEN not in caplog.text


def test_text_is_built_from_the_allow_list_only() -> None:
    info = {"full_name": "Nguyễn Văn A", "phone": "0912 345 678", "password": "leak-me"}
    text = build_registration_text(info, 201, {"lead_id": "L1", "external_customer_code": None})
    assert "leak-me" not in text
    assert "Nguyễn Văn A" in text
    assert "Lead: L1" in text


# --- the customer code line (option A + D, 2026-11-09) ------------------------------


def test_a_201_with_a_code_shows_it() -> None:
    text = build_registration_text(
        {"full_name": "A", "phone": "0912 345 678"},
        201,
        {"lead_id": "L1", "external_customer_code": "TT5233"},
    )
    assert "Mã KH: TT5233" in text
    assert "chưa đọc được" not in text


def test_a_201_without_a_code_says_so_instead_of_staying_silent() -> None:
    """Until 2026-11-09 the line was omitted; 61 code-less leads went unnoticed."""
    from app.notifications import MISSING_CODE_LINE

    text = build_registration_text(
        {"full_name": "A", "phone": "0912 345 678"},
        201,
        {"lead_id": "L1", "external_customer_code": None},
    )
    assert MISSING_CODE_LINE in text
    assert "khaibao9610" in MISSING_CODE_LINE


@pytest.mark.parametrize("status", [202, 409, 422])
def test_non_201_outcomes_do_not_claim_a_missing_code(status: int) -> None:
    text = build_registration_text({"full_name": "A", "phone": "0912"}, status, {"lead_id": "L1"})
    assert "Mã KH" not in text


def test_the_code_reaches_the_staff_message_end_to_end(make_harness) -> None:  # type: ignore[no-untyped-def]
    from pathlib import Path

    from app.providers.khaibao9610 import ViporderFrontendProvider

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "live_registration_20261009.json").read_text(
            encoding="utf-8"
        )
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/register"):
            return httpx.Response(200, json=fixture["register"]["body"], request=request)
        return httpx.Response(200, json=fixture["login"]["body"], request=request)

    provider = ViporderFrontendProvider(
        mode="http",
        enable_real_calls=True,
        enable_real_registration=True,
        base_url="https://example.invalid/frontend/v1",
        user_agent="test-agent",
        client=httpx.Client(transport=httpx.MockTransport(upstream)),
    )
    harness = make_harness(zalo_bot_token=TOKEN, zalo_notify_chat_id=CHAT, provider=provider)
    recorder = Recorder()
    _arm(harness, recorder)

    assert harness.post_registration().status_code == 201
    text = _sent(recorder)[0]["text"]
    assert "Mã KH: TT5233" in text
    assert DEFAULT_PASSWORD not in text
    assert "access_token" not in text
