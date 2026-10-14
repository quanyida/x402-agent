"""Base 链采集器入口：多资产余额快照对比 → 净流入/流出信号

数据源：https://base.drpc.org（免 key 公共 RPC）
方法：eth_getBalance(原生) + eth_call balanceOf(ERC20) 在 [now, 24h前] 两时点查询
资产：ETH / USDC / USDT / DAI / wETH / cbBTC（config.py 可配置扩展）
输出：data/latest_signals_base.json（供 M3 简报消费）

局限（如实标注）：EVM 无索引，只能监控显式资产列表，不能枚举任意 ERC20；
全网大额转账（S1）需 BaseScan V2 API（免费 key），后续接入。
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


def load_watched_wallets(chain="base") -> list:
    """读取监控地址库：data/watched_wallets.json 优先，否则内置默认。"""
    if os.path.exists(WATCHED_JSON):
        try:
            with open(WATCHED_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            if chain in data and data[chain]:
                return data[chain]
        except Exception as e:
            print(f"  [warn] 读取 {WATCHED_JSON} 失败，用内置默认: {e}")
    return DEFAULT_WATCHED_WALLETS.get(chain, [])


if __name__ == "__main__":
    t0 = time.time()
    wallets = load_watched_wallets("base")
    r = collect_balance_signals(wallets, lookback_hours=24, chain="base")
    print(f"\n[Base] 24h 多资产快照对比, 耗时 {time.time()-t0:.1f}s, "
          f"监控 {len(r['all'])} 地址, 信号 {len(r['signals'])} 个 (阈值 ${r['threshold_usd']:,})")
    for it in r["all"]:
        d = it["total_delta_usd"]
        print(f"  {it['name']:14s} {it['address'][:12]}...  总变动 ${d:>+14,.2f}  "
              f"{'★信号' if it['is_signal'] else ''}")
        for a in it["assets"]:
            if a["net_change"] != 0:
                print(f"      {a['symbol']:5s} Δ{a['net_change']:>+16,.4f} "
                      f"(${a['net_change_usd']:+,.2f})")

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "latest_signals_base.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    print(f"  结果已写入 {out_path}")
