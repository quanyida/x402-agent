"""Solana 链采集器：全资产余额快照对比 → 净流入/流出信号

数据源：https://solana-rpc.publicnode.com（免 key 公共 RPC）
方法：
- SOL 原生余额：getBalance
- 全部 SPL 代币：getTokenAccountsByOwner（jsonParsed 编码 → uiAmount）
  → 天然枚举地址持有的任意 SPL 代币（无需显式列表）
- 历史时点：Solana 公共 RPC 通常非归档节点，无法查 24h 前余额；
  用"首次采集建立基线 + 后续对比"方式（持久化到 data/sol_baseline.json）
输出：data/latest_signals_solana.json
"""
import json
import os
import sys
import time

_SM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SM_ROOT not in sys.path:
    sys.path.insert(0, _SM_ROOT)
from config import CHAINS, SPL_TOKEN_PROGRAM, SIGNAL_THRESHOLD_USD, DEFAULT_WATCHED_WALLETS
from collector.rpc import rpc_call
from collector.prices import get_prices

DATA_DIR = os.path.join(_SM_ROOT, "data")
WATCHED_JSON = os.path.join(DATA_DIR, "watched_wallets.json")
SOL_BASELINE = os.path.join(DATA_DIR, "sol_baseline.json")


def load_watched_wallets(chain="solana") -> list:
    if os.path.exists(WATCHED_JSON):
        try:
            with open(WATCHED_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            if chain in data and data[chain]:
                return data[chain]
        except Exception:
            pass
    return DEFAULT_WATCHED_WALLETS.get(chain, [])


def _rpc(method, params):
    cfg = CHAINS["solana"]
    return rpc_call(cfg["rpc"], method, params, force_proxy=cfg.get("force_proxy", False))


def get_sol_balance(addr: str) -> float:
    r = _rpc("getBalance", [addr])
    return r.get("value", 0) / 1e9


def get_spl_accounts(addr: str) -> list:
    """枚举地址全部 SPL 代币账户，返回 [{mint, symbol?, amount, decimals}]"""
    params = [addr, {"programId": SPL_TOKEN_PROGRAM}, {"encoding": "jsonParsed"}]
    r = _rpc("getTokenAccountsByOwner", params)
    out = []
    for acc in r.get("result", {}).get("value", []):
        info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        mint = info.get("mint", "")
        amount = info.get("tokenAmount", {})
        out.append({
            "mint": mint,
            "amount": float(amount.get("uiAmount") or 0),
            "decimals": amount.get("decimals", 0),
        })
    return out


def snapshot_all(addr: str) -> dict:
    """返回 {symbol/mint: amount}。SOL 固定；SPL 用 mint 地址作 key，已知 symbol 标出。"""
    out = {"SOL": get_sol_balance(addr)}
    known = {a["contract"]: a["symbol"] for a in CHAINS["solana"]["assets"] if a["contract"]}
    for ta in get_spl_accounts(addr):
        key = known.get(ta["mint"], ta["mint"][:8] + "...")
        out[key] = ta["amount"]
    return out


def collect_signals(watched_wallets: list, threshold_usd: int = None) -> dict:
    """Solana 快照对比：读持久化基线，与当前对比；首次运行仅建基线。"""
    thr = threshold_usd or SIGNAL_THRESHOLD_USD["solana"]
    baseline = {}
    if os.path.exists(SOL_BASELINE):
        try:
            with open(SOL_BASELINE, "r", encoding="utf-8") as f:
                baseline = json.load(f)
        except Exception:
            baseline = {}

    results = {"chain": "solana", "threshold_usd": thr, "signals": [], "all": [],
               "baseline_updated": False, "first_run": not bool(baseline),
               "note": ""}
    if not baseline:
        results["note"] = "首次运行，仅建立基线（下次运行起输出净变动）"
    now_snap = {}
    for w in watched_wallets:
        addr = w["address"]
        try:
            snap = snapshot_all(addr)
            now_snap[addr] = snap
        except Exception as e:
            print(f"  [warn] {addr[:12]} 采集失败: {e}")

    # 价格折算
    cg_ids = set()
    for a in CHAINS["solana"]["assets"]:
        cg_ids.add(a["cg_id"])
    prices = get_prices(list(cg_ids))
    results["prices"] = prices

    for w in watched_wallets:
        addr = w["address"]
        snap = now_snap.get(addr)
        if snap is None:
            continue
        prev = baseline.get(addr)
        asset_items = []
        total_delta = 0.0
        for sym, now_v in snap.items():
            if prev and sym in prev:
                delta = now_v - prev[sym]
            elif prev is None:
                # 首次建基线：当前余额记为 prev 同值，净变动=0（避免假信号）
                delta = 0.0
            else:
                delta = now_v  # 新出现的资产，全额视为变动
            price = prices.get("solana" if sym == "SOL" else "usd-coin", 0)
            delta_usd = delta * price
            total_delta += delta_usd
            asset_items.append({"symbol": sym, "balance_now": now_v,
                                "balance_prev": prev.get(sym, 0) if prev else now_v,
                                "net_change": delta, "net_change_usd": delta_usd,
                                "price_usd": price})
        item = {"name": w.get("name", addr[:8]), "address": addr, "tag": w.get("tag", ""),
                "assets": asset_items, "total_delta_usd": total_delta,
                "is_signal": abs(total_delta) >= thr,
                "direction": "inflow" if total_delta > thr else ("outflow" if total_delta < -thr else "flat")}
        results["all"].append(item)
        if item["is_signal"]:
            results["signals"].append(item)

    # 持久化本次快照为新基线
    if now_snap:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SOL_BASELINE, "w", encoding="utf-8") as f:
            json.dump(now_snap, f, ensure_ascii=False, indent=2)
        results["baseline_updated"] = True

    results["signals"].sort(key=lambda x: abs(x["total_delta_usd"]), reverse=True)
    return results


if __name__ == "__main__":
    t0 = time.time()
    wallets = load_watched_wallets("solana")
    r = collect_signals(wallets)
    print(f"\n[Solana] 全资产快照, 耗时 {time.time()-t0:.1f}s, "
          f"监控 {len(r['all'])} 地址, 信号 {len(r['signals'])} 个")
    print(f"  基线已更新: {r['baseline_updated']}; 首次运行: {r['first_run']}")
    if r.get("note"):
        print(f"  {r['note']}")
    for it in r["all"]:
        print(f"  {it['name']:14s} {it['address'][:12]}...  总变动 ${it['total_delta_usd']:+,.2f}  "
              f"{'★信号' if it['is_signal'] else ''}")
        for a in it["assets"]:
            print(f"      {a['symbol']:8s} {a['balance_prev']:>18,.4f} -> "
                  f"{a['balance_now']:>18,.4f}  Δ{a['net_change']:>+18,.4f}")

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "latest_signals_solana.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    print(f"  结果已写入 {out_path}")
