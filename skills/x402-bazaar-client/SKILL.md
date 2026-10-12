---
name: x402-bazaar-client
description: |
  Discover x402 services from the Coinbase CDP Bazaar registry and pay for them automatically —
  with a NEAR account (chain signatures, no CDP needed) OR a CDP-managed wallet.
  Use when the user wants to find real, live x402 APIs (registry), list them by network/price/tags,
  inspect what a service expects as input, and call it with automatic USDC payment.
  Two signer modes: `--signer near` (pay from your own NEAR account via MPC chain signatures,
  auto-funds the derived EVM address through 1Click when needed) or `--signer cdp` (default,
  CDP-managed wallet). Flow: `x402-bazaar list` -> `inspect <n>` -> `call <n> [--signer near]`.
  Network is locked to testnets unless the user explicitly asks for mainnet in the current turn.
compatibility: Python >= 3.10. NEAR mode needs py-near; CDP mode needs CDP API key + wallet secret (CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET). Proxy env (HTTP_PROXY/HTTPS_PROXY) honored when CDP/registry APIs are not directly reachable.
metadata:
  author: quanyida
  version: "0.2.0"
---

# x402 Bazaar Client — discover & pay for registry services

Walks the user from "what x402 services exist?" to "I paid $0.01 and got the result" in three
commands. **Supports paying with your own NEAR account** (no CDP signup needed) or a CDP wallet.

## Two ways to pay

| Mode | Who signs | What you need | Best for |
|---|---|---|---|
| `--signer near` | Your NEAR account via MPC chain signatures | NEAR account + private key (in `.env`) | Users who already have NEAR; no CDP account needed |
| `--signer cdp` (default) | CDP-managed EVM wallet | CDP API key + wallet secret | Developers already on Coinbase CDP |

## Quick start (NEAR mode — recommended for newcomers)

1. Install Python deps:

   ```bash
   pip install -r requirements.txt        # or: pip install py-near dotenv httpx x402[evm,svm,httpx] cdp-sdk
   ```

2. Copy the config template and fill in **your own** NEAR account + private key:

   ```bash
   cp .env.example .env        # Windows: copy .env.example .env
   ```

   `.env` fields:

   ```
   NEAR_ACCOUNT_ID=youraccount.near
   NEAR_PRIVATE_KEY=ed25519:...
   # optional: NEAR_RPC_URL=https://free.rpc.fastnear.com
   ```

   > ⚠️ Your private key stays local. Never commit `.env`, never paste it in chat.

3. List available services and pick one:

   ```bash
   python scripts/x402_bazaar.py list --network eip155:84532 --limit 10
   python scripts/x402_bazaar.py inspect 0
   ```

4. Pay with your NEAR account (auto-converts NEAR → USDC on the service's chain if needed):

   ```bash
   python scripts/x402_bazaar.py call 0 --signer near --input '{"requestKey":"my-key-001","tokenAddress":"0x..."}' --yes
   ```

   Or without `--input` for GET endpoints.

## When not to use

- **Calling your own / a known URL** (no registry lookup needed): use `build-x402-client` directly.
- **Building a paid server**: use `build-x402-server`.
- **Exploring the protocol / constructing payments by hand**: use `cdp-x402`.

## Hard rules

1. **Never pay on mainnet unless the user asks in the current turn.** Default networks are
   testnets only (`eip155:84532` Base Sepolia, `eip155:80002` Polygon Amstoy, `solana:901` Devnet).
   Registry resources on mainnet (`eip155:8453` Base, `eip155:137` Polygon, `solana:101`) are shown
   in listings but **must not be called** without an explicit mainnet go-ahead.
2. **One resource = one paid call.** Never retry a call with the same payment header; a second
   identical call would be rejected by nonce replay protection (and would waste USDC).
3. **Show the price and payee before paying.** Every `call` prints the amount + payTo + network and
   asks the user to confirm (interactive) before signing, unless the user passed `--yes`.

## Commands

```
x402-bazaar list      [--network eip155:84532|eip155:80002|solana:901|all] [--tag <t>] [--limit N] [--mainnet]
x402-bazaar inspect   <index>            # from the last list output (0-based)
x402-bazaar call      <index> [--input '<json>'] [--yes] [--signer cdp|near]
x402-bazaar my-wallet [--signer cdp|near]
```

- `list` pulls from `https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources` (public,
  no auth), prints a numbered table: index, serviceName, network, price, tags, l30DaysTotalCalls.
  Mainnet resources are marked `[MAINNET]` and excluded from `call` until `--mainnet`.
- `inspect` prints the full record: resource URL, description, accepts (exact amount, payTo, asset),
  and the Bazaar input schema/example — what body the paid POST/GET needs.
- `call` performs the buyer flow: GET the resource with no payment header first -> parse
  `Payment-Required` -> register the scheme from the 402 requirements -> auto pay -> replay -> print
  status + body. `--signer near` signs via your NEAR account (MPC chain signatures) and auto-funds
  the derived EVM address through 1Click when the NEAR-side balance can cover it.
- `my-wallet` prints the payer address + on-chain USDC balance.

## NEAR signer specifics (scripts/near_signer.py)

`near_signer.py` wraps the official `py-near` SDK (`pvolnov/py-near`): it manages the NEAR account,
derives an EVM address via MPC (default path `ethereum-1`), signs the EIP-712 x402 digest
(`keccak("\x19\x01" + header + body)` — verified byte-for-byte against the x402 server), and when
the derived address lacks USDC, calls 1Click (`1click.chaindefuser.com`) to convert NEAR/wNEAR/USDC
on NEAR into USDC on the destination EVM chain. RPC default `https://free.rpc.fastnear.com`
(reachable from mainland China without a proxy).

Config precedence: CLI flags (`--near-account`, `--near-key`, `--near-key-file`) > `.env`
(`NEAR_ACCOUNT_ID`, `NEAR_PRIVATE_KEY`, `NEAR_RPC_URL`).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `list` shows 0 items | Registry unreachable / proxy missing | Check `curl -x <proxy> https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources` |
| `call` fails 402 repeatedly | Wallet has no USDC on that network | `x402-bazaar my-wallet --signer near`, then check NEAR balance / 1Click auto-fund log |
| NEAR `--signer near` says missing account/key | `.env` not loaded or fields empty | Fill `NEAR_ACCOUNT_ID` + `NEAR_PRIVATE_KEY` in `.env`, or pass `--near-account`/`--near-key-file` |
| NEAR RPC timeout (`rpc.mainnet.near.org`) | Default RPC unreachable from your network | Set `NEAR_RPC_URL=https://free.rpc.fastnear.com` in `.env` |
| Python TypeError mixing sync/async | `x402Client` vs `x402ClientSync` mismatch | Use async `x402HttpxClient` + `x402Client` (this skill's default) |
| CDP auth timeout | `Urllib3AuthClient` ignores env proxy | Proxy patch applied at import (see build-x402-client); set HTTP(S)_PROXY |

## Examples

```bash
# List testnet services
x402-bazaar list --network eip155:84532 --limit 10

# Inspect the first one
x402-bazaar inspect 0

# Pay with your NEAR account (auto-funds USDC on Base Sepolia if needed)
x402-bazaar call 0 --signer near --input '{"requestKey":"my-key-001","tokenAddress":"0x..."}' --yes
```

## References

- 1Click API: https://1click.chaindefuser.com/docs/v0/openapi.yaml
- py-near: https://github.com/pvolnov/py-near
- x402 docs: https://docs.cdp.coinbase.com/x402/buyer/client-configuration
