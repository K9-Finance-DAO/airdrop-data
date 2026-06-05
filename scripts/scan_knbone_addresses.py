#!/usr/bin/env python3
"""
Enrich a knBONE airdrop CSV with address metadata from Shibarium and Ethereum.

The input CSV is preserved. The output CSV keeps the input columns and appends:

    smart_contract_shibarium
    smart_contract_ethereum
    holds_zero_eth
    has_zero_ethereum_tx
    holds_less_than_5_bone
    holds_less_than_10_bone
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

import requests
from tqdm.auto import tqdm

from snapshot import DEFAULT_RPC_URL, HTTP_TIMEOUT_SECS, normalize_address, to_hex_block

DEFAULT_ETHEREUM_RPC_URL = "https://ethereum.publicnode.com"
DEFAULT_KNBONE_SNAPSHOT_BLOCK = 17_555_555
BONE_WEI = 10**18
OUTPUT_COLUMNS = (
    "smart_contract_shibarium",
    "smart_contract_ethereum",
    "holds_zero_eth",
    "has_zero_ethereum_tx",
    "holds_less_than_5_bone",
    "holds_less_than_10_bone",
)


class Rpc:
    def __init__(self, url: str) -> None:
        self.url = url
        self.session = requests.Session()

    def call(self, method: str, params: List[object], *, retries: int = 6) -> object:
        payload = {
            "jsonrpc": "2.0",
            "id": random.randint(1, 1_000_000),
            "method": method,
            "params": params,
        }
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.post(
                    self.url,
                    json=payload,
                    timeout=HTTP_TIMEOUT_SECS,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result")
            except Exception as exc:  # pragma: no cover - network flakiness
                last_err = exc
                if attempt == retries:
                    break
                time.sleep(min(15.0, 1.0 * (2 ** attempt)) + random.uniform(0, 0.35))
        raise RuntimeError(f"{method} failed after {retries + 1} attempts: {last_err}")


def parse_bool(value: bool) -> str:
    return "true" if value else "false"


def has_code(rpc: Rpc, address: str, block_tag: str) -> bool:
    code = rpc.call("eth_getCode", [address, block_tag])
    return bool(code and code != "0x")


def scan_address(address: str, shibarium_url: str, ethereum_url: str, shibarium_block: int) -> Dict[str, str]:
    shibarium = Rpc(shibarium_url)
    ethereum = Rpc(ethereum_url)
    shibarium_block_tag = to_hex_block(shibarium_block)

    eth_code = has_code(ethereum, address, "latest")
    shibarium_code = has_code(shibarium, address, shibarium_block_tag)
    bone_balance = int(str(shibarium.call("eth_getBalance", [address, shibarium_block_tag])), 16)
    eth_balance = int(str(ethereum.call("eth_getBalance", [address, "latest"])), 16)
    eth_tx_count = int(str(ethereum.call("eth_getTransactionCount", [address, "latest"])), 16)

    return {
        "smart_contract_shibarium": parse_bool(shibarium_code),
        "smart_contract_ethereum": parse_bool(eth_code),
        "holds_zero_eth": parse_bool(eth_balance == 0),
        "has_zero_ethereum_tx": parse_bool(eth_tx_count == 0),
        "holds_less_than_5_bone": parse_bool(bone_balance < 5 * BONE_WEI),
        "holds_less_than_10_bone": parse_bool(bone_balance < 10 * BONE_WEI),
    }


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", type=Path, default=Path("shibarium-snapshot/knbone-airdrop.csv"))
    p.add_argument("--output", type=Path, default=Path("shibarium-snapshot/knbone-airdrop-address-scan.csv"))
    p.add_argument("--shibarium-rpc-url", default=os.environ.get("SHIBARIUM_RPC_URL", DEFAULT_RPC_URL))
    p.add_argument("--ethereum-rpc-url", default=os.environ.get("ETHEREUM_RPC_URL", DEFAULT_ETHEREUM_RPC_URL))
    p.add_argument(
        "--shibarium-block",
        type=int,
        default=int(os.environ.get("KNBONE_SNAPSHOT_BLOCK", DEFAULT_KNBONE_SNAPSHOT_BLOCK)),
        help="block tag for Shibarium smart-contract detection",
    )
    p.add_argument("--workers", type=int, default=16)
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    with args.input.open(newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise SystemExit(f"{args.input} has no rows")
    if "address" not in rows[0]:
        raise SystemExit(f"{args.input} must contain an address column")

    addresses = []
    for row in rows:
        address = normalize_address(row.get("address"))
        if not address:
            raise SystemExit(f"invalid address in {args.input}: {row.get('address')!r}")
        row["address"] = address
        addresses.append(address)

    scanned: Dict[str, Dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                scan_address,
                address,
                args.shibarium_rpc_url,
                args.ethereum_rpc_url,
                args.shibarium_block,
            ): address
            for address in addresses
        }
        with tqdm(total=len(futures), desc="scan addresses", unit="addr") as pbar:
            for fut in as_completed(futures):
                address = futures[fut]
                scanned[address] = fut.result()
                pbar.update(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) + [col for col in OUTPUT_COLUMNS if col not in rows[0]]
    with args.output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            row.update(scanned[row["address"]])
            writer.writerow(row)

    print(f"wrote {args.output} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
