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
HISTORY_FILE = os.path.join(DATA_DIR, "hl_history.jsonl")  # 方案B：追加式历史（时间序列）

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


def compute_delta(prev: dict | None, cur: dict) -> dict:
    """对比上一截面与当前截面 → 每币种净敞口变化（delta）。

    口径说明：这是「榜上大账户整体敞口」的变化，排行榜为滚动名单，
    人群可能部分换人，非严格固定人群 delta（精确对标需固定地址名单）。
    """
    if not prev:
        return {}
    prev_coins = {c["coin"]: c for c in prev.get("coins", [])}
    cur_coins = {c["coin"]: c for c in cur.get("coins", [])}
    items = []
    for coin in sorted(set(prev_coins) | set(cur_coins)):
        p = prev_coins.get(coin, {}).get("net_usd", 0.0)
        n = cur_coins.get(coin, {}).get("net_usd", 0.0)
        d = n - p
        if abs(d) < 500_000:
            continue  # 忽略微小变化（<$0.5M）
        if p <= 0 and n <= 0:
            action = "加空" if d < 0 else "回补(减空)"
        elif p >= 0 and n >= 0:
            action = "加多" if d > 0 else "减多"
        else:
            action = "翻多" if n > 0 else "翻空"
        cc = cur_coins.get(coin, {})
        items.append({
            "coin": coin,
            "prev_net_usd": round(p, 2),
            "now_net_usd": round(n, 2),
            "delta_usd": round(d, 2),
            "action": action,
            "n_long": cc.get("n_long", 0),
            "n_short": cc.get("n_short", 0),
        })
    items.sort(key=lambda x: -abs(x["delta_usd"]))
    return {
        "prev_generated_at": prev.get("generated_at", ""),
        "now_generated_at": cur.get("generated_at", ""),
        "hours_elapsed": None,  # 由调用方按时间戳补算
        "total_value_delta_usd": round(
            cur.get("total_account_value_usd", 0) - prev.get("total_account_value_usd", 0), 2),
        "items": items,
    }


def append_history(snapshot: dict, history_file: str = HISTORY_FILE) -> int:
    """方案B：把一次完整截面追加为 JSONL 一行（按 generated_at 去重）。

    返回历史总行数。
    """
    os.makedirs(os.path.dirname(history_file), exist_ok=True)
    ts = snapshot.get("generated_at", "")
    lines = []
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("generated_at") == ts:
                    continue  # 同时间戳视为同一截面，去重
                lines.append(line)
    lines.append(json.dumps(snapshot, ensure_ascii=False))
    with open(history_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)


def main():
    ap = argparse.ArgumentParser(description="Hyperliquid 永续多空采集器 (L4)")
    ap.add_argument("--top", type=int, default=50,
                    help="leaderboard 账户价值 Top N（默认 50）")
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "latest_hl_perps.json"))
    ap.add_argument("--history", default=HISTORY_FILE,
                    help="JSONL 历史文件（默认 data/hl_history.jsonl）")
    ap.add_argument("--no-delta", action="store_true",
                    help="跳过 delta 计算（仅采集写入）")
    ap.add_argument("--no-history", action="store_true",
                    help="跳过历史归档（仅写 latest）")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    # 读上一截面（delta 基准）
    prev = None
    if not args.no_delta and os.path.exists(args.out):
        try:
            with open(args.out, "r", encoding="utf-8") as f:
                prev = json.load(f)
        except Exception as e:
            print(f"[warn] 读取旧快照失败，跳过 delta：{e}")

    print(f"拉取 leaderboard Top {args.top} ...")
    rows = fetch_leaderboard(args.top)
    print(f"  -> {len(rows)} 交易员（账户价值 {float(rows[0]['accountValue']):,.0f} ~ "
          f"{float(rows[-1]['accountValue']):,.0f}）")
    print("聚合持仓多空 ...")
    result = aggregate(rows)

    # delta 计算（方案B 核心：与上一截面对比）
    if not args.no_delta:
        delta = compute_delta(prev, result)
        if delta:
            result["delta"] = delta
            elapsed = None
            try:
                t0 = datetime.strptime(delta["prev_generated_at"], "%Y-%m-%d %H:%M")
                t1 = datetime.strptime(delta["now_generated_at"], "%Y-%m-%d %H:%M")
                elapsed = round((t1 - t0).total_seconds() / 3600, 1)
            except Exception:
                pass
            if elapsed is not None:
                result["delta"]["hours_elapsed"] = elapsed
            n_items = len(delta.get("items", []))
            print(f"  delta：对比 {delta['prev_generated_at']} → {delta['now_generated_at']}，"
                  f"{n_items} 个币种净敞口变化 ≥$0.5M")
        else:
            print("  delta：无上一截面可比（首次运行）")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"已写入 {args.out}")

    # 方案B：JSONL 历史归档
    if not args.no_history:
        n = append_history(result, args.history)
        print(f"历史归档 {args.history}（当前 {n} 个截面）")

    print(f"成功 {result['queried']}/{result['top_n_attempted']}，失败 {result['failed']}")
    print("\n=== Perps 多空方向 ===")
    print(f"{'coin':<10} {'net$':>14} {'longs$':>13} {'shorts$':>13} {'L/S':>6}")
    for c in result["coins"][:15]:
        print(f"{c['coin']:<10} {c['net_usd']:>+14,.0f} {c['longs_usd']:>13,.0f} "
              f"{c['shorts_usd']:>13,.0f} {c['n_long']:>3}/{c['n_short']:<3}")

    # delta 摘要打印
    d = result.get("delta", {})
    if d and d.get("items"):
        print("\n=== 净敞口变化 (delta) ===")
        print(f"{'coin':<10} {'动作':<12} {'上一净额':>12} {'当前净额':>12} {'变化':>12}")
        for it in d["items"][:15]:
            print(f"{it['coin']:<10} {it['action']:<12} {it['prev_net_usd']:>+12,.0f} "
                  f"{it['now_net_usd']:>+12,.0f} {it['delta_usd']:>+12,.0f}")
        print(f"\n账户总价值变化：${d.get('total_value_delta_usd', 0):+,.0f} "
              f"（{d.get('prev_generated_at', '?')} → {d.get('now_generated_at', '?')}）")

    if result["errors"]:
        print("\n[errors]")
        for e in result["errors"]:
            print(" ", e)


if __name__ == "__main__":
    main()
