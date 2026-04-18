# K9 Finance — Airdrop Data

Public, version-controlled record of every K9 Finance airdrop's recipient list and merkle proofs, plus the python tooling used to compute the snapshots.

License: **CC0 1.0** (public domain). See `LICENSE`.

## Why this repo exists

- **Transparency.** Every published allocation is checked in to git history. Anyone can independently verify that a given JSON's merkle root matches what the on-chain `TokenClaim` contract enforces.
- **Immutability.** Date-versioned directories never get overwritten. If a published file is wrong, a new dated dir is created and the consuming dapp is updated to point at it. Old paths keep working for anyone who pinned them.
- **Source-of-truth for the dapp.** The K9 claim UI fetches its proofs JSON from raw URLs in this repo. No CDN, no backend — just static files served by GitHub.

## Layout

```
.
├── LICENSE                                 # CC0 1.0
├── README.md                               # this file
├── .gitignore
├── scripts/                                # snapshot tooling (python)
│   ├── README.md                           # how to run the snapshot scripts
│   ├── requirements.txt
│   ├── snapshot.py                         # primary snapshot generator
│   └── knbone_snapshot.py
├── shibarium-snapshot/                     # input source data: Shibarium balance snapshot used as the airdrop basis
│   ├── shibarium-snapshot-balances.csv
│   ├── shibarium-snapshot-balances.json
│   ├── knine-airdrop.csv                   # recipient list for KNINE airdrop
│   ├── knbone-airdrop.csv
│   └── esknine-airdrop.csv                 # recipient list for esKNINE airdrop
├── base-mainnet/                           # per-deployment dirs go here
│   └── TEMPLATE.md                         # shows the per-deployment dir structure
└── base-sepolia/                           # testnet allocations (same structure)
    └── TEMPLATE.md
```

A real per-deployment dir looks like:

```
base-mainnet/2026-04/
├── README.md                               # contract addresses, merkle roots, snapshot block, contributor notes
├── snapshot.csv                            # the recipient,amount input list
├── airdropProofs.knine.json                # exact wizard output for KNINE
└── airdropProofs.esknine.json              # exact wizard output for esKNINE
```

See `base-mainnet/TEMPLATE.md` for the full per-deployment template.

## How the dapp consumes this

The K9 dapp's claim page fetches two single-token proof JSONs in parallel — one per token — using two env vars:

```bash
# packages/nextjs/.env.local in the based-dog repo
NEXT_PUBLIC_AIRDROP_KNINE_PROOFS_URL=https://raw.githubusercontent.com/K9-Finance-DAO/airdrop-data/main/base-mainnet/2026-04/airdropProofs.knine.json
NEXT_PUBLIC_AIRDROP_ESKNINE_PROOFS_URL=https://raw.githubusercontent.com/K9-Finance-DAO/airdrop-data/main/base-mainnet/2026-04/airdropProofs.esknine.json

# Optional: explicit github.com blob URLs shown as "View allocations on GitHub"
# footer links under each claim card. If omitted, the dapp derives them from
# the raw URLs above.
NEXT_PUBLIC_AIRDROP_KNINE_ALLOCATIONS_GITHUB_URL=https://github.com/K9-Finance-DAO/airdrop-data/blob/main/base-mainnet/2026-04/airdropProofs.knine.json
NEXT_PUBLIC_AIRDROP_ESKNINE_ALLOCATIONS_GITHUB_URL=https://github.com/K9-Finance-DAO/airdrop-data/blob/main/base-mainnet/2026-04/airdropProofs.esknine.json
```

Pinning policy: prefer pinning to a commit SHA (`raw.githubusercontent.com/K9-Finance-DAO/airdrop-data/<sha>/...`) over `main` for production deployments, so a later edit to `main` cannot change what the live dapp serves.

JSON shape (matches the dapp's deploy wizard `lib/merkle.ts createDownloadableProofs` output):

```json
{
  "root": "0x...",
  "totalAmount": "...",
  "recipientCount": 12345,
  "timestamp": "2026-04-18T00:00:00.000Z",
  "proofs": [
    { "address": "0xabc...", "amount": "100000000000000000000", "proof": ["0x...", "0x..."], "index": 0 }
  ]
}
```

`amount` is wei as a decimal string. `proof` is an array of 0x-prefixed 32-byte hex hashes (the `bytes32[]` argument to `claim()`).

## How to verify allocations independently

For any published `airdropProofs.<token>.json`:

1. Read the `merkleRoot` (or `root`) field from the JSON.
2. Read `merkleRoot()` from the on-chain `TokenClaim` contract listed in the per-deployment README.
3. Confirm they match.
4. Optionally: re-derive the merkle tree from the `proofs[].address` + `amount` rows using the same hashing scheme (`keccak256(abi.encodePacked(address, uint256))`) and confirm the recomputed root also matches.

The python tooling in `scripts/` includes a `verify` mode that does steps 1, 3, and 4 in one command (see `scripts/README.md`).

## Adding a new airdrop

1. Generate the snapshot from the upstream chain via `scripts/snapshot.py`.
2. Run the K9 deploy wizard (in the K9 dapp) to upload the recipient CSVs and download two single-token `airdropProofs.<token>.json` files.
3. Create a new dated dir under `base-mainnet/` (or `base-sepolia/` for testnet).
4. Add a `README.md` to that dir documenting the contract addresses, merkle roots, snapshot block, and provenance.
5. Commit and push.
6. Update the dapp's env vars to point at the new files (pinned to the commit SHA).

## Maintainer

K9 Finance DAO. See git history for individual contributors.
