"""M3 简报生成器（规则化版，零 LLM 依赖）

读取三条链的 latest_signals_*.json，生成「聪明钱每日简报」：
- 免费摘要版：总览 + 阈值信号 + 非零异动 Top + 市场快照（用于 M5 X 引流）
- 完整版结构预留：逐地址明细（M4 付费解锁 x402 端点消费）

规则化生成，无需 LLM key；后续可用 LLM 升级为自然语言解读（方案附录已说明）。
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

_SM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SM_ROOT not in sys.path:
    sys.path.insert(0, _SM_ROOT)

DATA_DIR = os.path.join(_SM_ROOT, "data")
CHAIN_NAMES = {"base": "Base", "eth": "Ethereum", "solana": "Solana"}
CHAIN_EMOJI = {"base": "🟦", "eth": "◆", "solana": "🟪"}
CHAIN_EXPLORERS = {"base": "https://basescan.org", "eth": "https://etherscan.io",
                   "solana": "https://solscan.io"}


def load_signals(chain: str) -> dict:
    path = os.path.join(DATA_DIR, f"latest_signals_{chain}.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt_usd(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:,.2f}M"
    return f"${v:+,.2f}"


def fmt_amt(v: float, sym: str) -> str:
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:,.2f}M {sym}"
    if abs(v) >= 1_000:
        return f"{v:,.2f} {sym}"
    return f"{v:.4f} {sym}"


def generate_brief_parts() -> dict:
    """生成简报两部分：
    - free: 免费摘要版（总览+信号+Top异动+市场快照）→ M5 X 引流
    - full: 完整版（逐地址全资产明细+解读框架）→ M4 x402 付费端点
    """
    chains = {c: load_signals(c) for c in CHAIN_NAMES}
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime("%Y-%m-%d")
    ts_str = now.strftime("%Y-%m-%d %H:%M")

    # ---------- 免费摘要版 ----------
    free = []
    free.append(f"# 聪明钱每日简报 · {date_str}")
    free.append("")
    free.append(f"> 生成时间：{ts_str}（UTC+8）｜数据源：链上余额快照对比（24h）")
    free.append("")
    free.append("## 📊 总览")
    free.append("")
    free.append("| 链 | 监控地址 | 阈值信号 | 异动地址 | 24h净变动(USD) |")
    free.append("|---|---|---|---|---|")
    grand_total = 0.0
    total_sig = 0
    for c, d in chains.items():
        if not d:
            continue
        n_addr = len(d.get("all", []))
        n_sig = len(d.get("signals", []))
        movers = [a for a in d.get("all", []) if abs(a.get("total_delta_usd", 0)) > 0]
        chain_total = sum(a.get("total_delta_usd", 0) for a in d.get("all", []))
        grand_total += chain_total
        total_sig += n_sig
        free.append(f"| {CHAIN_EMOJI[c]} {CHAIN_NAMES[c]} | {n_addr} | {n_sig} | {len(movers)} | {fmt_usd(chain_total)} |")
    free.append("")
    free.append(f"**合计**：监控 {sum(len(d.get('all', [])) for d in chains.values() if d)} 地址，"
                f"触发 {total_sig} 个阈值信号，24h 净变动 {fmt_usd(grand_total)}")
    free.append("")

    free.append("## 🚨 阈值信号（≥$100K）")
    free.append("")
    sig_any = False
    for c, d in chains.items():
        for s in d.get("signals", []):
            sig_any = True
            free.append(f"### {CHAIN_EMOJI[c]} {CHAIN_NAMES[c]} · {s.get('name', '?')}（{s.get('tag', '')}）")
            free.append("")
            free.append(f"- **方向**：{'流入' if s['total_delta_usd'] > 0 else '流出'} "
                        f"{fmt_usd(s['total_delta_usd'])}")
            free.append(f"- **地址**：`{s['address']}` "
                        f"([查看]({CHAIN_EXPLORERS[c]}/address/{s['address']}))")
            for a in s.get("assets", []):
                if a.get("net_change", 0) != 0:
                    free.append(f"- {a['symbol']}：{fmt_amt(a['balance_prev'], a['symbol'])} → "
                                f"{fmt_amt(a['balance_now'], a['symbol'])} "
                                f"（{fmt_amt(a['net_change'], a['symbol'])}，{fmt_usd(a['net_change_usd'])}）")
            free.append("")
    if not sig_any:
        free.append("今日无阈值信号（监控地址 24h 变动均未达 $100K）。")
        free.append("")

    free.append("## 🔍 监控异动（非零变动）")
    free.append("")
    movers_all = []
    for c, d in chains.items():
        for a in d.get("all", []):
            if abs(a.get("total_delta_usd", 0)) > 0:
                movers_all.append((c, a))
    movers_all.sort(key=lambda x: -abs(x[1]["total_delta_usd"]))
    if movers_all:
        free.append("| 链 | 地址 | 标签 | 24h变动 | 主要资产 |")
        free.append("|---|---|---|---|---|")
        for c, a in movers_all[:10]:
            main_asset = ""
            top = max(a.get("assets", []), key=lambda x: abs(x.get("net_change_usd", 0)), default=None)
            if top:
                main_asset = f"{top['symbol']} {fmt_amt(top['net_change'], top['symbol'])}"
            free.append(f"| {CHAIN_NAMES[c]} | `{a['address'][:10]}...` | {a.get('tag', '-')} | "
                        f"{fmt_usd(a['total_delta_usd'])} | {main_asset} |")
        free.append("")
    else:
        free.append("今日监控地址无任何非零变动。")
        free.append("")

    free.append("## 💹 市场快照")
    free.append("")
    prices = {}
    for c, d in chains.items():
        for k, v in (d.get("prices", {}) or {}).items():
            prices.setdefault(k, v)
    if prices:
        names = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL",
                 "usd-coin": "USDC", "tether": "USDT", "dai": "DAI"}
        free.append("| 资产 | 价格(USD) |")
        free.append("|---|---|")
        for k, v in sorted(prices.items(), key=lambda x: -x[1]):
            free.append(f"| {names.get(k, k)} | ${v:,.4f} |")
        free.append("")
    free.append("---")
    free.append("")
    free.append("> 🔒 完整版（逐地址明细+聪明钱解读）见付费版：$0.05/份（Base 主网 USDC）")
    free.append("")

    # ---------- 完整版 ----------
    full = []
    full.append(f"# 聪明钱每日简报 · 完整版 · {date_str}")
    full.append("")
    full.append(f"> 生成时间：{ts_str}（UTC+8）｜数据源：链上余额快照对比（24h）")
    full.append("")

    for c, d in chains.items():
        if not d:
            continue
        full.append(f"## {CHAIN_EMOJI[c]} {CHAIN_NAMES[c]}")
        full.append("")
        full.append(f"监控 {len(d.get('all', []))} 地址，阈值 $100K，信号 {len(d.get('signals', []))} 个")
        full.append("")
        for a in d.get("all", []):
            full.append(f"### {a.get('name', '?')}（{a.get('tag', '-')}）")
            full.append("")
            full.append(f"- 地址：`{a['address']}` ([查看]({CHAIN_EXPLORERS[c]}/address/{a['address']}))")
            full.append(f"- 24h 净变动：{fmt_usd(a.get('total_delta_usd', 0))}  "
                        f"{'★信号' if a.get('is_signal') else ''}")
            full.append("")
            full.append("| 资产 | 24h前 | 现在 | 净变动 | USD |")
            full.append("|---|---|---|---|---|")
            for x in a.get("assets", []):
                full.append(f"| {x['symbol']} | {fmt_amt(x['balance_prev'], x['symbol'])} | "
                            f"{fmt_amt(x['balance_now'], x['symbol'])} | "
                            f"{fmt_amt(x['net_change'], x['symbol'])} | {fmt_usd(x['net_change_usd'])} |")
            full.append("")

    # 解读框架（M3.5 LLM 升级占位；当前规则化）
    full.append("## 🧠 聪明钱解读（规则化）")
    full.append("")
    movers_all.sort(key=lambda x: -abs(x[1]["total_delta_usd"]))
    for c, a in movers_all[:5]:
        d = a["total_delta_usd"]
        direction = "净流入" if d > 0 else "净流出"
        full.append(f"- **{a.get('name', '?')}**（{CHAIN_NAMES[c]}）{direction} {fmt_usd(abs(d))}："
                    f"{'资金在向该地址聚集' if d > 0 else '资金正从该地址流出'}，"
                    f"tag={a.get('tag', '-')}。")
    if not movers_all:
        full.append("- 今日监控地址无显著异动，市场整体平稳。")
    full.append("")
    full.append("## 📌 明日关注点")
    full.append("")
    full.append("- 持续跟踪上述异动地址的后续动作（转入/转出对手方）")
    full.append("- 阈值信号如持续累积，关注对应链的资金流向趋势")
    full.append("")
    return {"free": "\n".join(free), "full": "\n".join(full), "date": date_str,
            "generated_at": ts_str}


def main():
    parts = generate_brief_parts()
    os.makedirs(DATA_DIR, exist_ok=True)
    date_str = parts["date"].replace("-", "")
    free_path = os.path.join(DATA_DIR, f"brief_free_{date_str}.md")
    full_path = os.path.join(DATA_DIR, f"brief_full_{date_str}.md")
    with open(free_path, "w", encoding="utf-8") as f:
        f.write(parts["free"])
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(parts["full"])
    # latest 引用（M4/M5 消费）
    with open(os.path.join(DATA_DIR, "latest_brief_free.md"), "w", encoding="utf-8") as f:
        f.write(parts["free"])
    with open(os.path.join(DATA_DIR, "latest_brief_full.md"), "w", encoding="utf-8") as f:
        f.write(parts["full"])
    print(f"简报已生成：\n  免费版 {free_path}\n  完整版 {full_path}")
    print("--- 免费版预览（前 40 行）---")
    print("\n".join(parts["free"].splitlines()[:40]))


if __name__ == "__main__":
    main()
