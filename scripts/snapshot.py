#!/usr/bin/env python3
"""
Shibarium sunset snapshot pipeline.

For the four tokens tracked on Shibarium (KNINE, sKNINE, esKNINE, vKNINE) this
script produces three CSVs and a JSON lookup at a specific snapshot block:

    docs/shibarium-snapshot-balances.csv
        address,knine_wei,sknine_wei,esknine_wei,vknine_wei

    docs/knine-airdrop.csv
        address,amount         where amount = (KNINE + sKNINE) in wei

    docs/esknine-airdrop.csv
        address,amount         where amount = (esKNINE + vKNINE) in wei

    docs/shibarium-snapshot-balances.json
        { "snapshotBlock": int, "addresses": { "0x..": { "knine": "..", ... } } }

Approach (pure JSON-RPC; no web3.py / ethers):
  1. eth_getLogs per token for Transfer events across [START_BLOCK, SNAPSHOT_BLOCK]
     using AIMD block-range chunking (5k start, +500 on success, x0.8 on failure,
     25-fail kill switch).
  2. Union all `to` addresses (topics[2]) across the four tokens, minus 0x0.
  3. Batched eth_call balanceOf(addr) (one HTTP POST with 4 JSON-RPC calls per
     address) at blockTag = SNAPSHOT_BLOCK, parallelised with a 32-worker pool.
  4. Write outputs.

Defaults come from the liquid-staking frontend's .env / rpc-metadata.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import requests
from tqdm.auto import tqdm

# ---------------------------------------------------------------------------
# Defaults (overridable via CLI / env)
# ---------------------------------------------------------------------------
DEFAULT_RPC_URL = "https://shibarium.drpc.org"
DEFAULT_SNAPSHOT_BLOCK = 15_876_116  # matches NEXT_PUBLIC_SHIBARIUM_SUNSET_SNAPSHOT_BLOCK
DEFAULT_START_BLOCK = 1

TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
BALANCEOF_SELECTOR = "0x70a08231"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# AIMD tuning copied from Snapshot Data - RPC.ipynb
LOG_CHUNK_START = 5_000
LOG_CHUNK_ADD = 500
LOG_CHUNK_MULTIPLIER_DOWN = 0.8
LOG_CHUNK_MIN = 1
LOG_CHUNK_MAX = 25_000
LOG_FAIL_STREAK_LIMIT = 25

HTTP_TIMEOUT_SECS = 30
BACKOFF_BASE_SECS = 1.0
BACKOFF_MAX_SECS = 15.0
JITTER_SECS = 0.35

MAX_RETRIES_LOGS = 3
MAX_RETRIES_CALLS = 6
MAX_WORKERS = 32


@dataclass(frozen=True)
class Token:
    key: str          # e.g. "knine"
    symbol: str       # display symbol, e.g. "KNINE"
    address: str      # checksummed contract address


TOKENS: Tuple[Token, ...] = (
    Token("knine",   "KNINE",   "0x91fbB2503AC69702061f1AC6885759Fc853e6EaE"),
    Token("sknine",  "sKNINE",  "0xe13824Fb7b206E585c775B30431600528572C3E7"),
    Token("esknine", "esKNINE", "0x545d817F799092DbA53af785d79Bc95d296Af52e"),
    Token("vknine",  "vKNINE",  "0xf7384ba80A51979eC8cc0F17a843089ffD706f0a"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def tprint(msg: str) -> None:
    try:
        tqdm.write(str(msg))
    except Exception:
        print(msg)


def normalize_address(addr: Optional[str]) -> Optional[str]:
    if addr is None:
        return None
    a = str(addr).strip().lower()
    if not a.startswith("0x") or len(a) != 42:
        return None
    try:
        int(a[2:], 16)
    except ValueError:
        return None
    return a


def to_hex_block(n: int) -> str:
    return hex(int(n))


def topic_to_address(topic_hex: str) -> str:
    t = str(topic_hex).lower()
    if t.startswith("0x"):
        t = t[2:]
    return "0x" + t[-40:]


def encode_balance_of(addr: str) -> str:
    addr = normalize_address(addr)
    if addr is None:
        raise ValueError(f"invalid address for balanceOf: {addr}")
    return BALANCEOF_SELECTOR + addr[2:].rjust(64, "0")


def decode_uint256_hex(data: Optional[str]) -> int:
    if not data or data == "0x":
        return 0
    return int(data, 16)


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------
class Rpc:
    def __init__(self, url: str) -> None:
        self.url = url
        self.session = requests.Session()

    def _post(self, payload, *, label: str, max_retries: int) -> object:
        last_err: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                resp = self.session.post(
                    self.url,
                    json=payload,
                    timeout=HTTP_TIMEOUT_SECS,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # pragma: no cover - network flakiness
                last_err = exc
                if attempt == max_retries:
                    break
                wait = min(BACKOFF_MAX_SECS, BACKOFF_BASE_SECS * (2 ** attempt))
                wait += random.uniform(0, JITTER_SECS)
                time.sleep(wait)
        raise RuntimeError(f"{label} failed after {max_retries + 1} attempts: {last_err}")

    def get_logs(self, token_addr: str, from_block: int, to_block: int, symbol: str) -> List[dict]:
        token = normalize_address(token_addr)
        if token is None:
            raise ValueError(f"invalid token address: {token_addr}")
        params = {
            "fromBlock": to_hex_block(from_block),
            "toBlock": to_hex_block(to_block),
            "address": token,
            "topics": [TRANSFER_TOPIC0],
        }
        payload = {
            "jsonrpc": "2.0",
            "id": random.randint(1, 1_000_000),
            "method": "eth_getLogs",
            "params": [params],
        }
        data = self._post(payload, label=f"eth_getLogs {symbol} [{from_block}-{to_block}]", max_retries=MAX_RETRIES_LOGS)
        if isinstance(data, dict) and "error" in data:
            raise RuntimeError(f"eth_getLogs error: {data['error']}")
        return data.get("result", []) if isinstance(data, dict) else []

    def balance_batch(self, addr: str, tokens: Iterable[Token], snapshot_block: int) -> Dict[str, int]:
        calls = []
        order: List[str] = []
        for i, token in enumerate(tokens):
            order.append(token.key)
            calls.append({
                "jsonrpc": "2.0",
                "id": i + 1,
                "method": "eth_call",
                "params": [
                    {"to": token.address.lower(), "data": encode_balance_of(addr)},
                    to_hex_block(snapshot_block),
                ],
            })
        data = self._post(calls, label=f"balanceOf batch {addr}", max_retries=MAX_RETRIES_CALLS)
        if not isinstance(data, list):
            raise RuntimeError(f"balanceOf batch: unexpected response shape for {addr}: {data!r}")
        by_id = {item.get("id"): item for item in data if isinstance(item, dict)}
        result: Dict[str, int] = {}
        for i, key in enumerate(order):
            item = by_id.get(i + 1)
            if not item:
                result[key] = 0
                continue
            if "error" in item:
                raise RuntimeError(f"balanceOf {key} for {addr}: {item['error']}")
            result[key] = decode_uint256_hex(item.get("result"))
        return result


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------
def scan_token_transfer_recipients(rpc: Rpc, token: Token, start_block: int, snapshot_block: int) -> Set[str]:
    """Union of `to` addresses seen in Transfer logs across [start_block, snapshot_block]."""
    receivers: Set[str] = set()
    chunk = LOG_CHUNK_START
    current = start_block
    fail_streak = 0
    total = max(0, snapshot_block - start_block + 1)

    with tqdm(total=total, desc=f"logs {token.symbol}", unit="blk") as pbar:
        while current <= snapshot_block:
            end = min(snapshot_block, current + chunk - 1)
            try:
                logs = rpc.get_logs(token.address, current, end, token.symbol)
            except Exception as exc:
                fail_streak += 1
                if chunk <= LOG_CHUNK_MIN and fail_streak >= LOG_FAIL_STREAK_LIMIT:
                    raise RuntimeError(
                        f"{token.symbol}: {LOG_FAIL_STREAK_LIMIT} consecutive failures at min chunk; aborting"
                    ) from exc
                chunk = max(LOG_CHUNK_MIN, int(chunk * LOG_CHUNK_MULTIPLIER_DOWN))
                tprint(f"  {token.symbol}: getLogs failed near {current} ({exc}); chunk -> {chunk}")
                continue

            fail_streak = 0
            for log in logs:
                topics = log.get("topics", [])
                if len(topics) >= 3:
                    addr = normalize_address(topic_to_address(topics[2]))
                    if addr and addr != ZERO_ADDRESS:
                        receivers.add(addr)

            advanced = end - current + 1
            pbar.update(advanced)
            current = end + 1
            chunk = min(LOG_CHUNK_MAX, chunk + LOG_CHUNK_ADD)

    return receivers


def discover_addresses(rpc: Rpc, start_block: int, snapshot_block: int) -> Set[str]:
    seen: Set[str] = set()
    for token in TOKENS:
        before = len(seen)
        seen |= scan_token_transfer_recipients(rpc, token, start_block, snapshot_block)
        tprint(f"  {token.symbol}: +{len(seen) - before} new (running total: {len(seen)})")
    return seen


def fetch_balances(
    rpc: Rpc, addresses: Iterable[str], snapshot_block: int, workers: int = MAX_WORKERS
) -> Dict[str, Dict[str, int]]:
    address_list = list(addresses)
    out: Dict[str, Dict[str, int]] = {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(rpc.balance_batch, addr, TOKENS, snapshot_block): addr for addr in address_list}
        with tqdm(total=len(futures), desc="balanceOf", unit="addr") as pbar:
            for fut in as_completed(futures):
                addr = futures[fut]
                try:
                    out[addr] = fut.result()
                except Exception as exc:
                    tprint(f"  ! {addr}: {exc}")
                    out[addr] = {t.key: 0 for t in TOKENS}
                pbar.update(1)
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def write_master_csv(path: Path, balances: Dict[str, Dict[str, int]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["address", "knine_wei", "sknine_wei", "esknine_wei", "vknine_wei"])
        for addr in sorted(balances):
            b = balances[addr]
            # Skip rows where every balance is zero (e.g. contracts that received but
            # forwarded funds). A zero-row wouldn't be useful downstream.
            if not any(b.get(t.key, 0) for t in TOKENS):
                continue
            w.writerow([addr, b.get("knine", 0), b.get("sknine", 0), b.get("esknine", 0), b.get("vknine", 0)])
            rows += 1
    return rows


def write_airdrop_csv(path: Path, balances: Dict[str, Dict[str, int]], components: Tuple[str, ...]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["address", "amount"])
        for addr in sorted(balances):
            amount = sum(balances[addr].get(key, 0) for key in components)
            if amount <= 0:
                continue
            w.writerow([addr, amount])
            rows += 1
    return rows


def write_json_lookup(path: Path, snapshot_block: int, balances: Dict[str, Dict[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialisable = {
        "snapshotBlock": snapshot_block,
        "addresses": {
            addr: {
                "knine": str(b.get("knine", 0)),
                "sknine": str(b.get("sknine", 0)),
                "esknine": str(b.get("esknine", 0)),
                "vknine": str(b.get("vknine", 0)),
            }
            for addr, b in sorted(balances.items())
            if any(b.get(t.key, 0) for t in TOKENS)
        },
    }
    with path.open("w") as f:
        json.dump(serialisable, f, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: List[str]) -> argparse.Namespace:
    here = Path(__file__).resolve()
    default_out = here.parents[2] / "docs"
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rpc-url", default=os.environ.get("SHIBARIUM_RPC_URL", DEFAULT_RPC_URL))
    p.add_argument(
        "--snapshot-block",
        type=int,
        default=int(os.environ.get("SHIBARIUM_SUNSET_SNAPSHOT_BLOCK", DEFAULT_SNAPSHOT_BLOCK)),
    )
    p.add_argument(
        "--start-block",
        type=int,
        default=int(os.environ.get("SHIBARIUM_START_BLOCK", DEFAULT_START_BLOCK)),
    )
    p.add_argument("--out-dir", type=Path, default=default_out,
                   help="where to write CSV/JSON outputs (default: repo docs/)")
    p.add_argument("--workers", type=int, default=MAX_WORKERS,
                   help="parallel balanceOf workers")
    p.add_argument("--addresses-file", type=Path, default=None,
                   help="optional path to a newline-delimited address list to use INSTEAD of scanning Transfer logs")
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
    tprint(f"tokens:         {', '.join(t.symbol for t in TOKENS)}")

    rpc = Rpc(args.rpc_url)

    if args.addresses_file:
        tprint(f"\nStep 1/3: loading addresses from {args.addresses_file}")
        addresses: Set[str] = set()
        for line in args.addresses_file.read_text().splitlines():
            addr = normalize_address(line.strip())
            if addr:
                addresses.add(addr)
    else:
        tprint("\nStep 1/3: scanning Transfer logs for recipient addresses...")
        addresses = discover_addresses(rpc, args.start_block, args.snapshot_block)
    tprint(f"  {len(addresses):,} unique addresses")

    tprint("\nStep 2/3: fetching balanceOf at snapshot block...")
    balances = fetch_balances(rpc, addresses, args.snapshot_block, workers=args.workers)

    tprint("\nStep 3/3: writing outputs...")
    master = args.out_dir / "shibarium-snapshot-balances.csv"
    knine_airdrop = args.out_dir / "knine-airdrop.csv"
    esknine_airdrop = args.out_dir / "esknine-airdrop.csv"
    json_lookup = args.out_dir / "shibarium-snapshot-balances.json"

    master_rows = write_master_csv(master, balances)
    knine_rows = write_airdrop_csv(knine_airdrop, balances, ("knine", "sknine"))
    esknine_rows = write_airdrop_csv(esknine_airdrop, balances, ("esknine", "vknine"))
    write_json_lookup(json_lookup, args.snapshot_block, balances)

    tprint(f"  {master}       ({master_rows:,} rows)")
    tprint(f"  {knine_airdrop}         ({knine_rows:,} rows)")
    tprint(f"  {esknine_airdrop}       ({esknine_rows:,} rows)")
    tprint(f"  {json_lookup}")
    tprint("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
