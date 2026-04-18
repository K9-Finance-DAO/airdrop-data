# Base Mainnet — per-deployment template

Each Base mainnet airdrop lives in its own dated directory under `base-mainnet/`. Use the calendar date the snapshot was taken (or, if the snapshot date matters less than the deploy date, the deploy date — pick one and stay consistent).

## Directory structure

```
base-mainnet/<YYYY-MM>/
├── README.md                       # see "Per-deployment README contents" below
├── snapshot.csv                    # input recipient list: address,amount
├── airdropProofs.knine.json        # exact wizard output for KNINE
└── airdropProofs.esknine.json      # exact wizard output for esKNINE
```

If a deployment only ships one token, only include the file for that token.

## Per-deployment README contents

Suggested template:

```markdown
# <YYYY-MM> Base Mainnet Airdrop

## Snapshot
- Source chain: <e.g. Shibarium>
- Snapshot block: <number>
- Snapshot taken: <date>
- Generation script: scripts/<script>.py @ commit <sha>

## Contracts (Base mainnet, chainId 8453)
| Token   | TokenClaim address                                    | Merkle root                                          |
|---------|-------------------------------------------------------|------------------------------------------------------|
| KNINE   | 0x...                                                 | 0x...                                                |
| esKNINE | 0x...                                                 | 0x...                                                |

## Allocations
- Total recipients: <n>
- Total KNINE: <amount> KNINE
- Total esKNINE: <amount> esKNINE

## Claim window
- Opens (claimStart): <unix> (`<UTC date>`)
- Closes (claimEnd): <unix> (`<UTC date>`)

## Provenance / notes
<Anything anyone auditing this should know — eligibility rules, exclusion list,
manual adjustments, open issues, sign-offs, etc.>
```

## Pinning in the dapp

Once published, the dapp's `.env.local` should pin to the commit SHA, not `main`, so a later push cannot retroactively change what the live UI serves:

```bash
NEXT_PUBLIC_AIRDROP_KNINE_PROOFS_URL=https://raw.githubusercontent.com/K9-Finance-DAO/airdrop-data/<commit-sha>/base-mainnet/<YYYY-MM>/airdropProofs.knine.json
NEXT_PUBLIC_AIRDROP_ESKNINE_PROOFS_URL=https://raw.githubusercontent.com/K9-Finance-DAO/airdrop-data/<commit-sha>/base-mainnet/<YYYY-MM>/airdropProofs.esknine.json
```

## Never

- Never overwrite a published `airdropProofs.<token>.json`. If a correction is needed, create a new dated dir and update the dapp's env URLs to point at it.
- Never delete a published per-deployment dir. Audit-trail integrity depends on every claim window's data remaining reachable.
