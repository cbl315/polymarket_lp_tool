"""
Live account / orders / PnL for Telegram commands (no periodic-summary cache).

Each call performs fresh CLOB + optional on-chain/Bridge fetches.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Any, Optional, Tuple

from passive_liquidity.account_portfolio import (
    combine_clob_and_positions_market_value_usdc,
    fetch_collateral_snapshot,
    read_optional_deposit_env,
    resolve_deposit_reference,
)
from passive_liquidity.bridge_deposits import fetch_bridge_polygon_usdc_deposits
from passive_liquidity.order_manager import OrderManager, _market, _side, _token_id
from passive_liquidity.polygon_deposits import fetch_polygon_usdc_deposit_summary

LOG = logging.getLogger(__name__)


def _fmt_usdc(x: float) -> str:
    return f"{x:.4f}"


def _data_api_host() -> str:
    return os.environ.get(
        "POLYMARKET_DATA_API", "https://data-api.polymarket.com"
    ).rstrip("/")


def get_live_account_status(
    *,
    client: Any,
    order_manager: OrderManager,
    funder: str,
    account_label: str,
) -> Tuple[bool, str]:
    """
    Returns (ok, formatted_zh_message_or_error).
    """
    try:
        orders = order_manager.fetch_all_open_orders(client)
    except Exception as e:
        LOG.exception("live /status: fetch orders failed: %s", e)
        return False, f"查询失败（未成交单）: {e}"

    try:
        snap = fetch_collateral_snapshot(client, orders)
    except Exception as e:
        LOG.exception("live /status: collateral failed: %s", e)
        return False, f"查询失败（账户余额）: {e}"

    if snap is None:
        return False, "查询失败：无法取得 CLOB 抵押品快照。"

    clob_usdc = float(snap.total_balance_usdc)
    portfolio_usdc, pos_sum, pos_err = combine_clob_and_positions_market_value_usdc(
        clob_usdc, funder, _data_api_host()
    )
    if pos_sum is None:
        pos_note = f"持仓市值: （未计入，Data API: {pos_err}）"
    else:
        pos_note = f"持仓市值合计（Data API）: {_fmt_usdc(float(pos_sum))} USDC"

    env_dep = read_optional_deposit_env()
    polygon_summary = None
    bridge_summary = None
    try:
        polygon_summary = fetch_polygon_usdc_deposit_summary(funder)
    except Exception as e:
        LOG.debug("live /status: polygon deposit fetch: %s", e)
    try:
        bridge_summary = fetch_bridge_polygon_usdc_deposits(funder)
    except Exception as e:
        LOG.debug("live /status: bridge deposit fetch: %s", e)

    dep, dep_src, _approx = resolve_deposit_reference(
        polygon_summary=polygon_summary,
        env_override=env_dep,
        bridge_summary=bridge_summary,
        startup_total_balance=float(portfolio_usdc),
    )
    pnl: Optional[float]
    if dep is not None:
        pnl = float(portfolio_usdc) - float(dep)
    else:
        pnl = None

    label = (account_label or "Polymarket").strip() or "Polymarket"
    lines = [
        f"[{label}]",
        "实时状态",
        f"账户总额（组合≈）: {_fmt_usdc(portfolio_usdc)} USDC",
        f"CLOB 抵押 USDC: {_fmt_usdc(clob_usdc)} USDC",
        pos_note,
        f"可用余额（CLOB 可开新单≈）: {_fmt_usdc(snap.available_balance_usdc)} USDC",
    ]
    if dep is not None:
        lines.append(f"入账参考: {_fmt_usdc(dep)} USDC")
    else:
        lines.append("入账参考: （未配置）")
    if pnl is not None:
        sign = "+" if pnl >= 0 else ""
        lines.append(f"盈亏: {sign}{_fmt_usdc(pnl)} USDC")
    else:
        lines.append(f"盈亏: （未配置入账参考；{dep_src[:80]}）")
    lines.append(f"未成交买单占用: {_fmt_usdc(snap.locked_open_buy_usdc)} USDC")
    lines.append(f"当前挂单数: {len(orders)}")
    return True, "\n".join(lines)


def get_live_order_summary(
    *,
    client: Any,
    order_manager: OrderManager,
    account_label: str,
) -> Tuple[bool, str]:
    try:
        orders = order_manager.fetch_all_open_orders(client)
    except Exception as e:
        LOG.exception("live /orders: fetch failed: %s", e)
        return False, f"查询失败: {e}"

    label = (account_label or "Polymarket").strip() or "Polymarket"
    n = len(orders)
    lines = [
        f"[{label}]",
        "实时挂单",
        f"未成交单总数: {n}",
    ]
    if n == 0:
        lines.append("（当前无挂单）")
        return True, "\n".join(lines)

    by_token: Counter[str] = Counter()
    by_market: Counter[str] = Counter()
    by_side: Counter[str] = Counter()
    for o in orders:
        if not isinstance(o, dict):
            continue
        tid = _token_id(o)
        if tid:
            by_token[tid] += 1
        m = _market(o)
        if m:
            by_market[m] += 1
        by_side[_side(o) or "?"] += 1

    lines.append(
        "方向统计: "
        + ", ".join(f"{k}={v}" for k, v in sorted(by_side.items()))
    )
    lines.append(f"不同 outcome 数（token_id）: {len(by_token)}")
    lines.append(f"不同 condition 数: {len(by_market)}")
    lines.append("")
    lines.append("按 condition（前 10 个，次数，id 截断显示）:")
    for mk, c in by_market.most_common(10):
        disp = (mk[:18] + "…") if len(mk) > 20 else mk
        lines.append(f" · {c}×  {disp}")
    if len(by_market) > 10:
        lines.append(f" … 共 {len(by_market)} 个市场，已截断")
    lines.append("")
    lines.append("按 token（前 15 个，次数）:")
    for tid, c in by_token.most_common(15):
        lines.append(f" · {c}×  {tid[:20]}…" if len(tid) > 20 else f" · {c}×  {tid}")
    if len(by_token) > 15:
        lines.append(f" … 共 {len(by_token)} 个 token，已截断")
    return True, "\n".join(lines)


def get_live_pnl(
    *,
    client: Any,
    order_manager: OrderManager,
    funder: str,
    account_label: str,
) -> Tuple[bool, str]:
    """Same deposit resolution as /status; message focused on PnL."""
    try:
        orders = order_manager.fetch_all_open_orders(client)
    except Exception as e:
        LOG.exception("live /pnl: orders failed: %s", e)
        return False, f"查询失败（未成交单）: {e}"

    try:
        snap = fetch_collateral_snapshot(client, orders)
    except Exception as e:
        LOG.exception("live /pnl: collateral failed: %s", e)
        return False, f"查询失败（账户）: {e}"

    if snap is None:
        return False, "查询失败：无法取得账户总额。"

    clob_usdc = float(snap.total_balance_usdc)
    portfolio_usdc, pos_sum, pos_err = combine_clob_and_positions_market_value_usdc(
        clob_usdc, funder, _data_api_host()
    )

    env_dep = read_optional_deposit_env()
    polygon_summary = None
    bridge_summary = None
    try:
        polygon_summary = fetch_polygon_usdc_deposit_summary(funder)
    except Exception as e:
        LOG.debug("live /pnl: polygon: %s", e)
    try:
        bridge_summary = fetch_bridge_polygon_usdc_deposits(funder)
    except Exception as e:
        LOG.debug("live /pnl: bridge: %s", e)

    dep, dep_src, _ = resolve_deposit_reference(
        polygon_summary=polygon_summary,
        env_override=env_dep,
        bridge_summary=bridge_summary,
        startup_total_balance=float(portfolio_usdc),
    )
    label = (account_label or "Polymarket").strip() or "Polymarket"
    lines = [
        f"[{label}]",
        "实时盈亏",
        f"组合总额（≈）: {_fmt_usdc(portfolio_usdc)} USDC",
        f"CLOB 抵押: {_fmt_usdc(clob_usdc)} USDC",
    ]
    if pos_sum is None:
        lines.append(f"持仓市值: （未计入：{pos_err}）")
    else:
        lines.append(f"持仓市值: {_fmt_usdc(float(pos_sum))} USDC")
    if dep is not None:
        pnl = float(portfolio_usdc) - float(dep)
        sign = "+" if pnl >= 0 else ""
        lines.append(f"入账参考: {_fmt_usdc(dep)} USDC")
        lines.append(f"参考来源: {dep_src}")
        lines.append(f"盈亏: {sign}{_fmt_usdc(pnl)} USDC")
    else:
        lines.append("入账参考: （未配置）")
        lines.append(f"说明: {dep_src}")
        lines.append("盈亏: 无法计算（请先配置入账参考）")
    return True, "\n".join(lines)
