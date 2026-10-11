#!/usr/bin/env python3
"""
x402-bazaar-client — discover x402 services in the CDP Bazaar registry and pay for them.

Commands:
  list      [--network <eip155:84532|eip155:80002|solana:901|all>] [--tag <t>] [--limit N] [--mainnet]
  inspect   <index>
  call      <index> [--input '<json>'] [--yes]
  my-wallet

Requires env: CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET.
Proxy: uses HTTP_PROXY / HTTPS_PROXY env vars (httpx trust_env + urllib3 patch).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from urllib.parse import urlparse

# ---- env: load .env if present (CDP_API_KEY_ID etc.) ----
import os as _os
_env_found = None
for _cand in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
    _p = os.path.join(_cand, ".env")
    if os.path.isfile(_p):
        _env_found = _p
        break
if _env_found:
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_found)
    except Exception:
        pass
if not os.environ.get("CDP_API_KEY_ID"):
    print("[warn] CDP_API_KEY_ID not set; call/list still work, my-wallet/call will fail", file=sys.stderr)

# ---- proxy patch for CDP SDK's urllib3 client (must run before cdp import) ----
def _patch_urllib3_proxy() -> None:
    """Make Urllib3AuthClient honor HTTP(S)_PROXY by swapping its internal client."""
    try:
        import urllib3
        from urllib3.poolmanager import ProxyManager
        from cdp.auth.clients.urllib3.client import Urllib3AuthClient

        _orig_init = Urllib3AuthClient.__init__

        def _patched_init(self, options, base_url, debug=False):
            _orig_init(self, options, base_url, debug)
            proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            if proxy_url:
                self.client = ProxyManager(proxy_url=proxy_url)

        Urllib3AuthClient.__init__ = _patched_init
    except Exception as e:  # pragma: no cover - patch is best-effort
        print(f"[warn] urllib3 proxy patch failed: {e}", file=sys.stderr)


_patch_urllib3_proxy()

import httpx  # noqa: E402
from cdp import CdpClient  # noqa: E402
from cdp.evm_local_account import EvmLocalAccount  # noqa: E402
from x402 import x402Client  # noqa: E402
from x402.http.clients import x402HttpxClient  # noqa: E402
from x402.mechanisms.evm import EthAccountSigner  # noqa: E402
from x402.mechanisms.evm.exact import ExactEvmScheme  # noqa: E402

REGISTRY = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"
WALLET_NAME = "x402-client-wallet-1"

# Testnet networks we are allowed to pay on by default.
TESTNET_NETS = {
    "eip155:84532": "Base Sepolia",
    "eip155:80002": "Polygon Amstoy",
    "solana:901": "Solana Devnet",
    "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp": "Solana Devnet",  # devnet genesis hash
}
MAINNET_NETS = {
    "eip155:8453": "Base",
    "eip155:137": "Polygon",
    "eip155:1": "Ethereum",
    "eip155:42161": "Arbitrum",
    "eip155:10": "Optimism",
    "solana:101": "Solana",
    "solana:EtWTRABAAAABQ7TtLezq1v5VcKjSvi4Y1v8MxjL9kFvE": "Solana",  # mainnet genesis hash
}


def _net_label(net: str) -> str:
    return TESTNET_NETS.get(net) or MAINNET_NETS.get(net) or net


def _fetch_registry() -> list[dict]:
    with httpx.Client(trust_env=True, timeout=30) as c:
        r = c.get(REGISTRY)
        r.raise_for_status()
        data = r.json()
    return data.get("items", [])


def _pretty_price(accepts: list) -> str:
    if not accepts:
        return "?"
    a = accepts[0]
    amt = int(a.get("amount", 0))
    return f"${amt / 1_000_000:.4f}" if amt else "?"


# ---------------------------------------------------------------- commands ---

def _filter_items(items: list[dict], net_f: str | None, tag_f: str | None, mainnet: bool) -> list[dict]:
    """Apply the same filters in list / inspect / call so display indices match."""
    rows = []
    for it in items:
        net = it["accepts"][0]["network"] if it.get("accepts") else "?"
        if net_f and not net.startswith(net_f):
            continue
        if net in MAINNET_NETS and not mainnet:
            continue  # hide mainnet unless --mainnet (hard rule)
        tags = ",".join(it.get("tags", []) or [])
        if tag_f and tag_f not in tags.lower() and tag_f not in (it.get("serviceName") or "").lower():
            continue
        rows.append(it)
    return rows


def _resolve_index(args: argparse.Namespace) -> dict:
    items = _fetch_registry()
    net_f = args.network if getattr(args, "network", "all") != "all" else None
    tag_f = getattr(args, "tag", None)
    rows = _filter_items(items, net_f, tag_f, getattr(args, "mainnet", False))
    if args.index >= len(rows):
        print(f"index {args.index} out of range (only {len(rows)} matched). Re-run `list` with same filters.")
        sys.exit(1)
    return rows[args.index]

def cmd_list(args: argparse.Namespace) -> None:
    items = _fetch_registry()
    net_f = args.network if args.network != "all" else None
    rows = _filter_items(items, net_f, args.tag, args.mainnet)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("no resources matched")
        return
    print(f"{'#':>3}  {'service':<28} {'network':<20} {'price':>8}  calls/30d  tags")
    for i, it in enumerate(rows):
        net = it["accepts"][0]["network"] if it.get("accepts") else "?"
        q = it.get("quality", {}) or {}
        print(
            f"{i:>3}  {it.get('serviceName','')[:28]:<28} {_net_label(net):<20} "
            f"{_pretty_price(it.get('accepts')):>8}  {q.get('l30DaysTotalCalls','?'):>9}  "
            f"{','.join(it.get('tags',[]) or [])[:40]}"
        )
    print(f"\n{len(rows)} resource(s). Use `inspect <n>` for details, `call <n>` to pay & call.")


def cmd_inspect(args: argparse.Namespace) -> None:
    it = _resolve_index(args)
    print(f"serviceName : {it.get('serviceName')}")
    print(f"resource    : {it.get('resource')}")
    print(f"type        : {it.get('type')}  x402Version={it.get('x402Version')}")
    print(f"description : {it.get('description')}")
    print(f"lastUpdated : {it.get('lastUpdated')}")
    q = it.get("quality", {}) or {}
    print(f"calls/30d   : {q.get('l30DaysTotalCalls')}  uniquePayers/30d: {q.get('l30DaysUniquePayers')}")
    print("\naccepts:")
    print(json.dumps(it.get("accepts", []), indent=2, ensure_ascii=False))
    info = ((it.get("extensions") or {}).get("bazaar") or {}).get("info") or {}
    if info:
        print("\nbazaar input (what the paid call expects):")
        print(json.dumps(info, indent=2, ensure_ascii=False))


def cmd_call(args: argparse.Namespace) -> None:
    it = _resolve_index(args)
    accepts = it.get("accepts", [])
    if not accepts:
        print("resource has no accepts; cannot pay"); sys.exit(1)
    net = accepts[0]["network"]
    if net in MAINNET_NETS and not args.mainnet:
        print(f"[BLOCKED] {net} is mainnet. Hard rule: no mainnet payment unless --mainnet. Aborting.")
        sys.exit(2)
    if net not in TESTNET_NETS:
        print(f"[warn] network {net} not in known testnet set; proceeding (you asked).")

    url = it["resource"]
    desc = (it.get("description") or "")[:80]
    a0 = accepts[0]
    amount = int(a0.get("amount", 0)) / 1_000_000
    print(f"\n>>> Pay {a0.get('scheme')} ${amount:.4f} USDC on {_net_label(net)} ({net})")
    print(f"    payTo: {a0.get('payTo')}")
    print(f"    url  : {url}")
    print(f"    desc : {desc}")
    if not args.yes:
        ans = input("    confirm payment? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("aborted.")
            sys.exit(3)

    body = None
    query = None
    if args.input:
        body = json.loads(args.input)
    elif args.input_file:
        with open(args.input_file, "r", encoding="utf-8") as f:
            body = json.load(f)
    elif args.query:
        query = json.loads(args.query)
    elif args.query_file:
        with open(args.query_file, "r", encoding="utf-8") as f:
            query = json.load(f)
    else:
        info = ((it.get("extensions") or {}).get("bazaar") or {}).get("info") or {}
        inp = info.get("input") or {}
        if inp.get("type") == "http" and inp.get("method") in ("POST", "post"):
            print("    [info] resource is POST but no --input given; sending empty JSON body")
            body = {}
        elif inp.get("queryParams"):
            print(f"    [info] resource expects GET query params: {json.dumps(inp['queryParams'], ensure_ascii=False)}")
            print("           pass them with --query '{...}'")

    method = "POST" if body is not None else "GET"
    svm_keyfile = getattr(args, "svm_keyfile", None)
    tx = asyncio.run(_pay_and_call(url, net, method, body, query, svm_keyfile))
    print(f"    paid. tx={tx}")


def cmd_call_url(args: argparse.Namespace) -> None:
    """Pay & call an arbitrary x402-protected URL (not necessarily in the registry)."""
    net = args.network
    if net in MAINNET_NETS and not args.mainnet:
        print(f"[BLOCKED] {net} is mainnet. Hard rule: no mainnet payment unless --mainnet. Aborting.")
        sys.exit(2)
    url = args.url
    print(f"\n>>> Pay on {_net_label(net)} ({net})")
    print(f"    url  : {url}")
    if not args.yes:
        ans = input("    confirm payment? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("aborted."); sys.exit(3)
    body = None
    query = None
    if args.input:
        body = json.loads(args.input)
    elif args.input_file:
        with open(args.input_file, "r", encoding="utf-8") as f:
            body = json.load(f)
    elif args.query_file:
        with open(args.query_file, "r", encoding="utf-8") as f:
            query = json.load(f)
    method = "POST" if body is not None else "GET"
    svm_keyfile = getattr(args, "svm_keyfile", None)
    tx = asyncio.run(_pay_and_call(url, net, method, body, query, svm_keyfile))
    print(f"    paid. tx={tx}")


async def _pay_and_call(url: str, net: str, method: str, body: dict | None, query: dict | None = None, svm_keyfile: str | None = None) -> str:
    if query:
        from urllib.parse import urlencode
        url = url + ("&" if "?" in url else "?") + urlencode(query)
    # x402AsyncTransport defaults to httpx.AsyncHTTPTransport() with NO proxy.
    # Inject a proxy-aware underlying transport so external (non-localhost) resources work.
    import httpx
    from x402.http.clients import x402_httpx_transport

    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    base_transport = (
        httpx.AsyncHTTPTransport(proxy=proxy_url, trust_env=True)
        if proxy_url
        else httpx.AsyncHTTPTransport(trust_env=True)
    )

    payment = x402Client()

    if net.startswith("solana:"):
        # SVM payment: local keypair signer (no CDP needed)
        from solders.keypair import Keypair
        from x402.mechanisms.svm.exact import ExactSvmScheme
        from x402.mechanisms.svm.signers import KeypairSigner

        if svm_keyfile is None:
            svm_keyfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".svm_keypair.json")
        if not os.path.isfile(svm_keyfile):
            print(f"[error] SVM keypair file not found: {svm_keyfile}")
            print("        run setup_svm.py first (creates local keypair + devnet USDC)")
            sys.exit(4)
        import json as _json
        with open(svm_keyfile, "r", encoding="utf-8") as f:
            secret = _json.load(f)["secret"]
        kp = Keypair.from_bytes(bytes.fromhex(secret))
        signer = KeypairSigner(kp)
        print(f"    paying from: {kp.pubkey()} (solana)")
        try:
            payment.register(net, ExactSvmScheme(signer))
        except Exception as e:
            print(f"[error] SVM scheme register failed: {e}")
            sys.exit(4)
    else:
        async with CdpClient() as cdp:
            account = await cdp.evm.get_or_create_account(name=WALLET_NAME)
            signer = EthAccountSigner(EvmLocalAccount(account))
            print(f"    paying from: {signer.address}")
            # register every network that may appear; only the matching one gets used
            # (testnets + Base mainnet; mainnet payments are gated by --mainnet above)
            nets = [
                "eip155:84532", "eip155:80002", "eip155:8453",
                "solana:901", "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
            ]
            if net not in nets:
                nets.append(net)
            for n in nets:
                try:
                    payment.register(n, ExactEvmScheme(signer))
                except Exception:
                    pass
    async with httpx.AsyncClient(
        transport=x402_httpx_transport(payment, base_transport),
        trust_env=True,
        timeout=httpx.Timeout(60.0, connect=30.0),
    ) as http:
        if method == "POST":
            resp = await http.post(url, json=body or {})
        else:
            resp = await http.get(url)
            await resp.aread()
        print(f"    HTTP {resp.status_code}")
        hdr = resp.headers.get("Payment-Response")
        tx = ""
        if hdr:
            try:
                tx = json.loads(hdr).get("transaction", "")
            except Exception:
                pass
        body_txt = resp.text[:2000]
        try:
            print(json.dumps(json.loads(body_txt), indent=2, ensure_ascii=False))
        except Exception:
            print(body_txt)
        return tx


def _chain_usdc_balance(address: str) -> float:
    """Query on-chain USDC balance via Blockscout (SDK balance API is unreliable)."""
    import urllib.request
    for net, api in (
        ("base-sepolia", "https://base-sepolia.blockscout.com"),
        ("polygon-amoy", "https://amoy.polygonscan.com"),
    ):
        try:
            url = f"{api}/api/v2/addresses/{address}/token-balances"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            for entry in data:
                token = entry.get("token", {})
                if token.get("symbol") == "USDC":
                    decimals = int(token.get("decimals", 6))
                    return int(entry.get("value", 0)) / (10**decimals), net
        except Exception:
            continue
    return 0.0, "unknown"


def cmd_my_wallet() -> None:
    async def _run():
        async with CdpClient() as cdp:
            account = await cdp.evm.get_or_create_account(name=WALLET_NAME)
            print(f"payer wallet : {WALLET_NAME}")
            print(f"address      : {account.address}")
            bal, net = _chain_usdc_balance(account.address)
            print(f"USDC balance : ${bal:.6f}  ({net})")
    asyncio.run(_run())


# ------------------------------------------------------------------- main ---

def main() -> None:
    p = argparse.ArgumentParser(prog="x402-bazaar", description="Discover & pay x402 registry services")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="list registry resources")
    pl.add_argument("--network", default="all", help="prefix filter: eip155:84532 | eip155:80002 | solana:901 | all")
    pl.add_argument("--tag", default=None, help="tag substring filter")
    pl.add_argument("--limit", type=int, default=None, help="max rows")
    pl.add_argument("--mainnet", action="store_true", help="also show mainnet resources (never pay without explicit ask)")
    pl.set_defaults(fn=cmd_list)

    pi = sub.add_parser("inspect", help="inspect one resource")
    pi.add_argument("index", type=int)
    pi.add_argument("--network", default="all", help="same filter as list (align indices)")
    pi.add_argument("--tag", default=None, help="same filter as list")
    pi.add_argument("--mainnet", action="store_true", help="show mainnet resources too")
    pi.set_defaults(fn=cmd_inspect)

    pc = sub.add_parser("call", help="pay & call one resource")
    pc.add_argument("index", type=int)
    pc.add_argument("--network", default="all", help="same filter as list (align indices)")
    pc.add_argument("--tag", default=None, help="same filter as list")
    pc.add_argument("--input", default=None, help="JSON body for POST resources")
    pc.add_argument("--input-file", default=None, help="path to a JSON file with POST body (PowerShell-safe)")
    pc.add_argument("--query", default=None, help="JSON query params for GET resources (avoid in PowerShell; quotes get stripped)")
    pc.add_argument("--query-file", default=None, help="path to a JSON file with query params (PowerShell-safe)")
    pc.add_argument("--svm-keyfile", default=None, help="path to Solana keypair JSON for SVM payments")
    pc.add_argument("--yes", action="store_true", help="skip confirmation (still blocked on mainnet)")
    pc.add_argument("--mainnet", action="store_true", help="explicit mainnet go-ahead")
    pc.set_defaults(fn=cmd_call)

    pu = sub.add_parser("call-url", help="pay & call an arbitrary x402 URL (e.g. your own server)")
    pu.add_argument("url", help="resource URL, e.g. http://127.0.0.1:8402/report/AAOI")
    pu.add_argument("--network", default="eip155:84532", help="network to register for payment (default eip155:84532)")
    pu.add_argument("--input", default=None, help="JSON body for POST resources")
    pu.add_argument("--input-file", default=None, help="path to a JSON file with POST body (PowerShell-safe)")
    pu.add_argument("--query-file", default=None, help="path to a JSON file with query params (PowerShell-safe)")
    pu.add_argument("--svm-keyfile", default=None, help="path to Solana keypair JSON for SVM payments")
    pu.add_argument("--yes", action="store_true", help="skip confirmation (still blocked on mainnet)")
    pu.add_argument("--mainnet", action="store_true", help="explicit mainnet go-ahead")
    pu.set_defaults(fn=cmd_call_url)

    pw = sub.add_parser("my-wallet", help="show payer wallet")
    pw.set_defaults(fn=lambda a: cmd_my_wallet())

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
