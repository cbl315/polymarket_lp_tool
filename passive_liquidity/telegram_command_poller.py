"""
Long-poll Telegram updates in a daemon thread; handle /status, /orders, /pnl.

Isolated from the trading main loop; failures here do not affect order logic.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from passive_liquidity.order_manager import OrderManager
from passive_liquidity.telegram_live_queries import (
    get_live_account_status,
    get_live_order_summary,
    get_live_pnl,
)
from passive_liquidity.telegram_notifier import TelegramNotifier

LOG = logging.getLogger(__name__)


def _commands_enabled_from_env() -> bool:
    v = os.environ.get("TELEGRAM_COMMANDS_ENABLED", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return True


def _parse_command(text: str) -> Optional[str]:
    """
    Normalize Telegram command text for dispatch.

    In groups, Telegram sends e.g. ``/status@YourBotName`` — strip the ``@botname``
    suffix so all handlers see ``/status`` (same for /orders, /pnl, /help, /start).
    """
    raw = str(text).strip()
    if not raw.startswith("/"):
        return None
    # First whitespace-delimited token only (ignore command arguments)
    token = raw.split(None, 1)[0]
    # /status@PolyMarket_ccpanda_bot → /status
    command = token.split("@", 1)[0]
    if not command.startswith("/"):
        return None
    return command.lower()


def _chat_id_matches(msg_chat_id: Any, configured: str) -> bool:
    if msg_chat_id is None or not configured:
        return False
    return str(msg_chat_id).strip() == str(configured).strip()


def _get_updates(bot_token: str, offset: int, timeout_sec: int) -> list[dict]:
    params: dict[str, Any] = {"timeout": int(timeout_sec)}
    if offset > 0:
        params["offset"] = int(offset)
    q = urllib.parse.urlencode(params)
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates?{q}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_sec + 5) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw) if raw else {}
    if not data.get("ok"):
        LOG.warning("getUpdates not ok: %s", raw[:500])
        return []
    return list(data.get("result") or [])


def _poll_loop(
    *,
    stop: threading.Event,
    notifier: TelegramNotifier,
    client: Any,
    order_manager: OrderManager,
    funder: str,
    poll_timeout_sec: int,
) -> None:
    token = notifier.bot_token
    expect_chat = notifier.chat_id
    offset = 0
    while not stop.is_set():
        try:
            updates = _get_updates(token, offset, poll_timeout_sec)
        except urllib.error.HTTPError as e:
            LOG.warning("Telegram getUpdates HTTPError: %s", e)
            time.sleep(3.0)
            continue
        except Exception as e:
            LOG.warning("Telegram getUpdates failed: %s", e)
            time.sleep(3.0)
            continue

        max_uid = 0
        for u in updates:
            try:
                max_uid = max(max_uid, int(u.get("update_id") or 0))
            except (TypeError, ValueError):
                pass

        for u in updates:
            msg = u.get("message") or u.get("edited_message")
            if not isinstance(msg, dict):
                continue
            chat = msg.get("chat") or {}
            if not _chat_id_matches(chat.get("id"), expect_chat):
                continue

            text = msg.get("text")
            if not isinstance(text, str):
                continue
            cmd = _parse_command(text)
            if cmd is None:
                continue

            LOG.info("Telegram command received: %s", cmd)

            try:
                if cmd == "/status":
                    ok, body = get_live_account_status(
                        client=client,
                        order_manager=order_manager,
                        funder=funder,
                        account_label=notifier.account_label,
                    )
                elif cmd == "/orders":
                    ok, body = get_live_order_summary(
                        client=client,
                        order_manager=order_manager,
                        account_label=notifier.account_label,
                    )
                elif cmd == "/pnl":
                    ok, body = get_live_pnl(
                        client=client,
                        order_manager=order_manager,
                        funder=funder,
                        account_label=notifier.account_label,
                    )
                elif cmd in ("/start", "/help"):
                    body = (
                        f"[{notifier.account_label}]\n"
                        "可用命令（实时查询，非半点摘要）：\n"
                        "/status — 账户与挂单概览\n"
                        "/orders — 未成交单统计\n"
                        "/pnl — 盈亏\n"
                    )
                    ok = True
                else:
                    continue

                if not ok:
                    body = f"[{notifier.account_label}]\n⚠️ {body}"
                notifier.send_command_reply(body)
            except Exception as e:
                LOG.exception("Telegram command handler error: %s", e)
                notifier.send_command_reply(
                    f"[{notifier.account_label}]\n⚠️ 命令处理异常: {e}"
                )

        if max_uid > 0:
            offset = max_uid + 1


def start_telegram_command_poller(
    *,
    notifier: TelegramNotifier,
    client: Any,
    order_manager: OrderManager,
    funder: str,
    stop: threading.Event,
) -> Optional[threading.Thread]:
    if not notifier.enabled:
        LOG.info("Telegram command poller skipped (notifications disabled)")
        return None
    if not _commands_enabled_from_env():
        LOG.info("Telegram command poller skipped (TELEGRAM_COMMANDS_ENABLED=off)")
        return None

    def _timeout() -> int:
        try:
            v = int(os.environ.get("TELEGRAM_COMMAND_POLL_TIMEOUT", "25"))
        except ValueError:
            v = 25
        return max(1, min(50, v))

    poll_timeout = _timeout()

    def _run() -> None:
        LOG.info(
            "Telegram command poller started (timeout=%ds, chat_id=%s)",
            poll_timeout,
            notifier.chat_id[:12] + "…" if len(notifier.chat_id) > 12 else notifier.chat_id,
        )
        _poll_loop(
            stop=stop,
            notifier=notifier,
            client=client,
            order_manager=order_manager,
            funder=funder,
            poll_timeout_sec=poll_timeout,
        )
        LOG.info("Telegram command poller stopped")

    t = threading.Thread(
        target=_run,
        name="telegram-commands",
        daemon=True,
    )
    t.start()
    return t
