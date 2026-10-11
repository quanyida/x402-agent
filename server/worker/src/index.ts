/**
 * x402 收费行情报告服务 — Cloudflare Worker 版
 *
 * 路由: GET /report/:symbol  → $0.01/次（exact scheme，Base Sepolia）
 * 结算: 经 x402.org facilitator（免费测试网 facilitator，无需 API key）
 * 收款: x402-report-receiver (0x6D8F5690D773c68b566CA04F6a63F69740166a39)
 *
 * 部署: wrangler deploy
 * 验证: curl -i https://<worker>.workers.dev/report/NVDA  → 402 + PAYMENT-REQUIRED
 */
import { Hono } from "hono";
import { paymentMiddleware } from "x402-hono";

interface Env {
  PAY_TO?: string; // 收款钱包地址（默认用内置测试地址）
  NETWORK?: string; // 默认 base-sepolia
  FACILITATOR_URL?: string; // 默认 https://x402.org/facilitator
}

const DEFAULT_PAY_TO = "0x6D8F5690D773c68b566CA04F6a63F69740166a39"; // x402-report-receiver
const DEFAULT_NETWORK = "base-sepolia";
const DEFAULT_FACILITATOR = "https://x402.org/facilitator";

const app = new Hono<{ Bindings: Env }>();

// 动态构造 paymentMiddleware（读取 wrangler vars，方便切主网/换钱包）
app.use(async (c, next) => {
  const payTo = (c.env.PAY_TO || DEFAULT_PAY_TO) as `0x${string}`;
  const network = (c.env.NETWORK || DEFAULT_NETWORK) as
    | "base"
    | "base-sepolia"
    | "polygon"
    | "solana"
    | "solana-devnet";
  const facilitatorUrl = (c.env.FACILITATOR_URL ||
    DEFAULT_FACILITATOR) as `${string}://${string}`;

  const mw = paymentMiddleware(
    payTo,
    {
      "/report/*": {
        price: "$0.01",
        network,
        config: {
          description:
            "行情快照与观点摘要报告：输入美股/加密代码，返回价格、涨跌及简要市场观点（$0.01/次）",
        },
      },
    },
    { url: facilitatorUrl },
  );
  return mw(c, next);
});

app.get("/health", (c) => c.json({ status: "ok" }));

app.get("/report/:symbol", async (c) => {
  const symbol = c.req.param("symbol").toUpperCase();

  // 简化版报告（Worker 无 Python 行情源）：
  // 完整版可在此调用外部行情 API（如自有 Python 后端 / Alpha Vantage / Binance 等）
  const report = {
    symbol,
    note: "x402 Worker 版简化报告。完整行情+观点版见本地 FastAPI 服务。",
    generated_at: new Date().toISOString(),
  };

  return c.json(report);
});

export default app;
