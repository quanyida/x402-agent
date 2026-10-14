"""L4 采集器：Hyperliquid 永续聪明钱多空方向（对标 Nansen Perps Smart Money）

数据源（2026-10-14 实测打通）：
- leaderboard:  https://stats-data.hyperliquid.xyz/Mainnet/leaderboard
  → 必须带浏览器 UA + Referer（否则 403 AccessDenied），返回 leaderboardRows:
    ethAddress / accountValue / windowPerformances[day,week,month,allTime].{pnl,roi,vlm}
- 持仓:         POST https://api.hyperliquid.xyz/info  {"type":"clearinghouseState","user":<addr>}
  → assetPositions[].position: coin / szi(正=多,负=空) / positionValue(名义USD)

聚合逻辑：取 leaderboard 账户价值 Top N → 逐个查持仓 → 按币种聚合
  longs$（szi>0 的名义价值合计）/ shorts$ / net$（= longs - shorts）/ 多空账户数。
输出：data/latest_hl_perps.json（生成时间戳 + 币种方向表 + Top 信号）
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import httpx

_SM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_SM_ROOT, "data")

STATS_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO_URL = "https://api.hyperliquid.xyz/info"
PROXY = "http://127.0.0.1:10809"  # 大陆直连 Hyperliquid 偶发断连，代理回退
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://app.hyperliquid.xyz/", "Accept": "application/json"}


def _request(url: str, payload=None, timeout: float = 25.0):
    """直连优先，代理回退；带 UA/Referer。失败抛 RuntimeError。"""
    last = None
    for mode in ("direct", "proxy"):
        for attempt in range(3):
            try:
                c = httpx.Client(timeout=timeout,
                                 headers=HEADERS,
                                 proxy=PROXY if mode == "proxy" else None)
                if payload is not None:
                    r = c.post(url, json=payload)
                else:
                    r = c.get(url)
                c.close()
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code} {r.text[:100]}"
            except Exception as e:
                last = f"{type(e).__name__}: {str(e)[:100]}"
            time.sleep(1)
    raise RuntimeError(f"GET/POST {url} failed: {last}")


def fetch_leaderboard(top_n: int) -> list[dict]:
    """Top N 交易员（按 accountValue 降序）。"""
    rows = _request(STATS_URL)["leaderboardRows"]
    return rows[:top_n]


def fetch_positions(user: str) -> list[dict]:
    """clearinghouseState → assetPositions（原始条目）。"""
    d = _request(INFO_URL, {"type": "clearinghouseState", "user": user})
    return d.get("assetPositions", [])


def aggregate(rows: list[dict], max_fail: int = 5) -> dict:
    """聚合 Top N 交易员持仓 → 币种多空方向。"""
    agg = defaultdict(lambda: {"net": 0.0, "longs": 0.0, "shorts": 0.0,
                               "n_long": 0, "n_short": 0})
    queried, failed, total_value = 0, 0, 0.0
    errors = []
    for r in rows:
        addr = r["ethAddress"]
        try:
            pos = fetch_positions(addr)
        except Exception as e:
            failed += 1
            errors.append(f"{addr[:10]}...: {str(e)[:60]}")
            if failed > max_fail:
                errors.append(f"abort: too many failures ({failed})")
                break
            continue
        queried += 1
        total_value += float(r.get("accountValue", 0) or 0)
        for p in pos:
            posd = p.get("position", {})
            coin = posd.get("coin")
            if not coin:
                continue
            szi = float(posd.get("szi", 0) or 0)
            val = float(posd.get("positionValue", 0) or 0)
            e = agg[coin]
            if szi > 0:
                e["longs"] += val
                e["n_long"] += 1
            elif szi < 0:
                e["shorts"] += val
                e["n_short"] += 1
            e["net"] += val if szi > 0 else -val

    coins = []
    for coin, e in agg.items():
        coins.append({
            "coin": coin,
            "net_usd": round(e["net"], 2),
            "longs_usd": round(e["longs"], 2),
            "shorts_usd": round(e["shorts"], 2),
            "n_long": e["n_long"],
            "n_short": e["n_short"],
            "side": "LONG" if e["net"] > 0 else "SHORT",
        })
    coins.sort(key=lambda x: -abs(x["net_usd"]))

    # Top 净多/净空信号（简单阈值：净额 ≥ $2M）
    signals = [
        {"coin": c["coin"], "side": c["side"], "net_usd": c["net_usd"],
         "longs_usd": c["longs_usd"], "shorts_usd": c["shorts_usd"],
         "n_long": c["n_long"], "n_short": c["n_short"]}
        for c in coins if abs(c["net_usd"]) >= 2_000_000
    ][:15]

    now = datetime.now(timezone(timedelta(hours=8)))
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "source": "Hyperliquid stats-data leaderboard + clearinghouseState",
        "top_n_attempted": len(rows),
        "queried": queried,
        "failed": failed,
        "total_account_value_usd": round(total_value, 2),
        "coins": coins,
        "signals": signals,
        "errors": errors[:10],
    }


def main():
    ap = argparse.ArgumentParser(description="Hyperliquid 永续多空采集器 (L4)")
    ap.add_argument("--top", type=int, default=50,
                    help="leaderboard 账户价值 Top N（默认 50）")
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "latest_hl_perps.json"))
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    print(f"拉取 leaderboard Top {args.top} ...")
    rows = fetch_leaderboard(args.top)
    print(f"  -> {len(rows)} 交易员（账户价值 {float(rows[0]['accountValue']):,.0f} ~ "
          f"{float(rows[-1]['accountValue']):,.0f}）")
    print("聚合持仓多空 ...")
    result = aggregate(rows)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"已写入 {args.out}")
    print(f"成功 {result['queried']}/{result['top_n_attempted']}，失败 {result['failed']}")
    print("\n=== Perps 多空方向 ===")
    print(f"{'coin':<10} {'net$':>14} {'longs$':>13} {'shorts$':>13} {'L/S':>6}")
    for c in result["coins"][:15]:
        print(f"{c['coin']:<10} {c['net_usd']:>+14,.0f} {c['longs_usd']:>13,.0f} "
              f"{c['shorts_usd']:>13,.0f} {c['n_long']:>3}/{c['n_short']:<3}")
    if result["errors"]:
        print("\n[errors]")
        for e in result["errors"]:
            print(" ", e)


if __name__ == "__main__":
    main()
