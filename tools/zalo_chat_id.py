#!/usr/bin/env python3
"""Find the Zalo chat id for staff notifications, and send a test message.

Standard library only, so it runs on the VPS without the app's environment.

    # 1. Start this FIRST, then message the bot from the staff Zalo account
    #    while it waits (up to ~5 minutes):
    ZALO_BOT_TOKEN=... python3 tools/zalo_chat_id.py
    # 2. Put the printed id in deploy/.env as ZALO_NOTIFY_CHAT_ID, then:
    ZALO_BOT_TOKEN=... ZALO_NOTIFY_CHAT_ID=... python3 tools/zalo_chat_id.py --test

The token is read from the environment only, never from the command line
(which lands in shell history), and is never printed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://bot-api.zapps.me"
# getUpdates long-polls ~30 s and answers 408 when nothing arrived in that
# window. Measured 2026-10-10: a message sent BEFORE the call was not
# returned, so the tool waits through several windows instead of one.
POLL_SECONDS = 30
POLL_ROUNDS = 10


def call(token: str, method: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{API}/bot{token}/{method}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "viporder-notify/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:  # nosec B310 - fixed https host
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except ValueError:
            return {"ok": False, "error_code": exc.code, "description": "non-JSON error"}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"ok": False, "description": f"network error: {type(exc).__name__}"}


def _updates(result: object) -> list[dict]:
    if isinstance(result, list):
        return [u for u in result if isinstance(u, dict)]
    if isinstance(result, dict):
        return [result]
    return []


def main(argv: list[str]) -> int:
    token = os.environ.get("ZALO_BOT_TOKEN", "").strip()
    if not token:
        print("Set ZALO_BOT_TOKEN in the environment first.", file=sys.stderr)
        return 2

    if "--test" in argv:
        chat = os.environ.get("ZALO_NOTIFY_CHAT_ID", "").strip()
        if not chat:
            print("Set ZALO_NOTIFY_CHAT_ID for --test.", file=sys.stderr)
            return 2
        reply = call(
            token,
            "sendMessage",
            {"chat_id": chat, "text": "✅ VIPORDER: thông báo đăng ký đã được kết nối."},
        )
        if reply.get("ok"):
            print("Test message sent. Check Zalo.")
            return 0
        print(f"Refused: error_code={reply.get('error_code')} {reply.get('description')}")
        return 1

    print(
        f"Đang chờ tin nhắn tới bot (tối đa ~{POLL_SECONDS * POLL_ROUNDS // 60} phút). "
        "NHẮN BOT MỘT TIN BẤT KỲ NGAY BÂY GIỜ từ tài khoản Zalo nhận thông báo.",
        flush=True,
    )
    reply: dict = {}
    for round_no in range(1, POLL_ROUNDS + 1):
        reply = call(token, "getUpdates", {"timeout": POLL_SECONDS})
        if reply.get("ok") and _updates(reply.get("result")):
            break
        if reply.get("error_code") not in (None, 408):
            print(f"Refused: error_code={reply.get('error_code')} {reply.get('description')}")
            return 1
        print(f"  ... chưa có tin (lượt {round_no}/{POLL_ROUNDS})", flush=True)
    else:
        print("No messages yet. Rerun, and message the bot while this is waiting.")
        return 1
    seen: dict[str, str] = {}
    for update in _updates(reply.get("result")):
        message = update.get("message") if isinstance(update.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        chat_id = chat.get("id")
        if chat_id is not None:
            seen[str(chat_id)] = str(sender.get("display_name") or sender.get("name") or "?")
    if not seen:
        print("No messages yet. Send the bot any message from the staff Zalo, then rerun.")
        return 1
    for chat_id, name in seen.items():
        print(f"ZALO_NOTIFY_CHAT_ID={chat_id}    (from: {name})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
