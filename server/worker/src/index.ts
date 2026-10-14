/**
 * x402 收费行情报告服务 — Cloudflare Worker 版（x402 v2 + CDP Facilitator）
 *
 * 路由: GET /report/:symbol  → $0.001/次（exact scheme，Base 主网）
 * 结算: CDP Facilitator（https://api.cdp.coinbase.com/platform/v2/x402）
 * 收款: NEAR 派生 Base 地址（用户确认）
 *       0x3e9e790e4b54e1e885da833a45041021fc027394
 *
 * 部署要点（重要）:
 * - Cloudflare Worker 的 secrets 只在 fetch 的 env 参数中暴露，模块顶层 process.env 拿不到，
 *   因此 x402 server 必须「惰性初始化」：首次请求时从 c.env 读取 CDP 凭证并显式传给
 *   createCdpFacilitatorClient，再初始化 server。禁止在模块顶层 await createX402Server()
 *   或同步 initialize()（会导致 wrangler deploy 的模块验证阶段失败）。
 *
 * 验证: curl -i https://x402-report.quanyida.workers.dev/report/NVDA  → 402 + PAYMENT-REQUIRED
 */
import { Hono } from "hono";
import { createCdpFacilitatorClient } from "@coinbase/cdp-sdk/x402";
import { paymentMiddleware } from "@x402/hono";
import { x402ResourceServer } from "@x402/core/server";
import { ExactEvmScheme } from "@x402/evm/exact/server";
// Bazaar 发现扩展：让服务被 CDP Bazaar 索引（等价于 createX402Server 的自动注册）
import { bazaarResourceServerExtension, declareDiscoveryExtension } from "@x402/extensions/bazaar";
// 关键：显式 import jose 并触发其模块初始化链。
// jose 声明 sideEffects:false，esbuild 打包时可能摇掉 jose 内部的惰性 init（JWS 算法表），
// 导致 CDP SDK 生成 EdDSA JWT 时 keyAlgorithm("EdDSA") 读到 undefined。这里 import 一次
// 并实际调用一个导出，强制 esbuild 保留并执行 jose 的初始化。
import { importJWK } from "jose";

// 预热 jose 初始化链（workerd 下 esbuild 可能跳过 jose 的 __esm init）
async function warmupJose() {
  try {
    await importJWK({ kty: "OKP", crv: "Ed25519", x: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", d: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" }, "EdDSA");
  } catch {
    // 只需要触发初始化链，具体 key 无效无所谓
  }
}

// ---- Crypto polyfill（Workerd nodejs_compat 下 node:crypto.webcrypto 缺 getRandomValues）----
// uncrypto 在 node 条件下取 nodeCrypto.webcrypto.getRandomValues，Workerd 实现不完整。
// 把全局 Web Crypto 的 getRandomValues 桥接到 nodeCrypto.webcrypto。
// eslint-disable-next-line @typescript-eslint/no-var-requires
import nodeCrypto from "node:crypto";
import { getRandomValues as uncryptoGRV } from "uncrypto";
const _g = (globalThis as { crypto?: Crypto }).crypto;
const _wc = (nodeCrypto as unknown as { webcrypto?: Crypto }).webcrypto;
if (_wc && _g && typeof (_wc as unknown as Record<string, unknown>).getRandomValues !== "function") {
  Object.defineProperty(_wc, "getRandomValues", {
    value: _g.getRandomValues.bind(_g),
    configurable: true,
    writable: true,
  });
}
console.log(
  `[x402] polyfill: typeof globalCrypto.getRandomValues=${typeof _g?.getRandomValues}; ` +
  `typeof nodeCrypto.webcrypto.getRandomValues=${typeof _wc?.getRandomValues}; ` +
  `sameObject=${_g === _wc}; typeof uncryptoGRV=${typeof uncryptoGRV}`,
);

// ---- 收款地址（用户确认的 NEAR 派生 Base 地址）----
const PAY_TO_EVM = "0x3e9e790e4b54e1e885da833a45041021fc027394";

// ---- symbol 白名单（防滥用：只允许已知美股/加密代码）----
const STOCK_SYMBOLS = new Set([
  "NVDA", "AAOI", "AXTI", "MARA", "RIOT", "COIN", "HOOD", "TSLA",
  "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NFLX", "AMD", "INTC",
]);
const CRYPTO_SYMBOLS = new Set([
  "BTC", "ETH", "SOL", "NEAR", "TAO", "USDC", "USDT", "BNB", "XRP",
  "DOGE", "ADA", "AVAX", "LINK", "DOT", "MATIC", "ARB", "OP",
]);
const ALLOWED_SYMBOLS = new Set([...STOCK_SYMBOLS, ...CRYPTO_SYMBOLS]);

function isValidSymbol(s: string): boolean {
  return ALLOWED_SYMBOLS.has(s);
}

// ---- 数据源：CoinGecko coin id 映射（加密）----
const COIN_IDS: Record<string, string> = {
  BTC: "bitcoin", ETH: "ethereum", SOL: "solana", NEAR: "near",
  TAO: "bittensor", USDC: "usd-coin", USDT: "tether", BNB: "binancecoin",
  XRP: "ripple", DOGE: "dogecoin", ADA: "cardano", AVAX: "avalanche-2",
  LINK: "chainlink", DOT: "polkadot", MATIC: "matic-network", ARB: "arbitrum",
  OP: "optimism",
};

// ---- 行情抓取（Worker 侧直连，无需本地代理）----
const FETCH_TIMEOUT_MS = 10_000;

async function fetchJson(url: string): Promise<unknown | null> {
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0 (x402-report-worker)" },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// 加密批量行情：一次拉全部白名单币（CoinGecko 免费档限流 ~30/min，批量+缓存规避）
let cryptoQuotesCache: { at: number; data: Record<string, { usd?: number; usd_24h_change?: number | null }> } | null = null;
const CRYPTO_CACHE_TTL_MS = 30_000;

async function fetchCryptoQuotes(): Promise<Record<string, { usd?: number; usd_24h_change?: number | null }>> {
  const now = Date.now();
  if (cryptoQuotesCache && now - cryptoQuotesCache.at < CRYPTO_CACHE_TTL_MS) {
    return cryptoQuotesCache.data;
  }
  const ids = [...CRYPTO_SYMBOLS].map((s) => COIN_IDS[s]).join(",");
  const url = `https://api.coingecko.com/api/v3/simple/price?ids=${ids}&vs_currencies=usd&include_24hr_change=true`;
  const data = (await fetchJson(url)) as Record<string, { usd?: number; usd_24h_change?: number | null }> | null;
  if (data) {
    cryptoQuotesCache = { at: now, data };
  } else if (cryptoQuotesCache) {
    // 拉取失败回退到旧批量缓存（stale）
    return cryptoQuotesCache.data;
  }
  return data ?? {};
}

// 美股单票行情：Yahoo Finance chart API（免费无 key）
async function fetchStockQuote(symbol: string) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?interval=1d&range=5d`;
  const data = (await fetchJson(url)) as {
    chart?: { result?: Array<{ meta?: Record<string, unknown> }> };
  } | null;
  const meta = data?.chart?.result?.[0]?.meta;
  if (!meta || typeof meta.regularMarketPrice !== "number") return null;
  return {
    price: meta.regularMarketPrice as number,
    change_24h_pct: typeof meta.regularMarketChangePercent === "number" ? (meta.regularMarketChangePercent as number) : null,
    name: (meta.longName as string) ?? (meta.shortName as string) ?? symbol,
    currency: (meta.currency as string) ?? "USD",
    high_52w: typeof meta.fiftyTwoWeekHigh === "number" ? (meta.fiftyTwoWeekHigh as number) : null,
    low_52w: typeof meta.fiftyTwoWeekLow === "number" ? (meta.fiftyTwoWeekLow as number) : null,
  };
}

async function buildQuote(symbol: string) {
  if (STOCK_SYMBOLS.has(symbol)) {
    const q = await fetchStockQuote(symbol);
    if (!q) return null;
    return {
      symbol,
      type: "stock",
      name: q.name,
      price: q.price,
      change_24h_pct: q.change_24h_pct,
      currency: q.currency,
      high_52w: q.high_52w,
      low_52w: q.low_52w,
      source: "Yahoo Finance (query1.finance.yahoo.com)",
    };
  }
  const quotes = await fetchCryptoQuotes();
  const item = quotes[COIN_IDS[symbol]];
  if (!item || typeof item.usd !== "number") return null;
  return {
    symbol,
    type: "crypto",
    name: symbol,
    price: item.usd,
    change_24h_pct: typeof item.usd_24h_change === "number" ? item.usd_24h_change : null,
    currency: "USD",
    high_52w: null,
    low_52w: null,
    source: "CoinGecko (api.coingecko.com)",
  };
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
    for (const [k, v] of cache) {
      if (Date.now() - v.at > CACHE_TTL_MS) cache.delete(k);
    }
  }
}

// ---- x402 路由配置（exact scheme，Base 主网）----
// /report/:symbol  $0.001/次 行情快照
// /smartmoney-brief $0.05/次 聪明钱每日简报完整版（内容存于 KV SMARTMONEY_BRIEF）
const routes = {
  "GET /report/:symbol": {
    accepts: {
      scheme: "exact",
      payTo: PAY_TO_EVM,
      price: "$0.001",
      network: "eip155:8453" as const,
    },
    serviceName: "Market Quote Snapshot",
    tags: ["stock", "crypto", "market-data", "quote", "price"],
    description:
      "Real-time US stock & crypto price snapshots with 24h change, 52-week highs/lows and data source. Use when an agent needs current market prices for a whitelisted symbol such as NVDA, BTC or NEAR. Pay $0.001 USDC per call on Base mainnet.",
    extensions: {
      ...declareDiscoveryExtension({
        pathParams: { symbol: "NVDA" },
        pathParamsSchema: {
          type: "object",
          properties: {
            symbol: {
              type: "string",
              description:
                "Stock or crypto ticker symbol from the whitelist (e.g. NVDA, AAOI, BTC, ETH, SOL, NEAR, TAO). Use /report/:symbol with the uppercase symbol.",
            },
          },
          required: ["symbol"],
        },
        output: {
          example: {
            symbol: "NVDA",
            type: "stock",
            name: "NVIDIA Corporation",
            price: 229.28,
            change_24h_pct: -0.52,
            currency: "USD",
            high_52w: 243.37,
            low_52w: 164.27,
            source: "Yahoo Finance",
            generated_at: "2026-10-12T09:00:00.000Z",
          },
        },
      }),
    },
  },
  "GET /smartmoney-brief": {
    accepts: {
      scheme: "exact",
      payTo: PAY_TO_EVM,
      price: "$0.05",
      network: "eip155:8453" as const,
    },
    serviceName: "Smart Money Daily Brief",
    tags: ["smart-money", "crypto", "onchain", "intelligence"],
    description:
      "Daily smart-money brief for ETH/Base/Solana: 24h net asset changes across monitored onchain addresses with smart-money interpretation. Use when an agent needs an onchain whale/flow summary for a trading day. Pay $0.05 USDC per call on Base mainnet.",
    extensions: {
      ...declareDiscoveryExtension({
        pathParams: {},
        output: {
          example: {
            date: "2026-10-14",
            generated_at: "2026-10-14 14:34",
            content: "（Markdown 全文，此处省略）",
          },
        },
      }),
    },
  },
};

// ---- 惰性单例：首次请求时从 env 读 CDP 凭证，初始化 x402 server ----
type Env = {
  CDP_API_KEY_ID?: string;
  CDP_API_KEY_SECRET?: string;
  SMARTMONEY_BRIEF?: KVNamespace;
};

let serverPromise: Promise<x402ResourceServer> | null = null;

async function getServer(env: Env): Promise<x402ResourceServer> {
  if (!serverPromise) {
    serverPromise = (async () => {
      const wc = (nodeCrypto as unknown as { webcrypto?: Crypto }).webcrypto;
      console.log(
        `[x402] init start; keyId=${env.CDP_API_KEY_ID ? "present" : "MISSING"}; ` +
        `globalCrypto.getRandomValues=${typeof _g?.getRandomValues}; ` +
        `nodeCrypto.webcrypto.getRandomValues=${typeof wc?.getRandomValues}; ` +
        `sameObject=${_g === wc}`,
      );
      const facilitator = createCdpFacilitatorClient({
        apiKeyId: env.CDP_API_KEY_ID,
        apiKeySecret: env.CDP_API_KEY_SECRET,
      });
      // 预热 jose 初始化链（防止 esbuild 摇掉 JWS 算法表导致 EdDSA JWT 失败）
      try {
        await warmupJose();
      } catch {
        // 预热失败不影响继续（initialize 会再次尝试并报真实错误）
      }
      const server = new x402ResourceServer(facilitator);
      server.register("eip155:8453", new ExactEvmScheme());
      // 注册 Bazaar 发现扩展（对应 createX402Server 的自动 discovery 注册）
      try {
        server.registerExtension(bazaarResourceServerExtension);
        console.log("[x402] bazaar extension registered");
      } catch (e) {
        console.error("[x402] bazaar extension register failed:", JSON.stringify(e, Object.getOwnPropertyNames(e)));
      }
      try {
        await server.initialize();
      } catch (e) {
        console.error("[x402] initialize failed:", JSON.stringify(e, Object.getOwnPropertyNames(e)));
        console.error("[x402] cause chain:", (e as Error).stack);
        throw e;
      }
      return server;
    })();
  }
  return serverPromise;
}

const app = new Hono<{ Bindings: Env }>();

// x402 支付中间件（惰性初始化；必须在受保护路由之前注册）
app.use(async (c, next) => {
  const server = await getServer(c.env);
  const mw = paymentMiddleware(routes, server, undefined, undefined, false);
  return mw(c, next);
});

app.get("/health", (c) => c.json({ status: "ok" }));

// ---- OpenAPI 3.0 spec（Circle Agent Marketplace 上架前置；托管于服务自身）----
const OPENAPI_SPEC = {
  openapi: "3.0.3",
  info: {
    title: "x402-report — AI 行情快照与观点摘要服务",
    version: "0.2.0",
    description:
      "按次付费（x402 / USDC）的行情数据与观点摘要 API：输入美股或加密货币代码（NVDA / AAOI / BTC / NEAR / TAO 等白名单），返回价格、24h 涨跌、52 周高低点及数据来源。Base 主网 USDC 结算，$0.001/次。",
    "x-provider": {
      name: "quanyida",
      category: "FINANCIAL_ANALYSIS",
      description:
        "AI 行情快照与观点摘要：美股 + 加密双市场，支持股票报价（Yahoo Finance）与加密批量行情（CoinGecko），返回结构化 JSON，适合 Agent 做行情判断与投研素材。",
    },
  },
  servers: [{ url: "https://x402-report.quanyida.workers.dev" }],
  paths: {
    "/report/{symbol}": {
      get: {
        summary: "获取指定美股/加密代码的行情快照与观点摘要",
        description:
          "未付费请求返回 402 Payment Required + PAYMENT-REQUIRED 头（x402 v2，含 accepts 价目表）；带有效 USDC 付款后返回行情 JSON。Base 主网，$0.001/次。",
        parameters: [
          {
            name: "symbol",
            in: "path",
            required: true,
            description: "美股或加密货币代码（NVDA / AAOI / AXTI / BTC / ETH / SOL / NEAR / TAO 等）",
            schema: { type: "string", enum: [...ALLOWED_SYMBOLS] },
          },
        ],
        responses: {
          "200": {
            description: "付费成功，返回行情快照",
            content: {
              "application/json": {
                schema: {
                  type: "object",
                  properties: {
                    symbol: { type: "string" },
                    type: { type: "string", enum: ["stock", "crypto"] },
                    name: { type: "string" },
                    price: { type: ["number", "null"] },
                    change_24h_pct: { type: ["number", "null"] },
                    currency: { type: "string" },
                    high_52w: { type: ["number", "null"] },
                    low_52w: { type: ["number", "null"] },
                    source: { type: ["string", "null"] },
                    generated_at: { type: "string", format: "date-time" },
                  },
                  required: ["symbol", "type"],
                },
              },
            },
          },
          "400": { description: "非法 symbol（不在白名单）" },
          "402": { description: "Payment Required — 需附 x402 付款头" },
        },
      },
    },
    "/health": {
      get: { summary: "健康检查", responses: { "200": { description: "ok" } } },
    },
  },
};

app.get("/openapi.json", (c) => c.json(OPENAPI_SPEC));

app.get("/report/:symbol", async (c) => {
  const symbol = c.req.param("symbol").toUpperCase();

  if (!isValidSymbol(symbol)) {
    return c.json(
      { error: `unsupported symbol '${symbol}'; allowed: ${[...ALLOWED_SYMBOLS].join(", ")}` },
      400,
    );
  }

  const cached = getCached(symbol);
  if (cached !== undefined) return c.json(cached);

  const quote = await buildQuote(symbol);
  const report = quote ?? {
    symbol,
    type: STOCK_SYMBOLS.has(symbol) ? "stock" : "crypto",
    name: symbol,
    price: null,
    change_24h_pct: null,
    currency: "USD",
    high_52w: null,
    low_52w: null,
    source: null,
    error: "行情数据源暂不可用（Yahoo Finance / CoinGecko 超时或限流），请稍后重试",
    generated_at: new Date().toISOString(),
  };

  setCached(symbol, report);
  return c.json(report);
});

// ---- 聪明钱每日简报完整版（付费 $0.05 解锁；内容存于 KV）----
app.get("/smartmoney-brief", async (c) => {
  const kv = c.env.SMARTMONEY_BRIEF;
  if (!kv) {
    return c.json({ error: "KV not bound" }, 500);
  }
  try {
    const raw = await kv.get("latest_brief_full", "text");
    if (!raw) {
      return c.json({ error: "简报尚未生成，请稍后再试" }, 404);
    }
    const parsed = JSON.parse(raw);
    return c.json(parsed);
  } catch (e) {
    return c.json({ error: "读取简报失败: " + String(e) }, 500);
  }
});

export default app;
