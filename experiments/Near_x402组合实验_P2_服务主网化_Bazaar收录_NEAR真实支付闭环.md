# Near×x402 组合实验 P2：服务主网化 + Bazaar 收录 + NEAR 真实支付闭环

> 日期：2026-10-12
> 仓库：quanyida/x402-agent（server/worker/ + skills/x402-bazaar-client/）
> 状态：✅ 主网服务上线、Bazaar 收录、NEAR 真实付款闭环
> 方向 A：服务端上架（固定定价 / 校验缓存 / 注册 Bazaar / NEAR skill 调一次）

## 1. 目标

把自建的 x402 报告服务（Cloudflare Worker）从测试网升级为**主网收费服务**，并完成"卖服务＋买服务"闭环：

```
Seller: Cloudflare Worker x402-report（Base 主网 eip155:8453，$0.001/次）
    │  收款地址 = NEAR MPC 派生 Base 地址 0x3e9e...394（用户确认）
    ▼
Buyer: NEAR 账户（MPC 链签名）→ 派生 EVM 地址 → EIP-712 exact 付款
    ▼
闭环：HTTP 200 + 收款到账 + Bazaar 注册中心收录
```

## 2. 服务端改造（server/worker/src/index.ts）

| 改动点 | 说明 |
|---|---|
| 惰性单例初始化 | Workerd 下模块顶层 `await createX402Server()` 会在部署验证期失败；改为 `getServer(c.env)` 首次请求初始化并缓存 promise |
| jose warmup 修复 | jose 声明 `sideEffects:false`，esbuild 摇掉 JWS 惰性初始化链 → CDP SDK 生成 EdDSA JWT 时 `JWS["EdDSA"]` undefined；显式 `import { importJWK } from "jose"` + `warmupJose()`（无效 Ed25519 JWK 触发初始化链，失败忽略） |
| crypto polyfill | Workerd 的 `node:crypto.webcrypto` 缺 `getRandomValues` → 把全局 Web Crypto 的 `getRandomValues` 桥接到 nodeCrypto.webcrypto |
| 路由定价 | `GET /report/:symbol`：`exact` scheme、`$0.001/次`、`payTo=0x3e9e...394`、`network=eip155:8453` |
| Bazaar 注册 | 手动 `server.registerExtension(bazaarResourceServerExtension)` + 路由 `extensions: {...declareDiscoveryExtension({...})}`（`createX402Server` 的自动注册不适用于手动装配） |
| 白名单 + 缓存 | symbol 白名单校验 + 60s 缓存 |

## 3. 官方 validate（CDP Bazaar）

```
POST https://api.cdp.coinbase.com/platform/v2/x402/validate
{"resource":"https://x402-report.quanyida.workers.dev/report/NVDA","method":"GET"}
```

- 结果：`valid:true`、`simulation.outcome:"accepted"`、`statusCode:402`、`x402Version:2`
- 所有 required 检查 passed：URL/HTTPS/返回 402/JSON/x402 v2/PAYMENT-REQUIRED 头/exact scheme/network eip155:8453/USDC/amount≥1000/payTo 存在/Bazaar extension 全绿/routeTemplate `/report/:symbol` 匹配

## 4. NEAR 真实支付（$0.001 主网）

命令（本地生效副本 scripts/x402_bazaar.py）：

```
python x402_bazaar.py call-url https://x402-report.quanyida.workers.dev/report/NVDA \
  --network eip155:8453 --mainnet --signer near \
  --near-account <id> --near-key-file <keyfile> \
  --near-rpc https://free.rpc.fastnear.com --near-path ethereum-2 --amount 0.001 --yes
```

### 4.1 踩坑记录

| # | 症状 | 根因 | 修复 |
|---|---|---|---|
| 1 | key 文件 `Can't decode private key ed25519:2...` | PowerShell `Set-Content -Encoding UTF8` 写入带 BOM | `[IO.File]::WriteAllText(..., UTF8Encoding($false))` 无 BOM 写（96 字节） |
| 2 | `Cannot connect to host rpc.mainnet.near.org:443` | 大陆直连 NEAR 官方 RPC 超时 | `--near-rpc https://free.rpc.fastnear.com` |
| 3 | `HTTP 402` + body `{}` + `paid.tx=` 空 | **self_send_not_allowed**：付款方 = 收款方（收款地址 `0x3e9e...394` 就是 NEAR 账户 ethereum-1 派生地址，自己付自己被 CDP Facilitator 拒绝） | 换 `--near-path ethereum-2` 派生**不同**付款地址 |

### 4.2 成功输出

```
paying from: 0xfde70a3b75f31e890b1c69f7661bb34367ccd4f3  (NEAR <account> via MPC)
[fund] balance $0.0000 < $0.0010; auto-funding from NEAR assets (1Click) ...
[fund] funded:SUCCESS tx=Q8qozhTVQ42bZry9okW2LsrAFaaw9E6tdba8ZsQYLG1
HTTP 200
{
  "symbol": "NVDA",
  "price": null,
  "change_24h_pct": null,
  "note": "行情数据源待接入（当前为占位报告）...",
  "generated_at": "2026-10-12T09:00:49.014Z"
}
```

- 付款方派生地址无 USDC → **1Click 自动注资**（NEAR 资产 → Base USDC，`tx=Q8qozh...`）
- 最终 **HTTP 200**：付款验证 + 结算通过，服务返回报告

## 5. 收款到账验证

| 时间点 | 收款地址 USDC | 变化 |
|---|---|---|
| 付款前 | $0.191506 | — |
| 付款后 | $0.192506 | **+$0.001**（链上 settle 到账） |

## 6. Bazaar 收录确认

- discovery 接口全量翻页（32,250+ 条资源），在 **offset 32000** 找到：
  - `resource: https://x402-report.quanyida.workers.dev/report/:symbol`
  - `network: eip155:8453`、`amount: 1000`（$0.001）、`payTo: 0x3e9e...394`
  - `lastUpdated: 2026-10-12T09:00:49.939Z` —— **正好是成功付费时刻**
- 结论：**一次成功付费（settle 带 extensions.bazaar + resource）触发 Bazaar 索引**，文档所述收录机制验证成立

## 7. Git 提交

- commit `c8348ec`：`worker: bazaar discovery registration + jose warmup fix; verified paid call`
- 3 文件：`server/worker/src/index.ts` / `package.json` / `package-lock.json`
- `debug-local.mjs`（本地调试）未入库

## 8. 结论与边界

- ✅ **主网化 + Bazaar 收录 + NEAR 真实支付全链路闭环**（HTTP 200、$0.001 到账、注册中心可见）
- ⚠️ **Workerd ajv 坑**：Bazaar 声明里的 JSON Schema 在 Cloudflare 编译报 `Code generation from strings disallowed`（warn，不影响功能；留意后续 @x402/extensions 版本升级）
- ⚠️ 付款方与收款方同为 NEAR 账户派生地址时会被拒（self_send_not_allowed），自测须用不同派生路径
- ❌ 未做：报告接口真实行情数据源（当前为占位报告，可接 CoinGecko / Binance / Alpha Vantage）
