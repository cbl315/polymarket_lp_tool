from __future__ import annotations

import logging
import time
from typing import Any, Optional

from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions

from passive_liquidity.copy_trading.config import CopyConfig
from passive_liquidity.copy_trading.target_monitor import OrderSnapshot
from passive_liquidity.http_utils import http_json

LOG = logging.getLogger(__name__)

_DATA_API = "https://data-api.polymarket.com"


def _is_closing_order(inventory: float, side: str) -> bool:
    """Return True if this order would close/reduce an existing position."""
    if abs(inventory) < 1e-8:
        return False
    # Long position + SELL → closing
    if inventory > 0 and side == "SELL":
        return True
    # Short position + BUY → closing
    if inventory < 0 and side == "BUY":
        return True
    return False


class OrderReplicator:
    """Replicates target orders onto the self account."""

    def __init__(
        self,
        config: CopyConfig,
        self_client: Any,
        target_address: str,
    ) -> None:
        self._config = config
        self._client = self_client
        self._target_address = target_address
        # Map: target_order_id -> our_order_id
        self._our_orders: dict[str, str] = {}
        # Map: our_order_id -> target_order_id (reverse lookup for cancels)
        self._reverse_map: dict[str, str] = {}
        # Cached inventory: token_id -> inventory
        self._inventory_cache: dict[str, float] = {}
        self._inventory_ts: float = 0.0

    def _refresh_inventory(self) -> dict[str, float]:
        """Fetch target positions from Data API."""
        try:
            url = (
                f"{_DATA_API}/positions?"
                f"user={self._target_address}&limit=500"
            )
            rows = http_json("GET", url)
        except Exception as e:
            LOG.warning("Failed to fetch target positions: %s", e)
            return self._inventory_cache
        if not isinstance(rows, list):
            return self._inventory_cache
        inv: dict[str, float] = {}
        for p in rows:
            aid = str(p.get("asset") or "")
            size = float(p.get("size") or 0)
            if aid and abs(size) > 1e-8:
                inv[aid] = size
        self._inventory_cache = inv
        self._inventory_ts = time.monotonic()
        LOG.debug("Refreshed target inventory: %d tokens with position", len(inv))
        return inv

    def _get_inventory_batch(
        self, snaps: list[OrderSnapshot]
    ) -> dict[str, float]:
        """Get inventory for all tokens in snaps, refreshing cache if stale."""
        cache_age = time.monotonic() - self._inventory_ts
        needed_tokens = {s.token_id for s in snaps}
        missing = needed_tokens - set(self._inventory_cache.keys())
        if cache_age > 60 or missing:
            self._refresh_inventory()
        return self._inventory_cache

    def replicate_added(self, added: list[OrderSnapshot]) -> None:
        inv_map = self._get_inventory_batch(added) if added else {}
        active_count = len(self._our_orders)
        for snap in added:
            if active_count >= self._config.max_orders:
                LOG.warning(
                    "Max orders (%d) reached, skipping %s",
                    self._config.max_orders,
                    snap.order_id[:16],
                )
                break

            # Skip closing orders (target reducing an existing position)
            inv = inv_map.get(snap.token_id, 0.0)
            if _is_closing_order(inv, snap.side):
                LOG.info(
                    "SKIP_CLOSING target=%s side=%s inventory=%.4f token=%s — "
                    "平仓单不跟",
                    snap.order_id[:16],
                    snap.side,
                    inv,
                    snap.token_id[:16],
                )
                continue

            our_size = round(snap.size * self._config.size_ratio, 4)
            if our_size < self._config.min_size:
                LOG.info(
                    "Skip replicate %s: size %.4f < min %.4f",
                    snap.order_id[:16],
                    our_size,
                    self._config.min_size,
                )
                continue

            our_oid = self._place_order(snap, our_size)
            if our_oid:
                self._our_orders[snap.order_id] = our_oid
                self._reverse_map[our_oid] = snap.order_id
                active_count += 1
                LOG.info(
                    "REPLICATED target=%s -> ours=%s side=%s price=%.4f size=%.4f token=%s",
                    snap.order_id[:16],
                    our_oid[:16],
                    snap.side,
                    snap.price,
                    our_size,
                    snap.token_id[:16],
                )

    def cancel_removed(self, removed: list[OrderSnapshot]) -> None:
        for snap in removed:
            our_oid = self._our_orders.pop(snap.order_id, None)
            if not our_oid:
                continue
            self._reverse_map.pop(our_oid, None)
            self._cancel_order(our_oid)
            LOG.info(
                "CANCELLED ours=%s (target %s removed)",
                our_oid[:16],
                snap.order_id[:16],
            )

    def cleanup_all(self) -> None:
        for target_oid, our_oid in list(self._our_orders.items()):
            self._cancel_order(our_oid)
            LOG.info("Cleanup cancel ours=%s", our_oid[:16])
        self._our_orders.clear()
        self._reverse_map.clear()

    def _place_order(
        self, snap: OrderSnapshot, size: float
    ) -> Optional[str]:
        try:
            tick_size = self._client.get_tick_size(snap.token_id)
            neg_risk = self._client.get_neg_risk(snap.token_id)

            order_args = OrderArgs(
                token_id=snap.token_id,
                price=snap.price,
                size=size,
                side=snap.side,
            )
            opts = PartialCreateOrderOptions(
                tick_size=tick_size,
                neg_risk=neg_risk,
            )
            signed = self._client.create_order(order_args, opts)
            result = self._client.post_order(signed, orderType=OrderType.GTC)
            our_oid = result.get("orderID") or result.get("id") or ""
            if not our_oid and isinstance(result, dict):
                LOG.warning("post_order returned no orderID: %s", result)
            return str(our_oid) if our_oid else None
        except Exception as e:
            LOG.error(
                "Failed to place order (target=%s side=%s price=%.4f size=%.4f): %s",
                snap.order_id[:16],
                snap.side,
                snap.price,
                size,
                e,
            )
            return None

    def _cancel_order(self, order_id: str) -> None:
        try:
            self._client.cancel(order_id)
        except Exception as e:
            LOG.warning("Failed to cancel order %s: %s", order_id[:16], e)
