from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

LOG = logging.getLogger(__name__)


@dataclass
class OrderSnapshot:
    order_id: str
    token_id: str
    condition_id: str
    side: str
    price: float
    size: float

    @staticmethod
    def from_order(o: dict) -> Optional["OrderSnapshot"]:
        oid = o.get("id") or ""
        tid = o.get("asset_id") or ""
        cid = o.get("market") or ""
        side = (o.get("side") or "").upper()
        if not oid or not tid or not side:
            return None
        try:
            price = float(o.get("price", 0))
            size = float(o.get("original_size") or o.get("size", 0))
        except (TypeError, ValueError):
            return None
        return OrderSnapshot(
            order_id=str(oid),
            token_id=str(tid),
            condition_id=str(cid),
            side=side,
            price=price,
            size=size,
        )


@dataclass
class SnapshotDiff:
    added: list[OrderSnapshot]
    removed: list[OrderSnapshot]
    unchanged: list[OrderSnapshot]


def fetch_target_orders(client: Any) -> list[OrderSnapshot]:
    try:
        raw_orders = client.get_orders()
    except Exception as e:
        LOG.error("Failed to fetch target orders: %s", e)
        return []
    snapshots = []
    for o in raw_orders:
        if not isinstance(o, dict):
            continue
        snap = OrderSnapshot.from_order(o)
        if snap:
            snapshots.append(snap)
    return snapshots


def diff_snapshots(
    prev: dict[str, OrderSnapshot],
    current: list[OrderSnapshot],
) -> SnapshotDiff:
    cur_map = {s.order_id: s for s in current}
    added = [s for oid, s in cur_map.items() if oid not in prev]
    removed = [s for oid, s in prev.items() if oid not in cur_map]
    unchanged = [s for oid, s in cur_map.items() if oid in prev]
    return SnapshotDiff(added=added, removed=removed, unchanged=unchanged)
