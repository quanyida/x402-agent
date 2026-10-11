#!/usr/bin/env python3
"""Query Solana devnet via plain requests (proxy-aware) for tx status + token accounts."""
import json
import sys

import requests

RPC = "https://api.devnet.solana.com"
USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"


def rpc(method: str, params: list) -> dict:
    r = requests.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=60)
    r.raise_for_status()
    return r.json()


def main() -> None:
    addr = sys.argv[1]
    if len(sys.argv) > 2:
        tx = sys.argv[2]
        print("--- tx status ---")
        print(json.dumps(rpc("getTransaction", [tx, {"encoding": "json", "maxSupportedTransactionVersion": 0}]), indent=2, ensure_ascii=False)[:1500])
    print("--- balance ---")
    print(json.dumps(rpc("getBalance", [addr]), indent=2))
    print("--- USDC token accounts ---")
    print(json.dumps(rpc("getTokenAccountsByOwner", [addr, {"mint": USDC_MINT}, {"encoding": "jsonParsed"}]), indent=2, ensure_ascii=False)[:2500])


if __name__ == "__main__":
    main()
