"""ETH 主网采集器入口：多资产余额快照对比 → 净流入/流出信号

数据源：https://ethereum-rpc.publicnode.com（免 key 公共 RPC）
资产：ETH / USDC / USDT / DAI / wETH / WBTC（config.py 可配置扩展）
注意：ETH 出块 12s，24h ≈ 7200 块，历史 eth_call 可能超时（非归档节点）——自动重试+容错
"""
import json
import os
import sys
import time

_SM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SM_ROOT not in sys.path:
    sys.path.insert(0, _SM_ROOT)
from config import DEFAULT_WATCHED_WALLETS
from collector.evm_balance import collect_balance_signals

DATA_DIR = os.path.join(_SM_ROOT, "data")
WATCHED_JSON = os.path.join(DATA_DIR, "watched_wallets.json")


def load_watched_wallets(chain="eth") -> list:
    if os.path.exists(WATCHED_JSON):
        try:
            with open(WATCHED_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            if chain in data and data[chain]:
                return data[chain]
        except Exception:
            pass
    return DEFAULT_WATCHED_WALLETS.get(chain, [])


if __name__ == "__main__":
    t0 = time.time()
    wallets = load_watched_wallets("eth")
    r = collect_balance_signals(wallets, lookback_hours=24, chain="eth")
    print(f"\n[ETH] 24h 多资产快照对比, 耗时 {time.time()-t0:.1f}s, "
          f"监控 {len(r['all'])} 地址, 信号 {len(r['signals'])} 个")
    for it in r["all"]:
        print(f"  {it['name']:14s} {it['address'][:12]}...  总变动 ${it['total_delta_usd']:+,.2f}  "
              f"{'★信号' if it['is_signal'] else ''}")
        for a in it["assets"]:
            if a["net_change"] != 0:
                print(f"      {a['symbol']:5s} Δ{a['net_change']:>+16,.4f} ($ {a['net_change_usd']:+,.2f})")

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "latest_signals_eth.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    print(f"  结果已写入 {out_path}")
