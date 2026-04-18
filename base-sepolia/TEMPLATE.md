# Base Sepolia — per-deployment template

Same structure as `../base-mainnet/TEMPLATE.md`, just on chainId `84532` for testnet validation.

## Directory structure

```
base-sepolia/<YYYY-MM>/
├── README.md
├── snapshot.csv
├── airdropProofs.knine.json
└── airdropProofs.esknine.json
```

## Per-deployment README

Use the same template as `../base-mainnet/TEMPLATE.md` but with the testnet contract addresses on chainId `84532`.

## Pinning in the dapp

Testnet builds typically pin to `main` (or a branch) rather than a commit SHA, since testnet allocations are expected to change as a feature is iterated on:

```bash
NEXT_PUBLIC_AIRDROP_KNINE_PROOFS_URL=https://raw.githubusercontent.com/K9-Finance-DAO/airdrop-data/main/base-sepolia/<YYYY-MM>/airdropProofs.knine.json
NEXT_PUBLIC_AIRDROP_ESKNINE_PROOFS_URL=https://raw.githubusercontent.com/K9-Finance-DAO/airdrop-data/main/base-sepolia/<YYYY-MM>/airdropProofs.esknine.json
```

If you do publish a testnet snapshot you intend to keep stable for a long-running QA cycle, treat it like mainnet and pin to a commit SHA.
