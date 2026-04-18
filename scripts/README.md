# Shibarium sunset snapshot

Captures per-user balances of KNINE, sKNINE, esKNINE, and vKNINE on Shibarium at
the sunset snapshot block and emits the CSVs the airdrop wizard expects.

## Outputs (written to `docs/` at the repo root)

| File                                 | Columns                                                            |
| ------------------------------------ | ------------------------------------------------------------------ |
| `shibarium-snapshot-balances.csv`    | `address, knine_wei, sknine_wei, esknine_wei, vknine_wei`          |
| `knine-airdrop.csv`                  | `address, amount` — `amount = KNINE + sKNINE` (wei)                |
| `esknine-airdrop.csv`                | `address, amount` — `amount = esKNINE + vKNINE` (wei)              |
| `shibarium-snapshot-balances.json`   | `{ snapshotBlock, addresses: { [addr]: { knine, sknine, ... } } }` |

The two airdrop CSVs match the format the wizard's upload step accepts
(`packages/nextjs/lib/csv.ts`). Amounts are raw uint256 wei decimal strings.

## Run

```bash
cd scripts/shibarium-snapshot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# KNINE / sKNINE / esKNINE / vKNINE snapshot → 3 CSVs + JSON
python snapshot.py

# knBONE snapshot → knbone-airdrop.csv
python knbone_snapshot.py
```

The defaults match the liquid-staking frontend's env: RPC
`https://shibarium.drpc.org`, snapshot block `15876116`, start block `1`.
Override via flags or env vars:

```bash
python snapshot.py \
    --rpc-url https://shibarium.drpc.org \
    --snapshot-block 15876116 \
    --start-block 1 \
    --workers 32

# knBONE uses its own block env var so it can be re-run later with a
# different snapshot block without touching the KNINE config.
KNBONE_SNAPSHOT_BLOCK=15876116 python knbone_snapshot.py
```

### Env vars

| Var                            | Purpose                                              | Default      |
| ------------------------------ | ---------------------------------------------------- | ------------ |
| `SHIBARIUM_RPC_URL`            | JSON-RPC endpoint for both scripts                   | shib-rpc2    |
| `SHIBARIUM_START_BLOCK`        | first block scanned for Transfer logs                | `1`          |
| `SHIBARIUM_SUNSET_SNAPSHOT_BLOCK` | snapshot block for `snapshot.py` (KNINE family)    | `15876116`   |
| `KNBONE_SNAPSHOT_BLOCK`        | snapshot block for `knbone_snapshot.py` (placeholder until a real block is chosen) | `15876116` |

## Approach

Pure JSON-RPC (no `web3.py` / `ethers`):

1. `eth_getLogs` per token for the `Transfer` topic across
   `[start_block, snapshot_block]`, using AIMD block-range chunking (5k start,
   +500 on success, ×0.8 on failure, 25-fail kill switch).
2. Union all `to` addresses (topic[2]) across the four tokens, minus `0x0`.
3. One JSON-RPC batch per address with four `eth_call balanceOf(addr)` entries
   at `blockTag = snapshot_block`, parallelised with a 32-worker thread pool.
4. Write CSVs + JSON.

Ported from `/workspace/k9-bigquery-shibarium-notebooks/Snapshot Data - RPC.ipynb`
with sKNINE added and the output shape adapted to this repo's airdrop wizard.

## Note on archive nodes

Scanning from block 1 requires an archive-capable RPC (pruned nodes lose state
beyond ~128 blocks). Both `shib-rpc2.shib.army` and `shibarium.drpc.org` work
for this.
