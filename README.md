# x402-agent

## ⚡ Smart Money Brief — 聪明钱每日简报（付费服务）

> **30 秒看懂 Hyperliquid Top 50 大账户在干嘛**：永续多空净敞口 + 24h 增量变化（delta）。数据驱动，机器可读，直接喂给 Agent 或人工速读。

- **定价**：`$0.05 / 次`（Base 主网 USDC，x402 协议自动结算）
- **端点**：`GET https://x402-report.quanyida.workers.dev/smartmoney-brief`
- **支付**：NEAR 链签名 或 CDP USDC 钱包均可（用 NEAR 账户付款无需注册 CDP）
- **上架**：Coinbase CDP Bazaar / Circle Agent Marketplace / Cloudflare Worker

### 你今天会得到什么（2026-10-15 真实输出）

| 币种 | 动作 | 变化 |
|---|---|---|
| BTC | 加空 | $-42.13M |
| LIT | 回补(减空) | $+10.30M |
| HYPE | 翻多 | $+8.95M |
| XRP | 翻多 | $+6.91M |
| SOL | 加空 | $-6.83M |
| ETH | 回补(减空) | $+4.83M |

完整版包含：Top 50 大账户逐币种多空净敞口表、方向信号、delta 明细、规则化解读、明日关注点。

### 怎么调用

```bash
# 方式一：curl 直连（返回 402 + 付款要求，用 x402 客户端自动支付）
curl -i https://x402-report.quanyida.workers.dev/smartmoney-brief

# 方式二：用本仓库 skill（支持 NEAR 账户自动付款）
cd skills/x402-bazaar-client
python scripts/x402_bazaar.py list --network eip155:8453 --mainnet
python scripts/x402_bazaar.py call 0 --signer near --yes   # 按列表序号选择本服务
```

### 为什么用 NEAR 也能付？

x402-bazaar-client 支持 **NEAR Chain Signatures**：用你的 NEAR 账户派生 EVM 地址、签名 x402 付款头，余额不足时通过 1Click 自动把 NEAR 资产兑换成目标链 USDC。**没有 CDP 账号也能消费 x402 服务。**

---

## 技术部分（本仓库还包含什么）

x402 协议的学习与实践仓库：自建付费 HTTP 服务（FastAPI / FastMCP / Cloudflare Worker）、买家客户端、可复用 Skills 与全流程实验记录。

> x402 是一种基于 HTTP `402 Payment Required` 的机器间支付协议：服务端对资源定价，客户端签名支付授权并携带付款头重试，服务端验证并链上结算（Base USDC 等）后放行资源。

## 目录结构

```
x402-agent/
├── server/                  # 服务端（卖方）
│   ├── app/
│   │   ├── main.py          #   FastAPI HTTP 版（:8402，CDP Facilitator）
│   │   ├── mcp_server.py    #   FastMCP SSE 版（:4022）
│   │   └── services/        #   报告生成逻辑
│   ├── worker/              #   Cloudflare Worker（线上部署，v1 x402-hono）
│   │   ├── src/index.ts     #     Hono 入口，/report/* 定价 $0.01
│   │   ├── wrangler.jsonc
│   │   └── scripts/         #     v1 付款构造/签名脚本
│   ├── run.py
│   ├── requirements.txt
│   └── .env.example         # 凭证模板（实际 .env 不提交）
├── client/                  # 客户端（买方）
│   ├── client.py            #   HTTP 版付费调用
│   ├── mcp_client.py        #   MCP 版付费调用
│   ├── _encode_x402.js      #   v1 付款头构造（EIP-712）
│   ├── _sign_x402.js        #   cdp-cli 签名封装
│   ├── undici-proxy-preload.cjs
│   └── setup_svm.py / query_devnet.py / check_svm_balance.py  # Solana Devnet
├── skills/                  # 可复用 Skills
│   ├── x402-bazaar-client/  #   Bazaar 注册中心发现/调用
│   ├── build-x402-server/   #   服务端构建指南
│   ├── build-x402-client/   #   客户端构建指南
│   └── cdp-x402/            #   cdp CLI x402 流程
└── experiments/             # 实验记录
    ├── x402实验_AI_Agent自动付费闭环.md
    ├── x402实验二_cdp-x402_CLI手动支付闭环.md
    ├── x402实验三_Solana_Devnet_付费调用_glim_推特搜索.md
    ├── x402实验四_Cloudflare_Worker_线上部署与支付闭环.md
    └── x402注册中心调研归档.md
```

## 快速开始

### 服务端（本地 FastAPI）

```bash
pip install -r server/requirements.txt
# 配置 CDP_API_KEY_ID / CDP_API_KEY_SECRET / CDP_WALLET_SECRET（参考 server/.env.example）
python server/run.py
```

验证付费墙：

```bash
curl -i http://localhost:8402/report
# HTTP/1.1 402 Payment Required
# PAYMENT-REQUIRED: eyJ4NDAy...
```

### 客户端（付费调用）

```bash
python client/client.py          # 自动响应 402 并支付
python client/mcp_client.py      # MCP 场景
```

### Cloudflare Worker（线上）

```bash
cd server/worker
npm install
npx wrangler deploy
```

部署后付费墙：`https://<subdomain>.workers.dev/report/<symbol>`

### Bazaar 发现/调用

```bash
export CDP_API_KEY_ID=... CDP_API_KEY_SECRET=... CDP_WALLET_SECRET=...
python skills/x402-bazaar-client/scripts/x402_bazaar.py list
python skills/x402-bazaar-client/scripts/x402_bazaar.py call 0 --yes
```

## 关键经验（避免重踩）

- **x402 v1 vs v2**：x402-hono v1 路由只能用 `/report/*`（不支持 `:param`）；cdp CLI `x402 util build` 仅支持 v2（`eip155:*`），v1 付款需手工构造 EIP-712 `TransferWithAuthorization`（types 必须含 `EIP712Domain`）。
- **Cloudflare error 1010** 会拦截代理/数据中心 IP，线上验证只能走真实浏览器。
- **Bazaar 注册机制**：无注册表单，通过 CDP Facilitator 成功结算后自动索引；30 天无结算会被下架。
- **facilitator 二选一**：x402.org（测试网，免费）与 CDP Facilitator（需 API key）是两个独立目录，只有 CDP 结算才进 Coinbase Bazaar。

## 说明

- 仓库内的 `.env.example` 为模板；真实 `.env`、`svm_keypair.json` 等私钥文件已被 `.gitignore` 排除，切勿提交。
- 实验文档以中文记录，供学习与回顾。
