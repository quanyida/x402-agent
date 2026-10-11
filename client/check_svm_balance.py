#!/usr/bin/env python3
"""Check devnet USDC balance + token account for a Solana address (sync)."""
import sys

from solana.rpc.api import Client
from solders.pubkey import Pubkey

USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCiD"  # devnet USDC


def main() -> None:
    addr = sys.argv[1]
    rpc = Client("https://api.devnet.solana.com")
    bal = rpc.get_balance(Pubkey.from_string(addr))
    print(f"SOL balance: {bal.value / 1e9:.6f}")
    resp = rpc.get_token_accounts_by_owner_json_parsed(
        Pubkey.from_string(addr),
        mint=Pubkey.from_string(USDC_MINT),
    )
    if resp.value:
        for acc in resp.value:
            info = acc.account.data.parsed["info"]
            print(f"USDC token account: {acc.pubkey}  balance={info['tokenAmount']['uiAmount']}")
    else:
        print("USDC token account: NONE")


if __name__ == "__main__":
    main()
