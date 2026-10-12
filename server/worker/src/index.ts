/**
 * x402 收费行情报告服务 — Cloudflare Worker 版（x402 v2 + CDP Facilitator）
 *
 * 路由: GET /report/:symbol  → $0.001/次（exact scheme，Base 主网）
 * 结算: CDP Facilitator（https://api.cdp.coinbase.com/platform/v2/x402）
 * 收款: NEAR 派生 Base 地址（用户确认）
 *       0x3e9e790e4b54e1e885da833a45041021fc027394
 *
 * 部署: wrangler deploy（需 CDP_API_KEY_ID / CDP_API_KEY_SECRET 环境变量）
 * 验证: curl -i https://<worker>.workers.dev/report/NVDA  → 402 + PAYMENT-REQUIRED
 */
import { Hono } from "hono";
import { createX402Server } from "@coinbase/cdp-sdk/x402";
import { paymentMiddlewareFromHTTPServer } from "@x402/hono";

// ---- 收款地址（用户确认的 NEAR 派生 Base 地址）----
const PAY_TO_EVM = "0x3e9e790e4b54e1e885da833a45041021fc027394";

// ---- symbol 白名单（防滥用：只允许已知美股/加密代码）----
const ALLOWED_SYMBOLS = new Set([
  // 美股（示例子集）
  "NVDA", "AAOI", "AXTI", "MARA", "RIOT", "COIN", "HOOD", "TSLA",
  "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NFLX", "AMD", "INTC",
  // 加密
  "BTC", "ETH", "SOL", "NEAR", "TAO", "USDC", "USDT", "BNB", "XRP",
  "DOGE", "ADA", "AVAX", "LINK", "DOT", "MATIC", "ARB", "OP",
]);

function isValidSymbol(s: string): boolean {
  return ALLOWED_SYMBOLS.has(s);
}

// ---- 简易缓存（同 symbol 60 秒内返回相同结果，不重复计费）----
const cache = new Map<string, { at: number; body: unknown }>();
const CACHE_TTL_MS = 60_000;

function getCached(symbol: string): unknown | undefined {
  const hit = cache.get(symbol);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.body;
  return undefined;
}

function setCached(symbol: string, body: unknown): void {
  cache.set(symbol, { at: Date.now(), body });
  if (cache.size > 500) {
    // 简单清理
    for (const [k, v] of cache) {
      if (Date.now() - v.at > CACHE_TTL_MS) cache.delete(k);
    }
  }
}

// ---- 创建 x402 服务器（生产环境 = Base 主网；收款地址显式指定）----
const server = await createX402Server({
  environment: "production",
  payToConfig: { type: "address", evm: PAY_TO_EVM },
  builderCode: "x402_report", // 链上归属代码（可选）
  routes: {
    "GET /report/:symbol": {
      price: "$0.001",
      scheme: "exact",
      description: "行情快照与观点摘要：输入美股/加密代码，返回价格、涨跌及简要观点（$0.001/次，Base 主网 USDC）",
    },
  },
});

const app = new Hono();

// x402 支付中间件（必须在受保护路由之前注册）
app.use(paymentMiddlewareFromHTTPServer(server));

app.get("/health", (c) => c.json({ status: "ok" }));

app.get("/report/:symbol", async (c) => {
  const symbol = c.req.param("symbol").toUpperCase();

  if (!isValidSymbol(symbol)) {
    return c.json(
      { error: `unsupported symbol '${symbol}'; allowed: ${[...ALLOWED_SYMBOLS].join(", ")}` },
      400,
    );
  }

  // 命中缓存则不重新生成（已付款的调用结果可复用，避免重复计费场景）
  const cached = getCached(symbol);
  if (cached !== undefined) return c.json(cached);

  const report = {
    symbol,
    price: null as number | null,
    change_24h_pct: null as number | null,
    note: "行情数据源待接入（当前为占位报告）。后续可接自有 Python 后端 / 币安 / CoinGecko / Alpha Vantage。",
    generated_at: new Date().toISOString(),
  };

  // 尝试从 CoinGecko 免费接口获取加密币价（尽力而为，失败则返回占位报告）
  if (["BTC", "ETH", "SOL", "NEAR", "TAO", "USDC", "USDT", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "ARB", "OP"].includes(symbol)) {
    const coinId: Record<string, string> = {
      BTC: "bitcoin", ETH: "ethereum", SOL: "solana", NEAR: "near",
      TAO: "bittensor", USDC: "usd-coin", USDT: "tether", BNB: "binancecoin",
      XRP: "ripple", DOGE: "dogecoin", ADA: "cardano", AVAX: "avalanche-2",
      LINK: "chainlink", DOT: "polkadot", ARB: "arbitrum", OP: "optimism",
    };
    try {
      const r = await fetch(
        `https://api.coingecko.com/api/v3/simple/price?ids=${coinId[symbol]}&vs_currencies=usd&include_24hr_change=true`,
        { headers: { "User-Agent": "x402-report-worker" } },
      );
      if (r.ok) {
        const data = (await r.json()) as Record<string, { usd?: number; usd_24h_change?: number }>;
        const item = data[coinId[symbol]];
        if (item) {
          report.price = item.usd ?? null;
          report.change_24h_pct = item.usd_24h_change ?? null;
          report.note = "价格来源：CoinGecko 免费公开接口";
        }
      }
    } catch {
      // 保持占位报告
    }
  }

  setCached(symbol, report);
  return c.json(report);
});

export default app;
