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
    for (const [k, v] of cache) {
      if (Date.now() - v.at > CACHE_TTL_MS) cache.delete(k);
    }
  }
}

// ---- x402 路由配置（exact scheme，Base 主网，$0.001/次）----
const routes = {
  "GET /report/:symbol": {
    accepts: {
      scheme: "exact",
      payTo: PAY_TO_EVM,
      price: "$0.001",
      network: "eip155:8453" as const,
    },
    description:
      "行情快照与观点摘要：输入美股/加密代码，返回价格、涨跌及简要观点（$0.001/次，Base 主网 USDC）",
    extensions: {
      ...declareDiscoveryExtension({
        pathParams: { symbol: "NVDA" },
        pathParamsSchema: {
          type: "object",
          properties: {
            symbol: {
              type: "string",
              description: "美股或加密货币代码（NVDA / BTC / NEAR 等）",
            },
          },
          required: ["symbol"],
        },
        output: {
          example: {
            symbol: "NVDA",
            price: 138.42,
            change_24h_pct: 1.23,
            note: "价格来源：CoinGecko 免费公开接口",
            generated_at: "2026-10-12T08:00:00.000Z",
          },
        },
      }),
    },
  },
};

// ---- 惰性单例：首次请求时从 env 读 CDP 凭证，初始化 x402 server ----
type Env = { CDP_API_KEY_ID?: string; CDP_API_KEY_SECRET?: string };

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

  const report = {
    symbol,
    price: null as number | null,
    change_24h_pct: null as number | null,
    note: "行情数据源待接入（当前为占位报告）。后续可接自有 Python 后端 / 币安 / CoinGecko / Alpha Vantage。",
    generated_at: new Date().toISOString(),
  };

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
