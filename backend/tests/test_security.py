"""Security surface: headers, size limit, rate limit, admin gating, CORS."""

from __future__ import annotations

import pytest

from tests.conftest import Harness, payload

REQUIRED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": None,
    "content-security-policy": None,
}


def _assert_security_headers(response) -> None:
    headers = {k.lower(): v for k, v in response.headers.items()}
    for name, expected in REQUIRED_HEADERS.items():
        assert name in headers, f"missing {name}"
        if expected is not None:
            assert headers[name] == expected
    assert "default-src 'self'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


# --- headers ----------------------------------------------------------------


def test_headers_on_success(harness: Harness) -> None:
    _assert_security_headers(harness.post_registration())


def test_headers_on_validation_error(harness: Harness) -> None:
    _assert_security_headers(harness.post_registration(payload(consent=False)))


def test_headers_on_not_found(harness: Harness) -> None:
    _assert_security_headers(harness.client.get("/api/v1/nope"))


def test_headers_on_payload_too_large(harness: Harness) -> None:
    _assert_security_headers(harness.post_registration(payload(full_name="x" * 100_000)))


def test_headers_on_rate_limited(make_harness) -> None:
    harness = make_harness(rate_limit_enabled=True, rate_limit_attempts=1)
    harness.post_registration(payload(phone="0912000011"))
    _assert_security_headers(harness.post_registration(payload(phone="0912000012")))


def test_hsts_only_over_https(make_harness) -> None:
    harness = make_harness(trust_proxy_headers=True)

    plain = harness.client.get("/api/v1/health")
    assert "strict-transport-security" not in {k.lower() for k in plain.headers}

    secure = harness.client.get("/api/v1/health", headers={"X-Forwarded-Proto": "https"})
    assert secure.headers["strict-transport-security"].startswith("max-age=")


def test_hsts_is_not_trusted_from_a_forwarded_header_by_default(make_harness) -> None:
    harness = make_harness(trust_proxy_headers=False)
    response = harness.client.get("/api/v1/health", headers={"X-Forwarded-Proto": "https"})
    assert "strict-transport-security" not in {k.lower() for k in response.headers}


def test_request_id_is_echoed(harness: Harness) -> None:
    response = harness.client.get("/api/v1/health", headers={"X-Request-Id": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"


def test_request_id_is_generated_when_absent(harness: Harness) -> None:
    response = harness.client.get("/api/v1/health")
    assert len(response.headers["x-request-id"]) == 32


def test_unsafe_request_id_is_not_echoed(harness: Harness) -> None:
    """An unvalidated correlation id echoed into a log line is log injection."""
    hostile = "<script>alert(1)</script>"
    response = harness.client.get("/api/v1/health", headers={"X-Request-Id": hostile})
    echoed = response.headers["x-request-id"]
    assert echoed != hostile
    assert len(echoed) == 32


# --- request size -----------------------------------------------------------


def test_body_under_the_limit_is_accepted(make_harness) -> None:
    harness = make_harness(max_request_bytes=64 * 1024)
    assert harness.post_registration().status_code == 201


def test_body_over_a_small_limit_is_413(make_harness) -> None:
    harness = make_harness(max_request_bytes=2048)
    response = harness.client.post(
        "/api/v1/registrations",
        content=b"x" * 4096,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_oversized_body_creates_no_lead(harness: Harness) -> None:
    harness.post_registration(payload(full_name="x" * 100_000))
    assert harness.lead_rows() == []


# --- rate limit -------------------------------------------------------------


def test_rate_limit_returns_429_with_retry_after(make_harness) -> None:
    harness = make_harness(
        rate_limit_enabled=True, rate_limit_attempts=3, rate_limit_window_seconds=600
    )

    for phone in ("0912000011", "0912000012", "0912000013"):
        assert harness.post_registration(payload(phone=phone)).status_code == 201

    blocked = harness.post_registration(payload(phone="0912000014"))
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert int(blocked.headers["Retry-After"]) >= 1
    # The blocked attempt must not have created a lead.
    assert len(harness.lead_rows()) == 3


def test_rate_limit_applies_before_validation(make_harness) -> None:
    """An abusive caller cannot use invalid bodies to stay under the limit."""
    harness = make_harness(rate_limit_enabled=True, rate_limit_attempts=1)
    assert harness.post_registration(payload(consent=False)).status_code == 422
    assert harness.post_registration(payload(consent=False)).status_code == 429


def test_rate_limit_can_be_disabled(make_harness) -> None:
    harness = make_harness(rate_limit_enabled=False, rate_limit_attempts=1)
    for phone in ("0912000011", "0912000012", "0912000013"):
        assert harness.post_registration(payload(phone=phone)).status_code == 201


def test_rate_limit_is_per_client_ip(make_harness) -> None:
    harness = make_harness(
        rate_limit_enabled=True,
        rate_limit_attempts=1,
        trust_proxy_headers=True,
    )
    first = harness.post_registration(
        payload(phone="0912000011"), headers={"X-Forwarded-For": "203.0.113.9"}
    )
    second = harness.post_registration(
        payload(phone="0912000012"), headers={"X-Forwarded-For": "198.51.100.4"}
    )
    assert (first.status_code, second.status_code) == (201, 201)


def test_forwarded_for_is_ignored_unless_proxy_headers_are_trusted(make_harness) -> None:
    """With TRUST_PROXY_HEADERS=no a caller cannot forge their way past the limit."""
    harness = make_harness(
        rate_limit_enabled=True,
        rate_limit_attempts=1,
        trust_proxy_headers=False,
    )
    harness.post_registration(
        payload(phone="0912000011"), headers={"X-Forwarded-For": "203.0.113.9"}
    )
    spoofed = harness.post_registration(
        payload(phone="0912000012"), headers={"X-Forwarded-For": "198.51.100.4"}
    )
    assert spoofed.status_code == 429


# --- admin ------------------------------------------------------------------


def test_admin_route_is_404_when_the_token_env_is_unset(harness: Harness) -> None:
    body = harness.post_registration().json()
    response = harness.client.post(
        f"/api/v1/admin/registrations/{body['lead_id']}/retry",
        headers={"X-Admin-Token": "anything"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_admin_route_is_404_with_a_wrong_token(make_harness) -> None:
    harness = make_harness(admin_api_token="correct-horse")
    body = harness.post_registration().json()
    response = harness.client.post(
        f"/api/v1/admin/registrations/{body['lead_id']}/retry",
        headers={"X-Admin-Token": "wrong-horse"},
    )
    assert response.status_code == 404


def test_admin_route_is_404_without_a_token(make_harness) -> None:
    harness = make_harness(admin_api_token="correct-horse")
    body = harness.post_registration().json()
    response = harness.client.post(f"/api/v1/admin/registrations/{body['lead_id']}/retry")
    assert response.status_code == 404


def test_admin_retry_is_refused_while_a_customer_attempt_holds_the_phone(
    make_harness,
) -> None:
    """The operator door must respect the same phone claim as the customer door.

    The retry route re-attempts an EXISTING lead, so it never went through
    `create` — which is where the claim is taken. That left the race the claim
    exists to close open on the one path a human drives by hand: an admin retry
    could reach the provider while a customer attempt for the same phone was in
    flight. Refusing with `retried: false` is the truthful answer; a second
    provider call would risk a duplicate customer.
    """
    from datetime import timedelta

    from app.models import RegistrationStatus, utcnow

    # Provider down, so the lead is left PENDING — which is the only state a
    # retry is for. With the default (success) the registration completes and
    # there is nothing to retry.
    harness = make_harness(admin_api_token="correct-horse", mock_provider_behaviour="unavailable")
    body = harness.post_registration().json()
    lead_id = body["lead_id"]

    # Stand in for an attempt that is genuinely in flight: hold the claim.
    with harness.database.session() as session:
        from app.models import Lead

        lead = session.get(Lead, lead_id)
        assert lead is not None
        assert lead.registration_status is RegistrationStatus.PENDING
        lead.in_flight_at = utcnow() + timedelta(seconds=60)  # comfortably live
        session.commit()

    response = harness.client.post(
        f"/api/v1/admin/registrations/{lead_id}/retry",
        headers={"X-Admin-Token": "correct-horse"},
        json={"password": "admin-retry-secret"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["retried"] is False, (
        f"the retry was allowed through while the phone was claimed: {payload}"
    )
    assert "in flight" in payload["message"].lower(), payload["message"]


def test_admin_retry_completes_a_pending_lead(make_harness) -> None:
    """Provider down, then up: the lead must be recoverable, not stranded."""
    from app.providers.base import ProviderStatus, RegistrationResult

    class FlakyProvider:
        name = "flaky"

        def __init__(self) -> None:
            self.calls = 0

        def register(self, request):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return RegistrationResult(
                    status=ProviderStatus.UNAVAILABLE,
                    message="down",
                    http_status=503,
                    retryable=True,
                )
            return RegistrationResult(
                status=ProviderStatus.SUCCESS,
                external_customer_id="ext-9",
                external_customer_code="TT00099",
                message="ok",
                http_status=201,
            )

    harness = make_harness(admin_api_token="top-secret-admin", provider=FlakyProvider())
    pending = harness.post_registration()
    assert pending.status_code == 202
    lead_id = pending.json()["lead_id"]

    response = harness.client.post(
        f"/api/v1/admin/registrations/{lead_id}/retry",
        headers={"X-Admin-Token": "top-secret-admin"},
        json={"password": "secret-at-least-8"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["registration_status"] == "REGISTERED"
    assert body["external_customer_code"] == "TT00099"
    assert body["retried"] is True
    assert body["attempt_count"] == 2

    lead = harness.lead_row(lead_id)
    assert lead.registration_status.value == "REGISTERED"
    assert lead.external_customer_code == "TT00099"


def test_admin_retry_without_a_password_explains_itself(make_harness) -> None:
    harness = make_harness(
        admin_api_token="top-secret-admin", mock_provider_behaviour="unavailable"
    )
    lead_id = harness.post_registration().json()["lead_id"]

    response = harness.client.post(
        f"/api/v1/admin/registrations/{lead_id}/retry",
        headers={"X-Admin-Token": "top-secret-admin"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["retried"] is False
    assert body["registration_status"] == "PENDING"
    assert "password" in body["message"].lower()
    # Nothing changed on disk, so the lead is still recoverable later.
    assert harness.lead_row(lead_id).registration_status.value == "PENDING"


def test_admin_retry_never_returns_the_password(make_harness) -> None:
    harness = make_harness(
        admin_api_token="top-secret-admin", mock_provider_behaviour="unavailable"
    )
    lead_id = harness.post_registration().json()["lead_id"]
    secret = "extremely-secret-password"

    response = harness.client.post(
        f"/api/v1/admin/registrations/{lead_id}/retry",
        headers={"X-Admin-Token": "top-secret-admin"},
        json={"password": secret},
    )
    assert secret not in response.text


def test_admin_retry_on_an_unknown_lead_is_404(make_harness) -> None:
    harness = make_harness(admin_api_token="top-secret-admin")
    response = harness.client.post(
        "/api/v1/admin/registrations/does-not-exist/retry",
        headers={"X-Admin-Token": "top-secret-admin"},
    )
    assert response.status_code == 404


def test_admin_token_is_not_leaked_in_errors(make_harness) -> None:
    harness = make_harness(admin_api_token="top-secret-admin")
    body = harness.post_registration().json()
    response = harness.client.post(
        f"/api/v1/admin/registrations/{body['lead_id']}/retry",
        headers={"X-Admin-Token": "guess"},
    )
    assert "top-secret-admin" not in response.text


# --- CORS -------------------------------------------------------------------


def test_cors_is_off_by_default(harness: Harness) -> None:
    response = harness.client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_cors_is_enabled_only_for_configured_origins(make_harness) -> None:
    harness = make_harness(cors_allow_origins="https://viporder.com.vn")
    response = harness.client.get("/api/v1/health", headers={"Origin": "https://viporder.com.vn"})
    assert response.headers["access-control-allow-origin"] == "https://viporder.com.vn"


def test_cors_wildcard_never_sends_credentials(make_harness) -> None:
    harness = make_harness(cors_allow_origins="*")
    response = harness.client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert response.headers.get("access-control-allow-origin") == "*"
    assert "access-control-allow-credentials" not in {k.lower() for k in response.headers}


# --- open redirect / generic 500 -------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example",
        "//evil.example",
        "https://khachhang.viporder.com.vn.evil.example",
    ],
)
def test_login_url_is_a_server_side_constant(harness: Harness, hostile: str) -> None:
    response = harness.post_registration(
        payload(attribution={**payload()["attribution"], "referrer": hostile})
    )
    assert response.json()["login_url"] == "https://khachhang.viporder.com.vn"


def test_internal_error_is_a_plain_500_without_a_traceback(make_harness) -> None:
    harness = make_harness()

    def explode() -> None:
        raise RuntimeError("secret internal detail /srv/app/db.py line 42")

    harness.app.add_api_route("/api/v1/_boom", explode, methods=["GET"])

    response = harness.client.get("/api/v1/_boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "Traceback" not in response.text
    assert "secret internal detail" not in response.text
    assert "/srv/app/db.py" not in response.text


# --- admin follow-up list ------------------------------------------------------

FOLLOW_UP = "/api/v1/admin/registrations/follow-up"


def test_follow_up_list_is_404_when_the_token_env_is_unset(harness: Harness) -> None:
    response = harness.client.get(FOLLOW_UP, headers={"X-Admin-Token": "anything"})
    assert response.status_code == 404


def test_follow_up_list_is_404_with_a_wrong_or_missing_token(make_harness) -> None:
    harness = make_harness(admin_api_token="correct-horse")
    assert harness.client.get(FOLLOW_UP, headers={"X-Admin-Token": "wrong"}).status_code == 404
    assert harness.client.get(FOLLOW_UP).status_code == 404


def test_follow_up_list_returns_only_the_contact_fields_of_pending_leads(make_harness) -> None:
    """Staff are promised to call PENDING customers back; this is how they find them.
    It must publish contact details and NOTHING that authorises anything."""
    harness = make_harness(admin_api_token="correct-horse", mock_provider_behaviour="unavailable")
    pending = harness.post_registration().json()
    assert pending["registration_status"] == "PENDING"

    response = harness.client.get(FOLLOW_UP, headers={"X-Admin-Token": "correct-horse"})

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["count"] == 1
    row = body["leads"][0]
    assert row["lead_id"] == pending["lead_id"]
    assert row["registration_status"] == "PENDING"
    assert set(row) == {
        "lead_id",
        "registration_status",
        "full_name",
        "phone_display",
        "email",
        "province",
        "service_interest",
        "attempt_count",
        "last_error_code",
        "created_at",
    }
    assert pending["tracking_token"] not in response.text
    assert "password" not in response.text.lower()


def test_follow_up_list_omits_completed_registrations(make_harness) -> None:
    harness = make_harness(admin_api_token="correct-horse")
    assert harness.post_registration().json()["registration_status"] == "REGISTERED"

    body = harness.client.get(FOLLOW_UP, headers={"X-Admin-Token": "correct-horse"}).json()

    assert body == {"count": 0, "leads": []}


def test_follow_up_list_bounds_its_limit(make_harness) -> None:
    harness = make_harness(admin_api_token="correct-horse")
    headers = {"X-Admin-Token": "correct-horse"}
    assert harness.client.get(FOLLOW_UP + "?limit=0", headers=headers).status_code == 422
    assert harness.client.get(FOLLOW_UP + "?limit=201", headers=headers).status_code == 422
