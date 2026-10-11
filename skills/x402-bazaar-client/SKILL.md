---
name: x402-bazaar-client
description: |
  Discover x402 services from the Coinbase CDP Bazaar registry and pay for them automatically.
  Use when the user wants to find real, live x402 APIs (registry), list them by network/price/tags,
  inspect what a service expects as input, and call it with automatic USDC payment from a
  CDP-managed wallet. Combines Bazaar discovery (cdp-x402) with the buyer flow (build-x402-client):
  `x402-bazaar list`  ->  `x402-bazaar inspect <n>`  ->  `x402-bazaar call <n> [--input <json>]`.
  Network is locked to testnets (Base Sepolia / Polygon Amstoy / Solana Devnet) unless the user
  explicitly asks for mainnet in the current turn.
compatibility: Requires CDP API key + wallet secret in env (CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET). Python >= 3.10 with cdp-sdk and x402[evm,svm,httpx]. Proxy env (HTTP_PROXY/HTTPS_PROXY) needed when CDP API is not directly reachable.
metadata:
  author: user
  version: "0.1.0"
---

# x402 Bazaar Client — discover & pay for registry services

Walks the user from "what x402 services exist?" to "I paid $0.01 and got the result" in three
commands, using the CDP Bazaar registry as the source of services.

## When not to use

- **Calling your own / a known URL** (no registry lookup needed): use `build-x402-client` directly.
- **Building a paid server**: use `build-x402-server`.
- **Exploring the protocol / constructing payments by hand**: use `cdp-x402`.
- **Buying with a raw private key instead of CDP**: out of scope; migrate to CDP wallet first.

## Hard rules

1. **Never pay on mainnet unless the user asks in the current turn.** Default networks are
   testnets only (`eip155:84532` Base Sepolia, `eip155:80002` Polygon Amstoy, `solana:901` Devnet).
   Registry resources on mainnet (`eip155:8453` Base, `eip155:137` Polygon, `solana:101`) are shown
   in listings but **must not be called** without an explicit mainnet go-ahead.
2. **One resource = one paid call.** The skill never retries a call with the same payment header.
   If the first call returned 200, the job is done; a second identical call would be rejected by
   nonce replay protection (and would be a waste of USDC).
3. **Show the price and payee before paying.** Every `call` prints the amount + payTo + network and
   asks the user to confirm (interactive) before signing, unless the user passed `--yes`.

## Commands

```
x402-bazaar list      [--network eip155:84532|eip155:80002|solana:901|all] [--tag <t>] [--limit N] [--mainnet]
x402-bazaar inspect   <index>            # from the last list output (0-based)
x402-bazaar call      <index> [--input '<json>'] [--yes]
x402-bazaar my-wallet
```

- `list` pulls from `https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources` (public,
  no auth), prints a numbered table: index, serviceName, network, price (from accepts[0]), tags,
  l30DaysTotalCalls. Filters: exact network string (prefix match), tag substring, limit.
  Mainnet resources are shown with a `[MAINNET]` marker and excluded from `call` until `--mainnet`.
- `inspect` prints the full record: resource URL, description, accepts (exact amount, payTo, asset),
  and the Bazaar input schema/example (`extensions.bazaar.info.input`), which tells you exactly
  what body the paid POST/GET needs.
- `call` performs the buyer flow: resolve CDP wallet (`x402-client-wallet-1`, create if missing),
  GET the resource with no payment header first -> parse `Payment-Required` -> register the scheme
  from the 402 requirements -> auto pay -> replay with payment header -> print status + body.
  If the resource is a POST with a JSON body per `inspect`, `--input` supplies the body.
- `my-wallet` prints the payer address + on-chain USDC balance on Base Sepolia (Blockscout).

## Python implementation (scripts/x402_bazaar.py)

Single-file async script; the buyer path mirrors `build-x402-client` Python flow:

```python
async with CdpClient() as cdp:
    account = await cdp.evm.get_or_create_account(name="x402-client-wallet-1")
    signer = EthAccountSigner(EvmLocalAccount(account))
    payment = x402Client()
    # register schemes for all networks that may appear in the 402 response:
    for net in ("eip155:84532", "eip155:80002", "solana:901"):
        payment.register(net, ExactEvmScheme(signer))
    async with x402HttpxClient(payment) as http:
        r = await http.get(url)          # first call -> 402 + Payment-Required
        await r.aread()
        # x402HttpxClient auto-settles and replays; r is the final response
```

Key adaptations from the base pattern:
- The wallet name is fixed (`x402-client-wallet-1`) so balances accumulate in one known payer.
- Discovery list is fetched with plain `httpx` against the public registry endpoint (no CDP auth).
- For POST resources, the body comes from `extensions.bazaar.info.input.body` (idempotency key
  `requestKey` is caller-chosen; reuse the same key for the same logical request to avoid a second
  payment).
- Proxy: if `HTTP_PROXY`/`HTTPS_PROXY` env vars are set, `httpx` uses them (trust_env default);
  the CDP SDK needs the `Urllib3AuthClient` proxy patch from build-x402-client (applied in script
  before any CDP call).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `list` shows 0 items | Registry unreachable / proxy missing | Check `curl -x <proxy> https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources` |
| `call` fails 402 repeatedly | Wallet has no USDC on that network | `x402-bazaar my-wallet`, fund via CDP faucet or manual transfer, wait for confirm |
| Python TypeError mixing sync/async | `x402Client` vs `x402ClientSync` mismatch | Use async `x402HttpxClient` + `x402Client` (this skill's default) |
| "No scheme registered for network" | 402 network not in registered set | Ensure all three testnet networks registered before call |
| CDP auth timeout | `Urllib3AuthClient` ignores env proxy | Proxy patch applied at import (see build-x402-client) |

## Examples

```
x402-bazaar list --network eip155:84532 --limit 10
x402-bazaar inspect 0
x402-bazaar call 0 --input '{"requestKey":"my-key-001","tokenAddress":"0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"}' --yes
```
