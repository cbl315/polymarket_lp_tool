from __future__ import annotations

import logging
import signal
import sys
import time
from typing import Any

from py_clob_client.client import ClobClient

from passive_liquidity.copy_trading.config import CopyConfig
from passive_liquidity.copy_trading.order_replicator import OrderReplicator
from passive_liquidity.copy_trading.target_monitor import (
    OrderSnapshot,
    diff_snapshots,
    fetch_target_orders,
)

LOG = logging.getLogger(__name__)


def _build_client(config: CopyConfig, pk: str, funder: str, sig: int) -> ClobClient:
    client = ClobClient(
        config.host,
        key=pk,
        chain_id=config.chain_id,
        signature_type=sig,
        funder=funder,
    )
    creds = client.create_or_derive_api_creds()
    if creds is None:
        raise RuntimeError("Failed to create/derive API creds")
    client.set_api_creds(creds)
    return client


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    config = CopyConfig.from_env()
    LOG.info("=== Copy Trading Bot ===")
    LOG.info(
        "self=%s target=%s ratio=%.2f interval=%.1fs",
        config.self_funder[:10],
        config.target_funder[:10],
        config.size_ratio,
        config.poll_interval,
    )

    LOG.info("Building target client...")
    target_client = _build_client(
        config,
        config.target_private_key,
        config.target_funder,
        config.target_sig_type,
    )
    LOG.info("Target address: %s", target_client.get_address())

    LOG.info("Building self client...")
    self_client = _build_client(
        config,
        config.self_private_key,
        config.self_funder,
        config.self_sig_type,
    )
    LOG.info("Self address: %s", self_client.get_address())

    replicator = OrderReplicator(config, self_client, target_client.get_address())

    # Graceful shutdown
    running = True

    def _signal_handler(sig: int, frame: Any) -> None:
        nonlocal running
        LOG.info("Received signal %s, shutting down...", sig)
        running = False

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Initial snapshot — also replicate existing orders
    prev_map: dict[str, OrderSnapshot] = {}
    initial_orders = fetch_target_orders(target_client)
    LOG.info("Initial target orders: %d", len(initial_orders))
    for s in initial_orders:
        LOG.info(
            "  %s side=%s price=%.4f size=%.4f token=%s",
            s.order_id[:16],
            s.side,
            s.price,
            s.size,
            s.token_id[:20],
        )
    if initial_orders:
        LOG.info("Replicating initial target orders...")
        replicator.replicate_added(initial_orders)
    prev_map = {s.order_id: s for s in initial_orders}

    LOG.info("Polling started (interval=%.1fs)", config.poll_interval)

    while running:
        try:
            current = fetch_target_orders(target_client)
            diff = diff_snapshots(prev_map, current)

            if diff.added:
                LOG.info("New target orders: %d", len(diff.added))
                replicator.replicate_added(diff.added)

            if diff.removed:
                LOG.info("Removed target orders: %d", len(diff.removed))
                replicator.cancel_removed(diff.removed)

            prev_map = {s.order_id: s for s in current}

        except Exception as e:
            LOG.error("Poll cycle error: %s", e, exc_info=True)

        # Sleep in small chunks for responsive shutdown
        deadline = time.monotonic() + config.poll_interval
        while running and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))

    LOG.info("Shutting down, cleaning up orders...")
    replicator.cleanup_all()
    LOG.info("Done.")
