#!/usr/bin/env python3
"""Setup a local Solana keypair funded with devnet USDC via a CDP-managed account.

Flow: create CDP Solana account -> request devnet SOL+USDC faucet -> transfer USDC to
a local keypair that we control the private key for (so x402 ExactSvmScheme can sign).
Saves the keypair secret to .svm_keypair.json next to this script.
"""
import asyncio
import json
import os
import sys

# ---- proxy patch for CDP SDK (must run before cdp import) ----
def _patch_urllib3_proxy() -> None:
    try:
        import urllib3
        from urllib3.poolmanager import ProxyManager
        from cdp.auth.clients.urllib3.client import Urllib3AuthClient
        _orig_init = Urllib3AuthClient.__init__

        def _patched_init(self, *args, **kwargs):
            _orig_init(self, *args, **kwargs)
            proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            if proxy:
                self.client = ProxyManager(proxy)
        Urllib3AuthClient.__init__ = _patched_init
    except Exception:
        pass


def _load_env() -> None:
    for cand in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        p = os.path.join(cand, ".env")
        if os.path.isfile(p):
            try:
                from dotenv import load_dotenv
                load_dotenv(p)
            except Exception:
                pass
            break


WALLET_NAME = "x402-client-wallet-1"
KEYFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".svm_keypair.json")


async def main() -> None:
    from cdp import CdpClient
    from solders.keypair import Keypair

    # 1. local keypair (payer for x402)
    if os.path.isfile(KEYFILE):
        with open(KEYFILE) as f:
            kp = Keypair.from_bytes(bytes.fromhex(json.load(f)["secret"]))
    else:
        kp = Keypair()
        with open(KEYFILE, "w") as f:
            json.dump({"address": str(kp.pubkey()), "secret": bytes(kp).hex()}, f, indent=2)
        print(f"[setup] new local keypair saved -> {KEYFILE}")
    print(f"[setup] local payer   : {kp.pubkey()}")

    async with CdpClient() as cdp:
        # 2. CDP-managed solana account
        acc = await cdp.solana.get_or_create_account(name=WALLET_NAME)
        print(f"[setup] cdp solana   : {acc.address}")

        # 3. faucet SOL + USDC on devnet
        try:
            r = await acc.request_faucet("sol")
            print(f"[setup] SOL faucet    : {r}")
        except Exception as e:
            print(f"[setup] SOL faucet failed: {e}")
        try:
            r = await acc.request_faucet("usdc")
            print(f"[setup] USDC faucet   : {r}")
        except Exception as e:
            print(f"[setup] USDC faucet failed: {e}")

        # 4. transfer 5 USDC (5_000_000 atomic, 6 decimals) to local keypair
        try:
            tx = await acc.transfer(str(kp.pubkey()), 1_500_000, "usdc", "devnet")
            print(f"[setup] USDC transfer tx: {tx}")
        except Exception as e:
            print(f"[setup] USDC transfer failed: {e}")


if __name__ == "__main__":
    _load_env()
    _patch_urllib3_proxy()
    asyncio.run(main())
