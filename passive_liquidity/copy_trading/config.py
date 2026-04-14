from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_DIR / ".env"


@dataclass(frozen=True)
class CopyConfig:
    # --- Self account (places orders) ---
    self_private_key: str
    self_funder: str
    self_sig_type: int

    # --- Target account (read-only, query open orders) ---
    target_private_key: str
    target_funder: str
    target_sig_type: int

    # --- Parameters ---
    size_ratio: float = 0.5
    poll_interval: float = 5.0
    min_size: float = 1.0
    max_orders: int = 30

    # --- CLOB ---
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None) -> "CopyConfig":
        load_dotenv(env_file or _ENV_FILE, override=False)

        self_pk = os.environ.get("COPY_SELF_PRIVATE_KEY", "")
        self_funder = os.environ.get("COPY_SELF_FUNDER", "")
        target_pk = os.environ.get("COPY_TARGET_PRIVATE_KEY", "")
        target_funder = os.environ.get("COPY_TARGET_FUNDER", "")

        missing = []
        if not self_pk:
            missing.append("COPY_SELF_PRIVATE_KEY")
        if not self_funder:
            missing.append("COPY_SELF_FUNDER")
        if not target_pk:
            missing.append("COPY_TARGET_PRIVATE_KEY")
        if not target_funder:
            missing.append("COPY_TARGET_FUNDER")
        if missing:
            raise RuntimeError(
                f"Missing required env vars: {', '.join(missing)}"
            )

        return cls(
            self_private_key=self_pk,
            self_funder=self_funder,
            self_sig_type=int(os.environ.get("COPY_SELF_SIGNATURE_TYPE", "2")),
            target_private_key=target_pk,
            target_funder=target_funder,
            target_sig_type=int(os.environ.get("COPY_TARGET_SIGNATURE_TYPE", "2")),
            size_ratio=float(os.environ.get("COPY_SIZE_RATIO", "0.5")),
            poll_interval=float(os.environ.get("COPY_POLL_INTERVAL", "5")),
            min_size=float(os.environ.get("COPY_MIN_SIZE", "1")),
            max_orders=int(os.environ.get("COPY_MAX_ORDERS", "30")),
            host=os.environ.get("POLYMARKET_HOST", "https://clob.polymarket.com"),
            chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
        )
