#!/usr/bin/env python3
"""
near_signer.py — NEAR Chain Signatures signer for x402 (official py-near SDK).

Implements the x402 `ClientEvmSigner` protocol (address + sign_typed_data) using a NEAR
account as the signer via the NEAR MPC contract (`v1.signer`), plus a 1Click (NEAR Intents)
auto-funding helper that injects USDC onto the derived EVM address from NEAR assets.

NEAR interaction is delegated to the official `py-near` SDK (pip install py-near).
A private event-loop thread hosts the async py-near Account so the synchronous x402
signer protocol and the async 1Click path share one SDK instance.

Dependencies: py-near (official), httpx, eth_account, eth_keys.

Usage (inside x402_bazaar.py):
    signer = NearChainSigner(account_id=..., private_key=..., network="mainnet")
    payment.register("eip155:8453", ExactEvmScheme(signer))   # same call site as CDP
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

# --------------------------------------------------------------------------- #
# secp256k1 point arithmetic (only needed for child-key derivation)            #
# --------------------------------------------------------------------------- #
SECP_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
SECP_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
_G = (GX, GY)


def _pt_add(p1, p2):
    """Jacobian-free affine point addition on secp256k1 (None = infinity)."""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % SECP_P == 0:
            return None
        lam = (3 * x1 * x1) * pow(2 * y1, SECP_P - 2, SECP_P) % SECP_P
    else:
        lam = (y2 - y1) * pow(x2 - x1, SECP_P - 2, SECP_P) % SECP_P
    x3 = (lam * lam - x1 - x2) % SECP_P
    y3 = (lam * (x1 - x3) - y1) % SECP_P
    return (x3, y3)


def _pt_mul(k: int, p=_G):
    k %= SECP_N
    r = None
    q = p
    while k:
        if k & 1:
            r = _pt_add(r, q)
        q = _pt_add(q, q)
        k >>= 1
    return r


# --------------------------------------------------------------------------- #
# NEAR constants                                                                #
# --------------------------------------------------------------------------- #
RPC_MAINNET = "https://rpc.mainnet.near.org"
RPC_TESTNET = "https://rpc.testnet.near.org"
MPC_MAINNET = "v1.signer"
MPC_TESTNET = "v1.signer-prod.testnet"


def _pick_rpc(proxy: str | None = None, testnet: bool = False) -> str:
    """First reachable NEAR RPC (mainnet rpc.near.org is blocked in some regions)."""
    candidates = [RPC_TESTNET] if testnet else [RPC_MAINNET, "https://free.rpc.fastnear.com"]
    for u in candidates:
        try:
            with httpx.Client(proxy=proxy, timeout=5) as c:
                c.post(u, json={"jsonrpc": "2.0", "id": "x", "method": "block", "params": {"finality": "final"}}, timeout=5)
            return u
        except Exception:
            continue
    return RPC_TESTNET if testnet else RPC_MAINNET


# --------------------------------------------------------------------------- #
# py-near based NEAR account                                                    #
# --------------------------------------------------------------------------- #
class PyNearAccount:
    """Official py-near Account hosted on a private event-loop thread.

    Exposes both sync entry points (x402 signer protocol is synchronous) and async
    entry points (1Click auto-fund path), sharing one Account instance / one loop.
    """

    def __init__(self, account_id: str, private_key_b58: str, network: str = "mainnet", proxy: str | None = None, rpc_url: str | None = None):
        from py_near.account import Account

        self.account_id = account_id
        self.rpc = rpc_url or _pick_rpc(proxy=proxy, testnet=(network != "mainnet"))
        self._key = private_key_b58.strip()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="py-near-loop")
        self._thread.start()
        self._acc = Account(account_id, self._key, rpc_addr=self.rpc)
        self._startup_fut = self._submit(self._acc.startup())

    # ---- loop plumbing ----------------------------------------------------- #
    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _ready(self):
        if self._startup_fut is not None:
            self._startup_fut.result()  # ensure SDK startup completed on its loop
        return None

    def sync(self, coro) -> Any:
        """Run a py-near coroutine synchronously (blocking)."""
        self._ready()
        return self._submit(coro).result()

    async def call(self, coro) -> Any:
        """Run a py-near coroutine and await from the caller's (async) context."""
        self._ready()
        return await asyncio.wrap_future(self._submit(coro))

    # ---- views ------------------------------------------------------------- #
    def view(self, contract: str, method: str, args: dict | None = None) -> Any:
        """Sync view_function -> parsed JSON result."""
        res = self.sync(self._acc.view_function(contract, method, args or {}))
        return getattr(res, "result", None)

    async def view_async(self, contract: str, method: str, args: dict | None = None) -> Any:
        res = await self.call(self._acc.view_function(contract, method, args or {}))
        return getattr(res, "result", None)

    async def ft_balance(self, token_contract: str) -> int:
        """NEP-141 balance (atomic units)."""
        res = await self.call(self._acc.view_function(token_contract, "ft_balance_of", {"account_id": self.account_id}))
        return int(getattr(res, "result", None) or 0)

    async def near_balance(self) -> int:
        """Native NEAR balance (yoctoNEAR)."""
        return int(await self.call(self._acc.get_balance()))

    # ---- tx ---------------------------------------------------------------- #
    async def function_call(self, contract: str, method: str, args: dict, gas: int, amount: int):
        return await self.call(self._acc.function_call(contract, method, args, gas=gas, amount=amount))

    def function_call_sync(self, contract: str, method: str, args: dict, gas: int, amount: int):
        return self.sync(self._acc.function_call(contract, method, args, gas=gas, amount=amount))

    async def multi_action(self, receiver: str, actions: list):
        return await self.call(self._acc.sign_and_submit_tx(receiver, actions))

    def close(self):
        try:
            if self._startup_fut is not None:
                self._startup_fut.result(timeout=5)
            self._submit(self._acc.shutdown()).result(timeout=5)
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# NEAR Chain Signatures signer (x402 ClientEvmSigner protocol)                 #
# --------------------------------------------------------------------------- #
class NearChainSigner:
    """NEAR account signing EIP-712 typed data via MPC — drop-in for EthAccountSigner.

    Exposes:
      .address            -> derived EVM address (0x...)  [the wallet that must hold USDC]
      .sign_typed_data(...) -> 65-byte EVM signature
    """

    def __init__(
        self,
        account_id: str,
        private_key: str,
        network: str = "mainnet",
        path: str = "ethereum-1",
        mpc_contract: str | None = None,
        rpc_url: str | None = None,
        proxy: str | None = None,
    ):
        self._acct = PyNearAccount(account_id, private_key, network=network, proxy=proxy, rpc_url=rpc_url)
        self.network = network
        self.path = path
        self.mpc = mpc_contract or (MPC_MAINNET if network == "mainnet" else MPC_TESTNET)
        self.rpc = rpc_url or self._acct.rpc
        self._root_pub: bytes | None = None  # 64-byte uncompressed secp256k1 (no 0x04)
        self._address: str | None = None

    # ---- derivation ------------------------------------------------------ #
    def _fetch_root_public_key(self) -> bytes:
        if self._root_pub is None:
            res = self._acct.view(self.mpc, "public_key", {})
            if res is None:
                raise RuntimeError(f"MPC {self.mpc} public_key returned nothing (contract exists?)")
            pk = res.get("public_key", res) if isinstance(res, dict) else res
            if isinstance(pk, str):
                pk = pk.split(":")[-1]          # strip "secp256k1:" / "ed25519:" prefix
                import base58
                pk = base58.b58decode(pk)
            pk = bytes(pk)
            if len(pk) == 65 and pk[0] == 0x04:
                pk = pk[1:]
            if len(pk) != 64:
                raise RuntimeError(f"unexpected MPC public key length: {len(pk)}")
            self._root_pub = pk
        return self._root_pub

    def derive_address(self) -> str:
        """EVM address via the official NEAR epsilon-derivation (chainsig.js compatible).

        scalar = sha3_256("near-mpc-recovery v0.1.0 epsilon derivation:<account>,<path>")
        child  = root_point + scalar * G ;  address = keccak256(x||y)[-20:]
        """
        if self._address is not None:
            return self._address
        root = self._fetch_root_public_key()
        from eth_utils import keccak

        eps = f"near-mpc-recovery v0.1.0 epsilon derivation:{self._acct.account_id},{self.path}".encode()
        scalar = int.from_bytes(hashlib.sha3_256(eps).digest(), "big") % SECP_N
        child_point = _pt_add(
            (int.from_bytes(root[:32], "big"), int.from_bytes(root[32:], "big")),
            _pt_mul(scalar, _G),
        )
        child = child_point[0].to_bytes(32, "big") + child_point[1].to_bytes(32, "big")
        self._address = "0x" + keccak(child)[-20:].hex()
        return self._address

    @property
    def address(self) -> str:
        return self.derive_address()

    # ---- x402 signing ----------------------------------------------------- #
    def sign_typed_data(
        self,
        domain: Any,
        types: dict[str, list[Any]],
        primary_type: str,
        message: dict[str, Any],
    ) -> bytes:
        """Sign EIP-712 typed data with the NEAR MPC. Returns 65-byte (r||s||v)."""
        # Convert domain/types the same way EthAccountSigner does
        domain_dict: dict[str, Any]
        if hasattr(domain, "name") or hasattr(domain, "chain_id"):
            domain_dict = {
                "name": getattr(domain, "name", None),
                "version": getattr(domain, "version", None),
                "chainId": getattr(domain, "chain_id", None),
                "verifyingContract": getattr(domain, "verifying_contract", None),
            }
        else:
            domain_dict = domain
        domain_dict = {k: v for k, v in domain_dict.items() if v is not None}

        types_dict: dict[str, list[dict[str, str]]] = {}
        for tname, fields in types.items():
            types_dict[tname] = [
                {"name": f.name, "type": f.type} if hasattr(f, "name") else f
                for f in fields
            ]

        from eth_account.messages import encode_typed_data

        # EIP712Domain must NOT be among message_types (domain is passed separately);
        # otherwise eth_account treats it as a primary-type candidate and fails.
        types_for_encoding = {k: v for k, v in types_dict.items() if k != "EIP712Domain"}
        signable = encode_typed_data(domain_data=domain_dict, message_types=types_for_encoding, message_data=message)
        # signable.body is the struct hash, NOT the full EIP-712 digest.
        # Full digest = keccak256(0x19 0x01 || domain_hash(header) || struct_hash(body)),
        # which is what the x402 server verifies (see x402.mechanisms.evm.eip712.hash_typed_data).
        from eth_utils import keccak as _keccak

        digest = _keccak(b"\x19\x01" + signable.header + signable.body)  # 32-byte EIP-712 digest
        vrs = self._mpc_sign(digest)
        r, s, v = vrs
        return r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([v])

    # ---- MPC signing ------------------------------------------------------- #
    def _mpc_sign(self, digest: bytes) -> tuple[int, int, int]:
        """Call v1.signer.sign(request{payload_v2:{Ecdsa}, path, domain_id=0}); parse signature."""
        # chainsig.js: payload is a HEX string under payload_v2.<keyType>
        payload_hex = digest.hex()
        args = {
            "request": {"payload_v2": {"Ecdsa": payload_hex}, "path": self.path, "domain_id": 0}
        }
        gas = 300_000_000_000_000  # 300 TGas (chainsig.js constant) — MPC awaits external service
        deposit = 1                 # 1 yoctoNEAR (chainsig.js)
        res = self._acct.function_call_sync(self.mpc, "sign", args, gas, deposit)
        sig = self._parse_signature(res)
        if sig is None:
            tx_hash = getattr(res, "transaction", None)
            tx_hash = getattr(tx_hash, "hash", None) if tx_hash is not None else None
            status = getattr(res, "status", None)
            raise RuntimeError(
                f"MPC sign produced no parseable signature. tx={tx_hash} status={status}"
            )
        # verify recovery (eth_keys expects v in {0,1}; x402/EVM format uses 27/28)
        from eth_keys import keys

        rec = keys.Signature(vrs=(sig["recovery_id"], sig["r"], sig["s"]))
        addr = rec.recover_public_key_from_msg_hash(digest).to_checksum_address()
        if addr.lower() != self.address.lower():
            raise RuntimeError(
                f"MPC signature does not recover to derived address {self.address} (got {addr})"
            )
        return sig["r"], sig["s"], sig["recovery_id"] + 27

    @staticmethod
    def _parse_signature(res) -> dict | None:
        """Parse {big_r,s,recovery_id} / {bigR,x,s,recoveryId} from a py-near TransactionResult."""
        candidates: list[dict] = []

        def _collect_sv(sv):
            if not sv:
                return
            try:
                obj = json.loads(base64.b64decode(sv))
                if isinstance(obj, dict) and ("big_r" in obj or "bigR" in obj):
                    candidates.append(obj)
            except Exception:
                pass

        def _collect_logs(logs):
            for log in logs or []:
                if "big_r" in log or "bigR" in log:
                    try:
                        obj = json.loads(log[log.index("{") :])
                        if isinstance(obj, dict) and ("big_r" in obj or "bigR" in obj):
                            candidates.append(obj)
                    except Exception:
                        pass

        # main tx outcome + every receipt outcome
        for st in (getattr(res, "status", None), getattr(getattr(res, "transaction_outcome", None), "status", None)):
            if isinstance(st, dict):
                _collect_sv(st.get("SuccessValue"))
        for ro in getattr(res, "receipt_outcome", []) or []:
            st = getattr(ro, "status", None)
            if isinstance(st, dict):
                _collect_sv(st.get("SuccessValue"))
            _collect_logs(getattr(ro, "logs", None))
        _collect_logs(getattr(res, "logs", None))

        for obj in candidates:
            try:
                if "bigR" in obj:  # {bigR:{x}, s:bigint, recoveryId}
                    r = obj["bigR"]["x"]
                    if isinstance(r, int):
                        r = f"{r:064x}"
                    s = obj["s"]
                    if isinstance(s, int):
                        s = f"{s:064x}"
                    return {"r": int(r, 16), "s": int(s, 16), "recovery_id": int(obj.get("recoveryId", 0))}
                affine = obj["big_r"].get("signature", {}).get("affine_point") or obj["big_r"]
                if isinstance(affine, dict):
                    affine = affine.get("affine_point", "")
                affine = affine.replace("0x", "")
                r = int(affine[-64:], 16)          # compressed point: prefix(2) || x(64 hex)
                s_hex = obj["s"].get("scalar") if isinstance(obj["s"], dict) else obj["s"]
                s = int(s_hex.replace("0x", ""), 16)
                rid = int(obj.get("recovery_id", 0))
                return {"r": r, "s": s, "recovery_id": rid}
            except Exception:
                continue
        return None

    # ---- 1Click auto-funding (NEAR assets -> derived EVM address USDC) ----- #
    USDC_ON_NEAR = "nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
    USDC_ON_NEAR_CONTRACT = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
    WNEAR = "nep141:wrap.near"

    async def ensure_usdc_on_chain(
        self,
        target_chain: str,
        need_usdc: float,
        dest_asset: str | None = None,
        slippage_bps: int = 100,
        check_balance_fn=None,
    ) -> str:
        """Ensure >= need_usdc USDC sits on the derived EVM address on `target_chain`.

        check_balance_fn(address) -> float (USDC balance). If short, run a 1Click
        EXACT_INPUT swap from NEAR-side assets (USDC@NEAR -> wNEAR -> native NEAR)
        into USDC on target_chain.
        Returns: 'ok' | 'funded:<txhash>' | 'skipped:<reason>'
        """
        bal = 0.0
        if check_balance_fn is not None:
            bal = check_balance_fn(self.address) or 0.0
        if bal >= need_usdc:
            return "ok"
        if not dest_asset:
            dest_asset = await self._oneclick_find_dest_asset(target_chain)
        if not dest_asset:
            return f"skipped:no 1Click dest asset for {target_chain}"
        short = max(need_usdc * 1.05, need_usdc - bal + 0.05)  # small buffer
        origin = await self._pick_origin(short)
        if origin is None:
            return "skipped:no NEAR-side USDC/wNEAR/native NEAR to fund"
        return await self._oneclick_swap(origin, dest_asset, short, slippage_bps)

    async def _near_price(self) -> float:
        """wNEAR price in USD from 1Click tokens (fallback 0.0)."""
        try:
            async with httpx.AsyncClient(trust_env=True, timeout=30) as c:
                data = (await c.get("https://1click.chaindefuser.com/v0/tokens")).json()
            tl = data if isinstance(data, list) else data.get("tokens", [])
            for t in tl:
                if (t.get("symbol") or "").upper() in ("WNEAR", "NEAR") and (t.get("blockchain") or "").lower() == "near":
                    return float(t.get("price") or 0) or 0.0
        except Exception:
            pass
        return 0.0

    async def _pick_origin(self, need_usdc: float) -> str | None:
        """Choose funding source: USDC@NEAR > wNEAR > native NEAR (auto-wrap).

        Compares by USD value (wNEAR/native use 1e24 atomic, USDC uses 1e6).
        """
        need_usdc_int = int(need_usdc * 1_000_000)
        for contract, asset in ((self.USDC_ON_NEAR_CONTRACT, self.USDC_ON_NEAR),):
            try:
                if await self._acct.ft_balance(contract) >= need_usdc_int:
                    return asset
            except Exception:
                continue  # one probe failing must not block the others
        price = await self._near_price()
        need_atomic = int(need_usdc / price * 10**24) if price else 0
        for contract, asset in (("wrap.near", self.WNEAR),):
            try:
                if await self._acct.ft_balance(contract) >= need_atomic:
                    return asset
            except Exception:
                continue
        try:
            if await self._acct.near_balance() >= need_atomic:
                return self.WNEAR  # caller wraps native NEAR first
        except Exception:
            pass
        return None

    async def _oneclick_find_dest_asset(self, chain_name: str) -> str | None:
        """Find 1Click asset id for USDC on `chain_name` (base/arbitrum/…)."""
        async with httpx.AsyncClient(trust_env=True, timeout=30) as c:
            data = (await c.get("https://1click.chaindefuser.com/v0/tokens")).json()
        tokens_list = data if isinstance(data, list) else data.get("tokens", [])
        for t in tokens_list:
            aid = t.get("defuseAssetId", t.get("assetId", ""))
            sym = (t.get("symbol") or "").upper()
            chain = (t.get("blockchain") or "").lower()
            if sym == "USDC" and chain_name.lower() in chain:
                return aid
        return None

    async def _oneclick_swap(self, origin: str, dest: str, amount_usd: float, slippage_bps: int) -> str:
        from py_near.transactions import create_function_call_action

        base = "https://1click.chaindefuser.com"
        evm_addr = self.address
        origin_contract = origin.split(":")[-1]
        # 1Click `amount` is in ORIGIN asset atomic units: wNEAR uses 1e24, USDC 1e6.
        if origin == self.WNEAR:
            price = await self._near_price()
            amount_atomic = int(amount_usd / price * 10**24) if price else 0
        else:
            amount_atomic = int(amount_usd * 1_000_000)
        if amount_atomic <= 0:
            return "skipped:amount_atomic<=0 (no price data?)"
        wrap_needed = origin == self.WNEAR and await self._acct.ft_balance("wrap.near") < amount_atomic
        deadline = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        async with httpx.AsyncClient(trust_env=True, timeout=60) as c:
            body = {
                "dry": True,
                "swapType": "EXACT_INPUT",
                "originAsset": origin,
                "destinationAsset": dest,
                "amount": str(amount_atomic),
                "recipient": evm_addr,
                "refundTo": self._acct.account_id,
                "slippageTolerance": slippage_bps,
                "referral": "x402-bazaar-client",
                "depositType": "ORIGIN_CHAIN",
                "refundType": "ORIGIN_CHAIN",
                "recipientType": "DESTINATION_CHAIN",
                "deadline": deadline,
            }
            r = (await c.post(f"{base}/v0/quote", json=body)).json()
            amount_in = int(r["quote"]["amountIn"])
            body["dry"] = False
            r2 = (await c.post(f"{base}/v0/quote", json=body)).json()
            deposit_addr = r2["quote"]["depositAddress"]
            correlation = r2.get("correlationId")
            # 3) deposit on NEAR side: NEP-141 `ft_transfer` requires the receiver to be
            # storage-registered, so first `storage_deposit` the 1Click deposit address.
            ft_args = {"receiver_id": deposit_addr, "amount": str(amount_in)}
            storage_amount = 50_000_000_000_000_000_000_000  # 0.05 NEAR (covers registration)
            if origin_contract == "wrap.near":
                actions = []
                if wrap_needed:
                    # wrap native NEAR first
                    actions.append(create_function_call_action("near_deposit", b"{}", 30_000_000_000_000, amount_atomic))
                actions.append(create_function_call_action("storage_deposit", json.dumps({"account_id": deposit_addr}).encode(), 10_000_000_000_000, storage_amount))
                actions.append(create_function_call_action("ft_transfer", json.dumps(ft_args).encode(), 50_000_000_000_000, 1))
                tx_res = await self._acct.multi_action(origin_contract, actions)
            else:
                # USDC@NEAR: same registration guard (idempotent if already registered)
                actions = [
                    create_function_call_action("storage_deposit", json.dumps({"account_id": deposit_addr}).encode(), 10_000_000_000_000, storage_amount),
                    create_function_call_action("ft_transfer", json.dumps(ft_args).encode(), 50_000_000_000_000, 1),
                ]
                tx_res = await self._acct.multi_action(origin_contract, actions)
            tx_hash = getattr(getattr(tx_res, "transaction", None), "hash", None) or "?"
            # 4) notify + poll status
            try:
                await c.post(
                    f"{base}/v0/deposit/submit",
                    json={"txHash": tx_hash, "depositAddress": deposit_addr, "nearSenderAccount": self._acct.account_id},
                )
            except Exception:
                pass
            status = await self._oneclick_poll(c, correlation, deposit_addr)
        return f"funded:{status} tx={tx_hash}"

    @staticmethod
    async def _oneclick_poll(client: httpx.AsyncClient, correlation: str, deposit_addr: str, timeout_s: int = 240) -> str:
        start = time.time()
        while time.time() - start < timeout_s:
            try:
                r = await client.get(
                    "https://1click.chaindefuser.com/v0/status",
                    params={"correlationId": correlation, "depositAddress": deposit_addr},
                    timeout=30,
                )
                st = r.json().get("status", "")
                if st in ("SUCCESS", "REFUNDED", "FAILED", "EXPIRED", "INCOMPLETE_DEPOSIT"):
                    return st
            except Exception:
                pass
            await asyncio.sleep(5)
        return "TIMEOUT"
