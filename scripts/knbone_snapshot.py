#!/usr/bin/env python3
"""
knBONE snapshot pipeline.

Captures knBONE balances on Shibarium at a configurable snapshot block and
writes `knbone-airdrop.csv` in the wizard's `address,amount` (wei) format.

Uses the same Transfer-log-scan + batched balanceOf approach as snapshot.py
and reuses its helpers. The snapshot block is read from the KNBONE_SNAPSHOT_BLOCK
env var (placeholder-defaulted to the same value as the KNINE sunset block so
the script is runnable today; expected to be overridden once the knBONE
snapshot block is chosen).
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from tqdm.auto import tqdm

from snapshot import (
    DEFAULT_RPC_URL,
    DEFAULT_SNAPSHOT_BLOCK,
    DEFAULT_START_BLOCK,
    MAX_WORKERS,
    Rpc,
    Token,
    normalize_address,
    scan_token_transfer_recipients,
    tprint,
    write_airdrop_csv,
)

KNBONE = Token("knbone", "knBONE", "0x3358FCA51d7C0408750FBbE7777012E0b67C027F")


def fetch_knbone_balances(
    rpc: Rpc, addresses: Iterable[str], snapshot_block: int, workers: int
) -> Dict[str, int]:
    address_list = list(addresses)
    out: Dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(rpc.balance_batch, addr, (KNBONE,), snapshot_block): addr for addr in address_list}
        with tqdm(total=len(futures), desc="balanceOf", unit="addr") as pbar:
            for fut in as_completed(futures):
                addr = futures[fut]
                try:
                    out[addr] = fut.result().get(KNBONE.key, 0)
                except Exception as exc:
                    tprint(f"  ! {addr}: {exc}")
                    out[addr] = 0
                pbar.update(1)
    return out


def parse_args(argv: List[str]) -> argparse.Namespace:
    here = Path(__file__).resolve()
    default_out = here.parents[2] / "docs"
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rpc-url", default=os.environ.get("SHIBARIUM_RPC_URL", DEFAULT_RPC_URL))
    p.add_argument(
        "--snapshot-block",
        type=int,
        default=int(os.environ.get("KNBONE_SNAPSHOT_BLOCK", DEFAULT_SNAPSHOT_BLOCK)),
        help="block to snapshot at. Default: $KNBONE_SNAPSHOT_BLOCK (placeholder matches KNINE sunset block)",
    )
    p.add_argument(
        "--start-block",
        type=int,
        default=int(os.environ.get("SHIBARIUM_START_BLOCK", DEFAULT_START_BLOCK)),
    )
    p.add_argument("--out-dir", type=Path, default=default_out,
                   help="where to write outputs (default: repo docs/)")
    p.add_argument("--workers", type=int, default=MAX_WORKERS,
                   help="parallel balanceOf workers")
    p.add_argument("--addresses-file", type=Path, default=None,
                   help="optional newline-delimited address list to use INSTEAD of scanning logs")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.start_block > args.snapshot_block:
        print(f"start-block ({args.start_block}) > snapshot-block ({args.snapshot_block})", file=sys.stderr)
        return 2

    tprint(f"RPC:            {args.rpc_url}")
    tprint(f"start block:    {args.start_block:,}")
    tprint(f"snapshot block: {args.snapshot_block:,}")
    tprint(f"out dir:        {args.out_dir}")
    tprint(f"token:          {KNBONE.symbol} @ {KNBONE.address}")

    rpc = Rpc(args.rpc_url)

    if args.addresses_file:
        tprint(f"\nStep 1/3: loading addresses from {args.addresses_file}")
        addresses: Set[str] = set()
        for line in args.addresses_file.read_text().splitlines():
            addr = normalize_address(line.strip())
            if addr:
                addresses.add(addr)
    else:
        tprint("\nStep 1/3: scanning knBONE Transfer logs...")
        addresses = scan_token_transfer_recipients(rpc, KNBONE, args.start_block, args.snapshot_block)
    tprint(f"  {len(addresses):,} unique addresses")

    tprint("\nStep 2/3: fetching knBONE balanceOf at snapshot block...")
    raw_balances = fetch_knbone_balances(rpc, addresses, args.snapshot_block, workers=args.workers)

    tprint("\nStep 3/3: writing outputs...")
    # Wrap single-token balances in the shape write_airdrop_csv expects
    # (address -> {token_key: int}).
    balances = {addr: {KNBONE.key: bal} for addr, bal in raw_balances.items()}
    airdrop_path = args.out_dir / "knbone-airdrop.csv"
    rows = write_airdrop_csv(airdrop_path, balances, (KNBONE.key,))
    tprint(f"  {airdrop_path}  ({rows:,} rows)")
    tprint("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
