"""Public chat routes: the site's DOMY widget talks only to these.

::

    GET  /api/v1/chat/status    -> {"enabled": bool, "max_chars": int}
    POST /api/v1/chat/messages  {"visitor_id", "content"}
                                -> 200 {"conversation_id", "token"}
    GET  /api/v1/chat/poll?conversation_id&token&after
                                -> 200 {"messages": [...], "status": ..., "retry_after"?}

Errors use the common envelope. Messages are Vietnamese, because the visitor
reads them inside the widget.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..domy_chat import ChatLimited, ChatUnavailable, DomyChat, InvalidChatInput
from ..errors import INVALID_REQUEST, RATE_LIMITED, ApiError
from ..middleware import client_ip

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

CHAT_DISABLED = "CHAT_DISABLED"
CHAT_UNAVAILABLE = "CHAT_UNAVAILABLE"

MESSAGE_DISABLED = "Trợ lý DOMY chưa được bật trên trang này."
MESSAGE_UNAVAILABLE = (
    "Trợ lý DOMY tạm thời không phản hồi. Anh/chị thử lại sau ít phút "
    "hoặc gọi hotline để được hỗ trợ ngay."
)
MESSAGE_INVALID = "Tin nhắn không hợp lệ. Anh/chị kiểm tra lại giúp em."
MESSAGE_TOO_LONG = "Tin nhắn dài quá, anh/chị viết ngắn lại giúp em."
MESSAGE_SESSION = 'Phiên trò chuyện đã hết hạn. Anh/chị bấm "Mới" để bắt đầu lại.'
LIMIT_MESSAGES = {
    "minute": "Anh/chị gửi hơi nhanh, chờ em vài giây rồi gửi tiếp nhé.",
    "day": "Hôm nay anh/chị đã nhắn khá nhiều. Cần gấp, anh/chị gọi hotline giúp em.",
    "busy": "Trợ lý đang đông khách, anh/chị thử lại sau ít giây nhé.",
}

NO_STORE = {"Cache-Control": "no-store"}


def _chat(request: Request) -> DomyChat:
    chat: DomyChat = request.app.state.domy_chat
    if not chat.enabled:
        raise ApiError(503, CHAT_DISABLED, MESSAGE_DISABLED)
    return chat


@router.get("/status")
def status(request: Request) -> JSONResponse:
    chat: DomyChat = request.app.state.domy_chat
    return JSONResponse({"enabled": chat.enabled, "max_chars": chat.max_chars}, headers=NO_STORE)


@router.post("/messages")
async def send_message(request: Request) -> JSONResponse:
    chat = _chat(request)
    try:
        body = await request.json()
    except ValueError:
        raise ApiError(400, INVALID_REQUEST, MESSAGE_INVALID) from None
    if not isinstance(body, dict):
        raise ApiError(400, INVALID_REQUEST, MESSAGE_INVALID)
    try:
        visitor_id, content = chat.clean_message(body.get("visitor_id"), body.get("content"))
    except InvalidChatInput as exc:
        too_long = (
            exc.field == "content"
            and isinstance(body.get("content"), str)
            and len(body["content"].strip()) > chat.max_chars
        )
        raise ApiError(
            400,
            INVALID_REQUEST,
            MESSAGE_TOO_LONG if too_long else MESSAGE_INVALID,
            fields={exc.field: "invalid"},
        ) from None
    settings = request.app.state.settings
    ip = client_ip(request.scope, trust_proxy_headers=settings.trust_proxy_headers)
    try:
        result = chat.send(visitor_id, content, ip)
    except ChatLimited as exc:
        raise ApiError(
            429,
            RATE_LIMITED,
            LIMIT_MESSAGES.get(exc.scope, LIMIT_MESSAGES["busy"]),
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    except ChatUnavailable:
        raise ApiError(503, CHAT_UNAVAILABLE, MESSAGE_UNAVAILABLE) from None
    return JSONResponse(result, headers=NO_STORE)


@router.get("/poll")
def poll(request: Request) -> JSONResponse:
    chat = _chat(request)
    q = request.query_params
    try:
        conversation_id, token, after = chat.clean_poll(
            q.get("conversation_id"), q.get("token"), q.get("after", "")
        )
        result = chat.poll(conversation_id, token, after)
    except InvalidChatInput as exc:
        if exc.field == "token":
            raise ApiError(403, INVALID_REQUEST, MESSAGE_SESSION) from None
        raise ApiError(
            400, INVALID_REQUEST, MESSAGE_INVALID, fields={exc.field: "invalid"}
        ) from None
    except ChatUnavailable:
        raise ApiError(503, CHAT_UNAVAILABLE, MESSAGE_UNAVAILABLE) from None
    return JSONResponse(result, headers=NO_STORE)
