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
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from tqdm.auto import tqdm

from snapshot import (
    DEFAULT_RPC_URL,
    DEFAULT_SNAPSHOT_BLOCK,
    DEFAULT_START_BLOCK,
    LOG_CHUNK_ADD,
    LOG_CHUNK_MAX,
    LOG_CHUNK_MIN,
    LOG_CHUNK_MULTIPLIER_DOWN,
    LOG_CHUNK_START,
    LOG_FAIL_STREAK_LIMIT,
    MAX_WORKERS,
    Rpc,
    Token,
    ZERO_ADDRESS,
    normalize_address,
    topic_to_address,
    tprint,
    write_airdrop_csv,
)

KNBONE = Token("knbone", "knBONE", "0x3358FCA51d7C0408750FBbE7777012E0b67C027F")


def _checkpoint_config(start_block: int, snapshot_block: int) -> Dict[str, object]:
    return {
        "token": KNBONE.symbol,
        "tokenAddress": KNBONE.address.lower(),
        "startBlock": start_block,
        "snapshotBlock": snapshot_block,
    }


def _write_checkpoint(
    path: Path,
    start_block: int,
    snapshot_block: int,
    next_block: int,
    addresses: Set[str],
    *,
    complete: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **_checkpoint_config(start_block, snapshot_block),
        "nextBlock": next_block,
        "complete": complete,
        "addresses": sorted(addresses),
    }
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


def _load_checkpoint(
    path: Path, start_block: int, snapshot_block: int
) -> Tuple[int, Set[str], bool]:
    data = json.loads(path.read_text())
    expected = _checkpoint_config(start_block, snapshot_block)
    for key, value in expected.items():
        if data.get(key) != value:
            raise ValueError(
                f"{path} does not match this run: {key}={data.get(key)!r}, expected {value!r}"
            )

    next_block = int(data.get("nextBlock", start_block))
    if next_block < start_block or next_block > snapshot_block + 1:
        raise ValueError(f"{path} has invalid nextBlock={next_block}")

    addresses = {
        addr
        for addr in (normalize_address(item) for item in data.get("addresses", []))
        if addr
    }
    return next_block, addresses, bool(data.get("complete"))


def scan_knbone_transfer_recipients(
    rpc: Rpc,
    start_block: int,
    snapshot_block: int,
    checkpoint_file: Optional[Path],
    *,
    resume: bool,
    chunk_start: int,
    chunk_add: int,
    chunk_max: int,
) -> Set[str]:
    receivers: Set[str] = set()
    current = start_block

    if checkpoint_file and resume and checkpoint_file.exists():
        current, receivers, complete = _load_checkpoint(checkpoint_file, start_block, snapshot_block)
        tprint(
            f"  resumed {checkpoint_file}: next block {current:,}, "
            f"{len(receivers):,} addresses"
        )
        if complete:
            return receivers

    chunk = max(LOG_CHUNK_MIN, min(chunk_start, chunk_max))
    fail_streak = 0
    total = max(0, snapshot_block - start_block + 1)
    completed = max(0, min(current, snapshot_block + 1) - start_block)

    with tqdm(total=total, initial=completed, desc=f"logs {KNBONE.symbol}", unit="blk") as pbar:
        while current <= snapshot_block:
            end = min(snapshot_block, current + chunk - 1)
            try:
                logs = rpc.get_logs(KNBONE.address, current, end, KNBONE.symbol)
            except Exception as exc:
                fail_streak += 1
                if chunk <= LOG_CHUNK_MIN and fail_streak >= LOG_FAIL_STREAK_LIMIT:
                    raise RuntimeError(
                        f"{KNBONE.symbol}: {LOG_FAIL_STREAK_LIMIT} consecutive failures at min chunk; aborting"
                    ) from exc
                chunk = max(LOG_CHUNK_MIN, int(chunk * LOG_CHUNK_MULTIPLIER_DOWN))
                tprint(f"  {KNBONE.symbol}: getLogs failed near {current} ({exc}); chunk -> {chunk}")
                continue

            fail_streak = 0
            for log in logs:
                topics = log.get("topics", [])
                if len(topics) >= 3:
                    addr = normalize_address(topic_to_address(topics[2]))
                    if addr and addr != ZERO_ADDRESS:
                        receivers.add(addr)

            advanced = end - current + 1
            current = end + 1
            pbar.update(advanced)
            if checkpoint_file:
                _write_checkpoint(
                    checkpoint_file,
                    start_block,
                    snapshot_block,
                    current,
                    receivers,
                    complete=False,
                )
            chunk = min(chunk_max, chunk + chunk_add)

    if checkpoint_file:
        _write_checkpoint(
            checkpoint_file,
            start_block,
            snapshot_block,
            snapshot_block + 1,
            receivers,
            complete=True,
        )
    return receivers


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
    p.add_argument(
        "--checkpoint-file",
        type=Path,
        default=None,
        help="log scan checkpoint file. Default: <out-dir>/knbone-scan-checkpoint.json",
    )
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="ignore an existing checkpoint and rescan from start-block",
    )
    p.add_argument(
        "--log-chunk-start",
        type=int,
        default=int(os.environ.get("KNBONE_LOG_CHUNK_START", LOG_CHUNK_START)),
        help="initial eth_getLogs block range size",
    )
    p.add_argument(
        "--log-chunk-add",
        type=int,
        default=int(os.environ.get("KNBONE_LOG_CHUNK_ADD", LOG_CHUNK_ADD)),
        help="block range size increase after each successful eth_getLogs call",
    )
    p.add_argument(
        "--log-chunk-max",
        type=int,
        default=int(os.environ.get("KNBONE_LOG_CHUNK_MAX", LOG_CHUNK_MAX)),
        help="maximum eth_getLogs block range size",
    )
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
    checkpoint_file = args.checkpoint_file or (args.out_dir / "knbone-scan-checkpoint.json")
    tprint(f"checkpoint:     {checkpoint_file}")

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
        addresses = scan_knbone_transfer_recipients(
            rpc,
            args.start_block,
            args.snapshot_block,
            checkpoint_file,
            resume=not args.no_resume,
            chunk_start=args.log_chunk_start,
            chunk_add=args.log_chunk_add,
            chunk_max=args.log_chunk_max,
        )
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
