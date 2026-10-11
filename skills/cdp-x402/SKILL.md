---
name: cdp-x402
description: Explores x402 HTTP payment protocol — lists supported schemes, discovers resources from the bazaar, constructs and signs payment payloads for EVM and Solana.
---

# cdp-x402

x402 HTTP payment protocol: supported schemes, bazaar discovery, payment construction and verification.

## Supported schemes

```
cdp x402 supported
```
Returns `.kinds` with available network/scheme combinations.

## Discover resources from bazaar

```
cdp api /x402/discovery/resources
```
Filter for target network from the response items.

## Verify payment (seller-side)

```
cdp x402 verify paymentPayload:=<json> paymentRequirements:=<json> x402Version:=2
```

## Settle payment (seller-side)

```
cdp x402 settle paymentPayload:=<json> paymentRequirements:=<json> x402Version:=2
```

## Buyer flow (pay for a 402 resource)

Uses consistent variable names across EVM and Solana: `addr`, `payload`, `sig`, `header`.

### EVM flow

```bash
# 1. Resolve account address
addr=$(cdp evm accounts by-name name=$ACCOUNT | jq -r .address)

# 2. Build EIP-712 typed data (client-side, no API call)
payload=$(cdp util x402 build --from $addr --payment-requirements "$REQ")

# 3. Sign typed data via CDP (extract fields from build output)
D=$(echo "$payload" | jq -c .domain)
T=$(echo "$payload" | jq -c .types)
P=$(echo "$payload" | jq -r .primaryType)
M=$(echo "$payload" | jq -c .message)
sig=$(cdp evm accounts sign typed-data $addr \
  "domain:=$D" "types:=$T" "primaryType=$P" "message:=$M" | jq -r .signature)

# 4. Encode payment header (client-side)
header=$(cdp util x402 encode --payment-requirements "$REQ" \
  --signature "$sig" --authorization "$M")

# 5. Make the paid request (send both headers for v1/v2 compatibility)
curl -H "X-PAYMENT: $header" -H "PAYMENT-SIGNATURE: $header" <resourceUrl>
```

For `upto` scheme, override amount in step 2:
```bash
payload=$(cdp util x402 build --from $addr --payment-requirements "$REQ" --amount 50000)
```

### Solana flow

```bash
# 1. Resolve account address
addr=$(cdp solana accounts by-name name=$ACCOUNT | jq -r .address)

# 2. Build partially-signed transaction (client-side)
payload=$(cdp util x402 build --from $addr --payment-requirements "$REQ")

# 3. Sign transaction via CDP
sig=$(cdp solana accounts sign transaction $addr \
  transaction="$(echo $payload | jq -r .transaction)" | jq -r .signedTransaction)

# 4. Encode payment header (client-side, no --authorization needed for Solana)
header=$(cdp util x402 encode --payment-requirements "$REQ" --signature "$sig")

# 5. Make the paid request
curl -H "X-PAYMENT: $header" -H "PAYMENT-SIGNATURE: $header" <resourceUrl>
```

### Decode a payment header

```bash
cdp util x402 decode <base64-header>
```

## x402 utils reference

```
cdp util x402 build   --from <addr> --payment-requirements '<json>' [--amount N]
cdp util x402 encode  --payment-requirements '<json>' --signature <sig> --authorization '<json>' [--x402-version N]
cdp util x402 decode  <base64-header>
```

- `build` detects chain from network field (eip155:* or v1 names like base, solana)
- `encode` defaults to v1 payload format. Use `--x402-version 2` for v2 servers (those using PAYMENT-REQUIRED header)
- `decode` is chain-agnostic
- All utils are client-side only (no API calls)

## Notes

- `verify` and `settle` are for resource servers (sellers), not buyers.
- Buyer flow uses `cdp util x402` utils + resource signing commands.
- CDP facilitator: `https://api.cdp.coinbase.com/platform/v2/x402` (all networks, needs API key).
- x402.org facilitator: testnet only, no signup.
- Some servers use `X-PAYMENT`, others use `PAYMENT-SIGNATURE` header. Send both for compatibility.
- POST endpoints (e.g. Nansen) require `-X POST -H "Content-Type: application/json" -d '<body>'`.

## Network support

| Network | Facilitator |
|---------|-------------|
| Base / Base Sepolia | CDP or x402.org (testnet) |
| Polygon | CDP |
| Solana / Solana Devnet | CDP or x402.org (testnet) |
