# x402-agent

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
