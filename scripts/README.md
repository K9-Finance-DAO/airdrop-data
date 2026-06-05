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
cd scripts
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# KNINE / sKNINE / esKNINE / vKNINE snapshot → 3 CSVs + JSON
python snapshot.py

# knBONE snapshot → knbone-airdrop.csv
python knbone_snapshot.py

# knBONE address metadata scan → knbone-airdrop-address-scan.csv
python scan_knbone_addresses.py \
    --input ../shibarium-snapshot/knbone-airdrop.csv \
    --output ../shibarium-snapshot/knbone-airdrop-address-scan.csv \
    --shibarium-block 17555555
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

`knbone_snapshot.py` writes `<out-dir>/knbone-scan-checkpoint.json` while
scanning logs. If the process is interrupted or the RPC fails, rerun the same
command and it resumes from the checkpoint's `nextBlock`. Use `--no-resume` to
force a fresh scan. For RPCs that reject larger `eth_getLogs` ranges, cap the
range with `--log-chunk-start`, `--log-chunk-add`, and `--log-chunk-max`.

`scan_knbone_addresses.py` preserves the input CSV and writes an enriched copy
with `smart_contract_shibarium`, `smart_contract_ethereum`, `holds_zero_eth`,
and `has_zero_ethereum_tx`. Shibarium contract detection uses `--shibarium-block`;
Ethereum checks use latest state from `ETHEREUM_RPC_URL`.

### Env vars

| Var                            | Purpose                                              | Default      |
| ------------------------------ | ---------------------------------------------------- | ------------ |
| `SHIBARIUM_RPC_URL`            | JSON-RPC endpoint for both scripts                   | shib-rpc2    |
| `SHIBARIUM_START_BLOCK`        | first block scanned for Transfer logs                | `1`          |
| `SHIBARIUM_SUNSET_SNAPSHOT_BLOCK` | snapshot block for `snapshot.py` (KNINE family)    | `15876116`   |
| `KNBONE_SNAPSHOT_BLOCK`        | snapshot block for `knbone_snapshot.py` (placeholder until a real block is chosen) | `15876116` |
| `KNBONE_LOG_CHUNK_START`       | initial knBONE `eth_getLogs` block range size        | `5000`       |
| `KNBONE_LOG_CHUNK_ADD`         | knBONE block range increase after successful log calls | `500`      |
| `KNBONE_LOG_CHUNK_MAX`         | maximum knBONE `eth_getLogs` block range size        | `25000`      |

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
