#!/usr/bin/env python3
"""
x402-bazaar-client — discover x402 services in the CDP Bazaar registry and pay for them.

Commands:
  list      [--network <eip155:84532|eip155:80002|solana:901|all>] [--tag <t>] [--limit N] [--mainnet]
  inspect   <index>
  call      <index> [--input '<json>'] [--yes] [--signer cdp|near] [--near-account ..] [--near-key ..]
  my-wallet [--signer cdp|near] ...

Signers:
  cdp   (default) — CDP-managed EVM wallet (requires CDP_API_KEY_ID/SECRET, CDP_WALLET_SECRET)
  near           — NEAR account via chain signatures (MPC). Payments still settle as USDC
                   on the EVM chain, signed by your NEAR account. If the derived EVM address
                   lacks USDC, `call` auto-funds it from NEAR assets through 1Click (disable
                   with --no-fund).

Requires env: CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET (cdp signer).
NEAR signer needs: --near-account <id> and --near-key <base58 private key> (or --near-key-file).
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

try:
    from near_signer import NearChainSigner  # same dir (run as `python scripts/x402_bazaar.py`)
except ImportError:
    from scripts.near_signer import NearChainSigner  # package-style import

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
    near = _near_from_args(args)
    tx = asyncio.run(_pay_and_call(
        url, net, method, body, query, svm_keyfile,
        signer_mode=getattr(args, "signer", "cdp"),
        near=near,
        do_fund=not getattr(args, "no_fund", False),
        amount_usd=amount,
    ))
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
    near = _near_from_args(args)
    tx = asyncio.run(_pay_and_call(
        url, net, method, body, query, svm_keyfile,
        signer_mode=getattr(args, "signer", "cdp"),
        near=near,
        do_fund=not getattr(args, "no_fund", False),
        amount_usd=getattr(args, "amount", 0.0) or 0.0,
    ))
    print(f"    paid. tx={tx}")


async def _pay_and_call(
    url: str,
    net: str,
    method: str,
    body: dict | None,
    query: dict | None = None,
    svm_keyfile: str | None = None,
    signer_mode: str = "cdp",
    near: dict | None = None,
    do_fund: bool = True,
    amount_usd: float = 0.0,
) -> str:
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
    elif signer_mode == "near":
        # NEAR chain-signature signer: EVM payment signed by a NEAR account.
        if not near or not near.get("account") or not near.get("key"):
            print("[error] --signer near requires --near-account and --near-key (or --near-key-file).")
            sys.exit(4)
        is_mainnet, oneclick_chain = _NET_META.get(net, (False, None))
        signer = NearChainSigner(
            account_id=near["account"],
            private_key=near["key"],
            network="mainnet" if is_mainnet else "testnet",
            path=near.get("path") or "ethereum-1",
            rpc_url=near.get("rpc"),
            proxy=proxy_url,
        )
        print(f"    paying from: {signer.address}  (NEAR {near['account']} via MPC)")
        if do_fund and is_mainnet and oneclick_chain and amount_usd > 0:
            print(f"    [fund] checking USDC on {oneclick_chain} for {signer.address} ...")
            need = amount_usd
            have = _usdc_balance_on(net, signer.address)
            if have < need:
                print(f"    [fund] balance ${have:.4f} < ${need:.4f}; auto-funding from NEAR assets (1Click) ...")
                try:
                    res = await signer.ensure_usdc_on_chain(
                        oneclick_chain, need,
                        check_balance_fn=lambda a: _usdc_balance_on(net, a),
                    )
                    print(f"    [fund] {res}")
                except Exception as e:
                    print(f"    [fund] FAILED: {e}")
                    print("           proceeding anyway — payment will likely fail if USDC missing.")
            else:
                print(f"    [fund] ok (${have:.4f})")
        elif do_fund and not is_mainnet:
            print("    [fund] testnet: no auto-fund (fund the derived address manually)")
        elif not do_fund:
            print("    [fund] skipped (--no-fund)")
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


def _usdc_balance_on(net: str, address: str) -> float:
    """USDC balance on the specific x402 network (testnet + mainnet EVM).

    Mainnet EVM chains use JSON-RPC eth_call against the USDC contract
    (Blockscout mainnet endpoints are unreachable from CN and return 0,
    which previously forced a redundant 1Click top-up on every call).
    """
    # (rpc, usdc contract) for mainnet chains; eth_call is the authoritative path
    _MAINNET_USDC = {
        "eip155:8453": ("https://mainnet.base.org", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"),
        "eip155:1": ("https://eth.llamarpc.com", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
        "eip155:137": ("https://polygon-rpc.com", "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"),
        "eip155:42161": ("https://arb1.arbitrum.io/rpc", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"),
        "eip155:10": ("https://mainnet.optimism.io", "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85"),
    }
    m = _MAINNET_USDC.get(net)
    if m:
        try:
            import httpx
            rpc, usdc = m
            data = "0x70a08231" + address.lower().removeprefix("0x").rjust(64, "0")
            r = httpx.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": usdc, "data": data}, "latest"]}, timeout=15)
            out = r.json().get("result")
            if out and out != "0x":
                return int(out, 16) / 1e6
        except Exception:
            pass
        # fall through to blockscout on failure
    api = {
        "eip155:84532": "https://base-sepolia.blockscout.com",
        "eip155:80002": "https://amoy.polygonscan.com",
        "eip155:8453": "https://base.blockscout.com",
        "eip155:137": "https://polygon.blockscout.com",
        "eip155:1": "https://eth.blockscout.com",
        "eip155:42161": "https://arbitrum.blockscout.com",
        "eip155:10": "https://optimism.blockscout.com",
    }.get(net)
    if not api:
        return 0.0
    import urllib.request

    try:
        url = f"{api}/api/v2/addresses/{address}/token-balances"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        for entry in data:
            token = entry.get("token", {})
            if token.get("symbol") == "USDC":
                decimals = int(token.get("decimals", 6))
                return int(entry.get("value", 0)) / (10**decimals)
    except Exception:
        pass
    return 0.0


# network -> (is_mainnet, 1Click target chain name for USDC)
_NET_META = {
    "eip155:84532": (False, None),
    "eip155:80002": (False, None),
    "eip155:8453": (True, "base"),
    "eip155:137": (True, "polygon"),
    "eip155:1": (True, "ethereum"),
    "eip155:42161": (True, "arbitrum"),
    "eip155:10": (True, "optimism"),
}


def cmd_my_wallet(args: argparse.Namespace | None = None) -> None:
    if args is not None and getattr(args, "signer", "cdp") == "near":
        near = _near_from_args(args)
        if not near or not near.get("account") or not near.get("key"):
            print("[error] --signer near requires --near-account and --near-key (or --near-key-file).")
            sys.exit(4)
        network = getattr(args, "near_network", "mainnet")
        signer = NearChainSigner(
            near["account"], near["key"],
            network=network, path=near["path"], rpc_url=near.get("rpc"),
        )
        net = "eip155:8453" if network == "mainnet" else "eip155:84532"
        print(f"payer signer : NEAR ({near['account']}) via MPC (path={near['path']})")
        print(f"derived EVM  : {signer.address}  ({_net_label(net)})")
        bal = _usdc_balance_on(net, signer.address)
        print(f"USDC balance : ${bal:.6f}  ({net})")
        print("note: payments settle as USDC on the EVM chain; the NEAR account only signs.")
        return

    async def _run():
        async with CdpClient() as cdp:
            account = await cdp.evm.get_or_create_account(name=WALLET_NAME)
            print(f"payer wallet : {WALLET_NAME}")
            print(f"address      : {account.address}")
            bal, net = _chain_usdc_balance(account.address)
            print(f"USDC balance : ${bal:.6f}  ({net})")
    asyncio.run(_run())


# ------------------------------------------------------------------- main ---

def _near_from_args(args: argparse.Namespace) -> dict | None:
    """Extract NEAR signer config (None unless --signer near).

    Precedence: CLI args > NEAR_ACCOUNT_ID / NEAR_PRIVATE_KEY env (.env).
    This lets users configure once in .env and run --signer near without flags.
    """
    if getattr(args, "signer", "cdp") != "near":
        return None
    key = getattr(args, "near_key", None)
    if not key and getattr(args, "near_key_file", None):
        with open(args.near_key_file, "r", encoding="utf-8") as f:
            key = f.read().strip()
    account = getattr(args, "near_account", None) or os.environ.get("NEAR_ACCOUNT_ID")
    if not key:
        key = os.environ.get("NEAR_PRIVATE_KEY")
    if key and ":" not in key and key.startswith("ed25519"):
        # tolerate env values missing the ed25519: prefix
        key = f"ed25519:{key}"
    return {
        "account": account,
        "key": key,
        "path": getattr(args, "near_path", "ethereum-1"),
        "rpc": getattr(args, "near_rpc", None) or os.environ.get("NEAR_RPC_URL"),
    }


def _add_near_args(p) -> None:
    p.add_argument("--signer", choices=["cdp", "near"], default="cdp",
                   help="payment signer: cdp (default, CDP wallet) or near (NEAR chain signatures)")
    p.add_argument("--near-account", default=None, help="NEAR account id (--signer near)")
    p.add_argument("--near-key", default=None, help="NEAR private key base58 (--signer near; prefer --near-key-file)")
    p.add_argument("--near-key-file", default=None, help="file containing NEAR private key base58")
    p.add_argument("--near-path", default="ethereum-1", help="derivation path (default ethereum-1)")
    p.add_argument("--near-rpc", default=None, help="custom NEAR RPC URL")
    p.add_argument("--no-fund", action="store_true",
                   help="disable 1Click auto-funding of USDC onto the derived EVM address")


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
    _add_near_args(pc)
    pc.set_defaults(fn=cmd_call)

    pu = sub.add_parser("call-url", help="pay & call an arbitrary x402 URL (e.g. your own server)")
    pu.add_argument("url", help="resource URL, e.g. http://127.0.0.1:8402/report/AAOI")
    pu.add_argument("--network", default="eip155:84532", help="network to register for payment (default eip155:84532)")
    pu.add_argument("--input", default=None, help="JSON body for POST resources")
    pu.add_argument("--input-file", default=None, help="path to a JSON file with POST body (PowerShell-safe)")
    pu.add_argument("--query-file", default=None, help="path to a JSON file with query params (PowerShell-safe)")
    pu.add_argument("--svm-keyfile", default=None, help="path to Solana keypair JSON for SVM payments")
    pu.add_argument("--amount", type=float, default=0.0, help="expected payment amount in USDC (used for auto-fund)")
    pu.add_argument("--yes", action="store_true", help="skip confirmation (still blocked on mainnet)")
    pu.add_argument("--mainnet", action="store_true", help="explicit mainnet go-ahead")
    _add_near_args(pu)
    pu.set_defaults(fn=cmd_call_url)

    pw = sub.add_parser("my-wallet", help="show payer wallet")
    pw.add_argument("--near-network", choices=["mainnet", "testnet"], default="mainnet",
                    help="which chain the derived EVM address belongs to (--signer near)")
    _add_near_args(pw)
    pw.set_defaults(fn=cmd_my_wallet)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
