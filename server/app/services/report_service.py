"""报告业务逻辑：拉取行情快照并生成结构化观点摘要。"""

import asyncio

import yfinance as yf

# 简易代码映射：加密符号 → 美股/加密标识
CRYPTO_SYMBOLS = {
    "BTC", "ETH", "SOL", "NEAR", "TAO", "AAOI", "AXTI",
}


def _is_crypto(symbol: str) -> bool:
    s = symbol.upper()
    return s in CRYPTO_SYMBOLS and s not in ("AAOI", "AXTI")


async def build_report(symbol: str):
    """生成行情快照 + 观点摘要报告。"""
    symbol = symbol.strip().upper()
    if not symbol:
        return {"error": "symbol required"}

    # yfinance 是阻塞库，放到线程池执行
    data = await asyncio.to_thread(_fetch_quote, symbol)
    if data is None:
        return {"error": f"symbol {symbol} not found"}

    return {
        "symbol": symbol,
        "quote": data,
        "view": _build_view(data),
        "disclaimer": "此报告为行情快照与规则化观点摘要，非投资建议。",
    }


def _fetch_quote(symbol: str):
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        history = ticker.history(period="5d")
        last_close = history["Close"].iloc[-1] if not history.empty else None
        prev_close = history["Close"].iloc[-2] if len(history) > 1 else None

        change_pct = None
        if last_close and prev_close:
            change_pct = round((last_close - prev_close) / prev_close * 100, 2)

        return {
            "price": round(last_close, 4) if last_close else None,
            "change_pct_1d": change_pct,
            "market_cap": getattr(info, "market_cap", None),
            "currency": getattr(info, "currency", None),
            "period": "5d",
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


def _build_view(data: dict) -> str:
    """基于快照生成规则化观点摘要（后续可替换为真实多渠道观点采集）。"""
    if "error" in data:
        return "行情拉取失败，无法生成观点。"

    price = data.get("price")
    chg = data.get("change_pct_1d")
    if price is None:
        return "暂无有效行情数据。"

    parts = [f"当前价格 {price} {data.get('currency', '')}"]
    if chg is not None:
        direction = "上涨" if chg >= 0 else "下跌"
        parts.append(f"近 5 日收盘较前一日{direction} {abs(chg)}%")
    if data.get("market_cap"):
        parts.append(f"市值约 {data['market_cap'] / 1e9:.2f}B")

    parts.append("建议结合公司财报、行业景气度与多空情绪进一步研判。")
    return "；".join(parts)
