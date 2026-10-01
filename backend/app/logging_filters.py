"""Logging with password redaction.

Hard constraint: a registration password must never be logged or traced. Relying
on "we remember not to log it" is not a control.

Redaction happens at the **record**, in the log-record factory installed by
:func:`install_record_factory`, so it is applied once, before any handler runs.
That ordering is the whole design: a handler the application does not own — a
test capture handler, an APM agent, a log shipper, a plain
``logging.basicConfig()`` stream installed by a library before this app was even
constructed — can format the record however it likes and still cannot see a
password.

What :func:`scrub_record` rewrites:

* ``record.msg`` and ``record.args`` (rendered once and frozen when the message
  carries ``%``-arguments, so rewriting a template cannot break formatting);
* ``record.exc_info`` — the traceback is rendered and scrubbed into
  ``record.exc_text`` and ``exc_info`` is cleared, because a traceback is a
  *first-class* leak channel: an exception raised inside the provider carries the
  request in its message. A handler-level formatter would never get the chance
  to fix this, since any handler may format the record itself;
* ``record.exc_text`` and ``record.stack_info``, if they were set directly.

:class:`RedactingFormatter` is a second, independent layer over the final
rendered line. It is belt-and-braces, not the primary control: it only protects
records that pass through a handler we configured.

Literal secrets can also be registered at runtime with :func:`register_secret`,
which is what the registration path does with the incoming password so that even
an unrelated log line containing it is scrubbed.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict

REDACTED = "<redacted>"

#: How many recently-seen secrets stay scrubbable. A password is kept for a
#: short while *after* the request too, so a late error handler or a background
#: log still cannot print it. The cache is bounded so a long-running process
#: cannot accumulate customer passwords without limit.
MAX_REMEMBERED_SECRETS = 64

#: Keys whose *value* is a secret no matter what it looks like.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "pass",
        "confirmpassword",
        "confirm_password",
        "new_password",
        "old_password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
    }
)

#: ``password=...`` / ``"password": "..."`` left inside a rendered string.
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)([\"']?\b(?:password|passwd|pwd|confirmPassword|confirm_password)\b[\"']?"
    r"\s*[:=]\s*)([\"']?)([^\s,;}\)\"']+)(\2)"
)

_lock = threading.Lock()
_literal_secrets: OrderedDict[str, None] = OrderedDict()

# Never scrub these even if they were somehow registered: they are far too
# short to be a real secret and replacing them would mangle every log line.
_MIN_SECRET_LENGTH = 6


def register_secret(value: str | None) -> None:
    """Remember a literal secret so every log line containing it is scrubbed.

    Bounded to the most recent :data:`MAX_REMEMBERED_SECRETS` values.
    """
    if not value or not isinstance(value, str):
        return
    if len(value) < _MIN_SECRET_LENGTH:
        return
    with _lock:
        _literal_secrets[value] = None
        _literal_secrets.move_to_end(value)
        while len(_literal_secrets) > MAX_REMEMBERED_SECRETS:
            _literal_secrets.popitem(last=False)


def forget_secret(value: str | None) -> None:
    """Stop scrubbing a literal secret (used to bound memory explicitly)."""
    if not value:
        return
    with _lock:
        _literal_secrets.pop(value, None)


def clear_secrets() -> None:
    with _lock:
        _literal_secrets.clear()


def _current_secrets() -> tuple[str, ...]:
    with _lock:
        # Longest first: a secret that contains another must not be partially
        # replaced and leave a recognisable fragment behind.
        return tuple(sorted(_literal_secrets, key=len, reverse=True))


def scrub_text(text: str) -> str:
    """Remove known secrets and ``password=...`` fragments from a string."""
    if not text:
        return text
    result = text
    for secret in _current_secrets():
        if secret in result:
            result = result.replace(secret, REDACTED)
    result = _KEY_VALUE_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}{m.group(4)}", result
    )
    return result


def scrub_value(value: object, *, key: str | None = None) -> object:
    """Scrub a single value; values under a sensitive key are replaced outright."""
    if key is not None and key.replace("-", "_").lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {k: scrub_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        scrubbed = [scrub_value(v) for v in value]
        return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
    return value


class PasswordRedactionFilter(logging.Filter):
    """Scrubs log records before any handler formats them.

    Records that carry ``%``-arguments are rendered once, scrubbed, and then
    frozen (``args`` set to ``()``). That matters: rewriting the template
    ``"password=%s"`` to ``"password=<redacted>"`` while leaving the argument in
    place makes ``%``-formatting raise, and a logging error handler is a
    surprisingly good way to leak the very value you were hiding.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        scrub_record(record)
        return True


#: Used only to render a traceback so it can be scrubbed. A shared instance is
#: fine: ``formatException`` is stateless.
_TRACEBACK_FORMATTER = logging.Formatter()


def _render_exception(exc_info: object) -> str:
    try:
        return _TRACEBACK_FORMATTER.formatException(exc_info)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - never break logging over a bad exc_info
        try:
            return repr(exc_info)
        except Exception:  # noqa: BLE001
            return "<unrenderable exception>"


def scrub_record(record: logging.LogRecord) -> None:
    """Redact a record in place. Idempotent.

    Tracebacks are scrubbed here rather than in a formatter, because a traceback
    is a leak channel in its own right: an exception raised while handling a
    registration carries the request (including the plaintext password) in its
    message. Only handlers that use *our* formatter would be protected by
    formatter-level scrubbing, and any handler may format the record itself.
    """
    if record.exc_info:
        record.exc_text = scrub_text(_render_exception(record.exc_info))
        record.exc_info = None
        record.exc_info_scrubbed = True  # type: ignore[attr-defined]
    elif record.exc_text:
        record.exc_text = scrub_text(record.exc_text)

    if record.stack_info:
        record.stack_info = scrub_text(record.stack_info)

    if isinstance(record.msg, dict):
        record.msg = scrub_value(record.msg, key=None)

    if record.args:
        if isinstance(record.args, dict):
            record.args = {k: scrub_value(v, key=str(k)) for k, v in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(scrub_value(a) for a in record.args)
        else:
            record.args = scrub_value(record.args, key=None)  # type: ignore[assignment]

        # Render once, scrub, freeze.
        try:
            rendered = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging over a bad format
            rendered = str(record.msg)
        record.msg = scrub_text(rendered)
        record.args = ()
        return

    if isinstance(record.msg, str):
        record.msg = scrub_text(record.msg)


_RECORD_FACTORY_MARKER = "_viporder_scrubbing_record_factory"


def install_record_factory() -> None:
    """Scrub every record at creation time.

    A handler-level filter only runs when that handler runs, so a handler that
    the application does not own (a test capture handler, an APM agent, a
    third-party log shipper) could format the record first. Scrubbing in the
    record factory removes that ordering dependency entirely: no handler ever
    sees an unscrubbed password. Idempotent.
    """
    current = logging.getLogRecordFactory()
    if getattr(current, _RECORD_FACTORY_MARKER, False):
        return

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = current(*args, **kwargs)  # type: ignore[arg-type]
        scrub_record(record)
        return record

    setattr(factory, _RECORD_FACTORY_MARKER, True)
    logging.setLogRecordFactory(factory)


class RedactingFormatter(logging.Formatter):
    """Formatter that scrubs the rendered line, including tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return scrub_text(super().format(record))


def configure_logging(level: int = logging.INFO) -> None:
    """Install the redaction layers on the logging subsystem (idempotent)."""
    install_record_factory()

    root = logging.getLogger()
    root.setLevel(level)

    # Records logged directly on the root logger skip handler ordering
    # concerns entirely when the filter sits on the logger too.
    if not any(isinstance(f, PasswordRedactionFilter) for f in root.filters):
        root.addFilter(PasswordRedactionFilter())

    # Protect handlers owned by other libraries without hijacking their
    # formatter: a test capture handler or an APM agent keeps its own format.
    for handler in root.handlers:
        if not any(isinstance(f, PasswordRedactionFilter) for f in handler.filters):
            handler.addFilter(PasswordRedactionFilter())

    if not any(getattr(h, "_viporder_redacting", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler.addFilter(PasswordRedactionFilter())
        handler._viporder_redacting = True  # type: ignore[attr-defined]
        root.addHandler(handler)
