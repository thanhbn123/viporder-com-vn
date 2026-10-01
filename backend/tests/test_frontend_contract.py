"""The payload shapes the public front end sends.

Provenance — read this before trusting the file
-----------------------------------------------
An earlier version of this docstring claimed these shapes were "observed on the
wire" from ``static/js/app.js``. That was false: at this branch's base commit
``static/js/app.js`` performs no ``fetch`` at all (it is 15 lines of mobile-nav
and smooth-scroll code). The claim is corrected here rather than quietly
deleted, because a test that overstates where its expectations come from is
worse than no test.

Where the shapes actually come from, as of this writing:

* the API contract issued to the front-end workstream (the controller's written
  clarification: ``email`` arrives as ``""``, ``service_interest`` is omitted
  when unset, ``202`` is not a conversion, the ``Idempotency-Key`` is a UUID
  reused across retries of one form session);
* **read from the real client source**, not assumed — ``static/js/register.js``
  on branch ``feature/g05-g06-g07-frontend-analytics-seo`` (PR #10), which:
    - ``buildPayload()`` sends ``email: ""`` with the comment "The form does not
      collect an email yet — sent empty so the payload keeps the documented
      shape";
    - omits ``service_interest`` entirely unless the customer chose one;
    - sets ``consent: true``;
    - generates one ``Idempotency-Key`` per attempt, stores it in
      ``sessionStorage``, and clears it only after a completed registration.

NOT verified here: an actual browser POST against a running server. No
end-to-end browser test was run, so "this is what Chrome sends" remains a claim
about that source file, not a measurement. The tests below prove the *backend*
accepts these shapes; they do not prove the browser produces them.

The four points this file pins down:

1. ``"email": ""`` — the public form has no email field, so the key arrives
   EMPTY. Validating it with ``EmailStr`` would 422 every single registration.
2. ``service_interest`` is ABSENT — not empty — when the customer picks nothing,
   because ``""`` is not a member of the enum.
3. Provider-unavailable keeps returning ``202`` + ``tracking_token``. The front
   end fires ``viporder_lead_success`` only on ``201`` and a separate
   ``viporder_lead_pending`` on ``202``; an unconfirmed lead must never be
   counted as a conversion.
4. ``Idempotency-Key`` is a UUID reused across retries of the same form session,
   and a repeat must return the identical stored response body.
"""

from __future__ import annotations

import uuid

from tests.conftest import DEFAULT_PASSWORD, Harness

#: What the browser actually posts: no email field, so `email` is empty; no
#: service pick, so `service_interest` is missing entirely.
FRONTEND_PAYLOAD = {
    "full_name": "Nguyễn Văn A",
    "phone": "0912345678",
    "password": DEFAULT_PASSWORD,
    "email": "",
    "province": "Bắc Ninh",
    "consent": True,
    "attribution": {
        "utm_source": "facebook",
        "utm_medium": "cpc",
        "utm_campaign": "g01",
        "utm_content": "ad1",
        "utm_term": "nhaphang",
        "landing_page": "https://viporder.com.vn/?utm_source=facebook",
        "referrer": "https://facebook.com/",
    },
}


def _frontend(**overrides: object) -> dict:
    body = {k: (dict(v) if isinstance(v, dict) else v) for k, v in FRONTEND_PAYLOAD.items()}
    body.update(overrides)
    return body


# --- 1. empty email ---------------------------------------------------------


def test_empty_email_is_accepted_not_422(harness: Harness) -> None:
    """`"email": ""` must mean "no email", never a validation failure."""
    response = harness.post_registration(_frontend())

    assert response.status_code == 201, response.text
    assert response.json()["registration_status"] == "REGISTERED"
    assert harness.lead_row(response.json()["lead_id"]).email is None


def test_whitespace_only_email_is_accepted_not_422(harness: Harness) -> None:
    response = harness.post_registration(_frontend(email="   "))

    assert response.status_code == 201, response.text
    assert harness.lead_row(response.json()["lead_id"]).email is None


def test_tab_and_newline_only_email_is_accepted(harness: Harness) -> None:
    response = harness.post_registration(_frontend(email="\t\n "))
    assert response.status_code == 201, response.text


def test_missing_email_key_is_accepted(harness: Harness) -> None:
    body = _frontend()
    del body["email"]
    response = harness.post_registration(body)

    assert response.status_code == 201, response.text
    assert harness.lead_row(response.json()["lead_id"]).email is None


def test_non_empty_bad_email_is_still_rejected(harness: Harness) -> None:
    """Normalising "" to None must not turn email validation off."""
    response = harness.post_registration(_frontend(email="not-an-email"))

    assert response.status_code == 422
    assert "email" in response.json()["error"]["fields"]


def test_non_empty_good_email_is_stored(harness: Harness) -> None:
    response = harness.post_registration(_frontend(email="khach@example.com"))

    assert response.status_code == 201
    assert harness.lead_row(response.json()["lead_id"]).email == "khach@example.com"


# --- 2. absent service_interest --------------------------------------------


def test_absent_service_interest_is_accepted(harness: Harness) -> None:
    """The key is omitted entirely when the customer picks nothing."""
    body = _frontend()
    assert "service_interest" not in body

    response = harness.post_registration(body)

    assert response.status_code == 201, response.text
    assert harness.lead_row(response.json()["lead_id"]).service_interest is None


def test_empty_service_interest_is_accepted(harness: Harness) -> None:
    response = harness.post_registration(_frontend(service_interest=""))

    assert response.status_code == 201, response.text
    assert harness.lead_row(response.json()["lead_id"]).service_interest is None


def test_null_service_interest_is_accepted(harness: Harness) -> None:
    response = harness.post_registration(_frontend(service_interest=None))
    assert response.status_code == 201, response.text


def test_a_real_service_interest_still_validates(harness: Harness) -> None:
    ok = harness.post_registration(_frontend(service_interest="customs"))
    assert ok.status_code == 201
    assert harness.lead_row(ok.json()["lead_id"]).service_interest == "customs"

    bad = harness.post_registration(
        _frontend(service_interest="not_a_real_service", phone="0912000011")
    )
    assert bad.status_code == 422


# --- the whole payload, exactly as sent ------------------------------------


def test_the_frontend_payload_round_trips_completely(harness: Harness) -> None:
    """One assertion per stored field, so a silent drop cannot hide."""
    response = harness.post_registration(_frontend())
    assert response.status_code == 201, response.text

    lead = harness.lead_row(response.json()["lead_id"])
    assert lead.full_name == "Nguyễn Văn A"
    assert lead.phone == "+84912345678"
    assert lead.phone_display == "0912 345 678"
    assert lead.email is None
    assert lead.province == "Bắc Ninh"
    assert lead.service_interest is None
    assert lead.lead_type.value == "REGISTER_LEAD"
    assert lead.registration_status.value == "REGISTERED"
    assert lead.source == "facebook"
    assert lead.medium == "cpc"
    assert lead.campaign == "g01"
    assert lead.content == "ad1"
    assert lead.term == "nhaphang"
    assert lead.landing_page == "https://viporder.com.vn/?utm_source=facebook"
    assert lead.referrer == "https://facebook.com/"


def test_frontend_payload_without_attribution(harness: Harness) -> None:
    body = _frontend()
    del body["attribution"]
    assert harness.post_registration(body).status_code == 201


def test_frontend_payload_with_empty_attribution(harness: Harness) -> None:
    """A first-party visit has no UTMs at all."""
    body = _frontend(attribution={})
    response = harness.post_registration(body)
    assert response.status_code == 201, response.text


def test_frontend_payload_with_screenshotted_phone_formatting(harness: Harness) -> None:
    response = harness.post_registration(_frontend(phone="0912 345 678"))
    assert response.status_code == 201
    assert harness.lead_row(response.json()["lead_id"]).phone == "+84912345678"


# --- 3. PENDING is 202, never 201 ------------------------------------------


def test_provider_unavailable_stays_202_and_is_not_a_conversion(make_harness) -> None:
    """The front end counts `viporder_lead_success` on 201 only.

    A kept-but-unconfirmed lead must never be reported as a conversion, so this
    must NOT be softened to 201.
    """
    harness = make_harness(mock_provider_behaviour="unavailable")
    response = harness.post_registration(_frontend())

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["registration_status"] == "PENDING"
    assert body["tracking_token"], "the front end needs a token for the pending event"
    assert body["external_customer_id"] is None
    assert body["external_customer_code"] is None

    # The lead is retained, so the pending event is not a lie either.
    rows = harness.lead_rows()
    assert len(rows) == 1
    assert rows[0].lead_id == body["lead_id"]
    assert rows[0].registration_status.value == "PENDING"


def test_success_is_201_and_not_202(harness: Harness) -> None:
    """The other half of the same contract."""
    response = harness.post_registration(_frontend())
    assert response.status_code == 201
    assert response.json()["registration_status"] == "REGISTERED"


# --- 4. UUID idempotency key reused across retries -------------------------


def test_uuid_idempotency_key_reused_returns_the_identical_body(harness: Harness) -> None:
    key = str(uuid.uuid4())
    headers = {"Idempotency-Key": key}

    first = harness.post_registration(_frontend(), headers=headers)
    second = harness.post_registration(_frontend(), headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert len(harness.lead_rows()) == 1
    assert harness.lead_row(first.json()["lead_id"]).idempotency_key == key


def test_uuid_idempotency_key_replay_after_a_202_returns_the_same_token(
    make_harness,
) -> None:
    harness = make_harness(mock_provider_behaviour="unavailable")
    key = str(uuid.uuid4())
    headers = {"Idempotency-Key": key}

    first = harness.post_registration(_frontend(), headers=headers)
    second = harness.post_registration(_frontend(), headers=headers)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert first.json()["tracking_token"] == second.json()["tracking_token"]
    assert len(harness.lead_rows()) == 1


def test_uuid_idempotency_key_is_not_treated_as_a_duplicate_phone(harness: Harness) -> None:
    """A replay is a replay, not a 409 on the customer's own phone."""
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    harness.post_registration(_frontend(), headers=headers)
    replay = harness.post_registration(_frontend(), headers=headers)
    assert replay.status_code == 201


def test_frontend_payload_with_a_relative_landing_page_is_rejected(
    harness: Harness,
) -> None:
    """`landing_page` is the one attribution field with a real rule."""
    response = harness.post_registration(
        _frontend(attribution={**FRONTEND_PAYLOAD["attribution"], "landing_page": "/"})
    )
    assert response.status_code == 422


def test_the_409_then_edit_phone_path_does_not_leak_the_first_customer(
    harness: Harness,
) -> None:
    """The reachable cross-customer leak, driven the way the browser drives it.

    ``register.js`` keeps one ``Idempotency-Key`` in ``sessionStorage`` and
    clears it only after a *completed* registration. So the sequence below is
    what a real customer does after mistyping their number:

        key K, phone A  -> 201 (an account now exists for A)
        key K, phone B  -> must NOT answer with A's lead or customer code

    The front end has separately been asked to reset the key on 409. This test
    exists so the backend does not depend on that.
    """
    key = str(uuid.uuid4())
    headers = {"Idempotency-Key": key}

    first = harness.post_registration(_frontend(phone="0912000011"), headers=headers)
    assert first.status_code == 201
    stolen = first.json()

    second = harness.post_registration(_frontend(phone="0912000012"), headers=headers)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    for secret in (stolen["lead_id"], stolen["external_customer_code"]):
        assert secret not in second.text
    assert len(harness.lead_rows()) == 1
