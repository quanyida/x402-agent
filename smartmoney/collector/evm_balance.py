"""通用 EVM 链多资产余额采集引擎

对指定地址在 [24h前区块, 最新] 两时点查询：
- 原生币余额（eth_getBalance）
- 配置列表中的 ERC20 余额（eth_call balanceOf）
输出每资产余额、净变动，并用 CoinGecko 价格折算 USD 触发信号。

注意：EVM 无索引时只能查"显式资产列表"（原生币+主流代币），
无法枚举地址持有的任意 ERC20；Solana 的 getTokenAccountsByOwner 可以全量枚举。
"""
import json
import os
import sys
import time

_SM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SM_ROOT not in sys.path:
    sys.path.insert(0, _SM_ROOT)
from config import CHAINS, BALANCEOF_SELECTOR, SIGNAL_THRESHOLD_USD
from collector.rpc import rpc_call
from collector.prices import get_prices


def get_latest_block(chain: str) -> int:
    r = rpc_call(CHAINS[chain]["rpc"], "eth_blockNumber", [])
    return int(r, 16)


def get_native_balance(addr: str, block: int, chain: str) -> float:
    r = rpc_call(CHAINS[chain]["rpc"], "eth_getBalance", [addr, hex(block)])
    return int(r, 16) / 1e18


def get_token_balance(addr: str, contract: str, block: int, decimals: int, chain: str) -> float:
    data = BALANCEOF_SELECTOR + addr[2:].lower().rjust(64, "0")
    r = rpc_call(CHAINS[chain]["rpc"], "eth_call",
                 [{"to": contract, "data": data}, hex(block)])
    return int(r, 16) / (10 ** decimals)


def snapshot_assets(addr: str, block: int, chain: str) -> dict:
    """查询地址在指定区块的所有配置资产余额，返回 {symbol: amount}"""
    out = {}
    for a in CHAINS[chain]["assets"]:
        try:
            if a["contract"] is None:
                out[a["symbol"]] = get_native_balance(addr, block, chain)
            else:
                out[a["symbol"]] = get_token_balance(addr, a["contract"], block,
                                                     a["decimals"], chain)
        except Exception as e:
            print(f"  [warn] {addr[:12]} {a['symbol']}@{block} 查询失败: {e}")
            out[a["symbol"]] = None
    return out


def collect_balance_signals(watched_wallets: list, lookback_hours: int = 24,
                            chain: str = "base") -> dict:
    """对监控地址做多资产余额快照对比，输出净流入/流出信号（USD 折算）。"""
    cfg = CHAINS[chain]
    latest = get_latest_block(chain)
    blocks_back = int(lookback_hours * 3600 / cfg["block_time"])
    prev_block = max(latest - blocks_back, 0)
    threshold = SIGNAL_THRESHOLD_USD[chain]

    cg_ids = [a["cg_id"] for a in cfg["assets"]]
    prices = get_prices(cg_ids)

    results = {
        "chain": chain, "latest_block": latest, "prev_block": prev_block,
        "lookback_hours": lookback_hours, "threshold_usd": threshold,
        "prices": prices, "signals": [], "all": [],
    }

    for w in watched_wallets:
        addr = w["address"]
        try:
            snap_now = snapshot_assets(addr, latest, chain)
            snap_prev = snapshot_assets(addr, prev_block, chain)

            asset_items = []
            total_delta_usd = 0.0
            for a in cfg["assets"]:
                sym = a["symbol"]
                now_v, prev_v = snap_now.get(sym), snap_prev.get(sym)
                if now_v is None or prev_v is None:
                    continue
                delta = now_v - prev_v
                price = prices.get(a["cg_id"], 0)
                delta_usd = delta * price
                total_delta_usd += delta_usd
                asset_items.append({
                    "symbol": sym,
                    "balance_now": now_v,
                    "balance_prev": prev_v,
                    "net_change": delta,
                    "net_change_usd": delta_usd,
                    "price_usd": price,
                })

            item = {
                "name": w.get("name", addr[:8]),
                "address": addr,
                "tag": w.get("tag", ""),
                "assets": asset_items,
                "total_delta_usd": total_delta_usd,
                "is_signal": abs(total_delta_usd) >= threshold,
                "direction": "inflow" if total_delta_usd > threshold else (
                    "outflow" if total_delta_usd < -threshold else "flat"),
            }
            results["all"].append(item)
            if item["is_signal"]:
                results["signals"].append(item)
        except Exception as e:
            print(f"  [warn] {addr[:12]} 采集失败: {e}")

    results["signals"].sort(key=lambda x: abs(x["total_delta_usd"]), reverse=True)
    return results


if __name__ == "__main__":
    # 自测
    t0 = time.time()
    r = collect_balance_signals([{"name": "Coinbase-2",
                                  "address": "0x503828976D22510aad0201ac7EC88293211D23Da",
                                  "tag": "CEX"}], lookback_hours=24, chain="base")
    print(f"耗时 {time.time()-t0:.1f}s, 信号 {len(r['signals'])}")
    for it in r["all"]:
        print(f"  {it['name']}: 总变动 ${it['total_delta_usd']:+,.2f}")
        for a in it["assets"]:
            print(f"    {a['symbol']:5s} {a['balance_prev']:>16,.4f} -> "
                  f"{a['balance_now']:>16,.4f}  Δ{a['net_change']:>+16,.4f} "
                  f"(${a['net_change_usd']:+,.2f})")
