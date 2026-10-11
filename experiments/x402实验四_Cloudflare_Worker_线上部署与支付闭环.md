# x402 实验四：Cloudflare Worker 线上部署与支付闭环

> 日期：2026-10-11
> 目标：把自建 x402 付费报告服务部署到 Cloudflare（Worker 方案），实现「公网可达 + 边缘 402 结算」，让 Agent 真实付费调用。

---

## 一、部署架构

```
Agent / 浏览器 (买家)
   │  GET /report/NVDA（无付款头）
   ▼
Cloudflare Worker  https://x402-report.quanyida.workers.dev
   │  x402-hono v1 paymentMiddleware → 返回 402 + 付款要求(accepts)
   ▼
买家构造 X-PAYMENT header（EIP-712 签名）
   │  GET /report/NVDA + X-PAYMENT
   ▼
Worker 验证付款 → x402.org facilitator 结算 → 返回 200 + 报告 JSON
   ▼
收款钱包（Base Sepolia USDC）：0x6D8F5690D773c68b566CA04F6a63F69740166a39
```

## 二、关键事实

| 项 | 值 |
|---|---|
| 线上 URL | https://x402-report.quanyida.workers.dev |
| Cloudflare 子域 | quanyida.workers.dev（Account ID 126d4d59...，邮箱 2331725105@qq.com） |
| Worker 名 / Version | x402-report / 4c6e528a-ed7d-4c5f-80e5-3e8a39f0b66d |
| 协议 | x402 **v1**（x402-hono 1.2.0，Cloudflare 官方文档当前仍推荐） |
| 路由保护 | `/report/*` $0.01 exact，Base Sepolia USDC |
| facilitator | https://x402.org/facilitator（免费测试网，无需 API key） |
| 收款钱包 payTo | 0x6D8F5690D773c68b566CA04F6a63F69740166a39 |
| 买家钱包 | x402-client-wallet-1 = 0x71ff4146e69993B66ba2a82DaE17709B32830208（CDP 托管） |

## 三、实施过程（含坑）

1. **环境**：Node v22.23.2 / npm 10.9.8；wrangler 全局安装因网络代理失败 → 设置 npm proxy（http://127.0.0.1:10809）后装成功（wrangler 4.149.0）。
2. **Worker 骨架**：`F:\个人知识库\x402-report-server\worker\`（Hono + x402-hono；wrangler.jsonc 定义 NETWORK=base-sepolia、FACILITATOR_URL）。
   - 坑①：`npm view x402-hono` 真实版本是 **1.2.0**（最初写 ^0.1.0 不存在）。
   - 坑②：x402-hono v1 路由正则**不支持 `:param`**，只支持字面量 / `*` / `[...]` → 路由键必须用 `/report/*`，handler 里从 `c.req.path` 解析 symbol。
   - 坑③：wrangler 4.149 要求 @cloudflare/workers-types ^5.20261006.1，升级后 ERESOLVE 解决。
3. **部署**：wrangler login 超时一次后成功；OAuth 授权页坐标点击无效，**JS b.click() 有效**。首次 deploy 需注册 workers.dev 子域 → 新版 wrangler 已移除 `subdomain` 命令 → 用 REST API 直接注册：`PUT /accounts/<id>/workers/subdomain {"subdomain":"quanyida"}`。
4. **线上验证**：
   - 直连超时（WinError 10060）；走本地代理被 Cloudflare **error 1010** 拦截（数据中心 IP）→ **线上验证只能走浏览器**。
   - 浏览器：/health → `{"status":"ok"}` ✅；/report/NVDA → 标准付款页（Payment Required · $0.01 Base Sepolia USDC · Connect wallet）✅。
5. **支付闭环（最难点）**：
   - cdp CLI 2.0.109 的 `cdp util x402 build` **只支持 v2 协议**（network 需 eip155:*），对 v1（network:"base-sepolia"）报 "invalid payment requirements JSON" → 不能直接用。
   - 解法：读本地 `node_modules\x402`（v1.2.0）客户端源码，确认 v1 exact EVM 结构：
     - EIP-712：primaryType=`TransferWithAuthorization`；types 含 6 字段（from/to/value/validAfter/validBefore/nonce）；domain = {name:"USDC", version:"2", chainId:84532, verifyingContract:USDC 地址}；**CDP 签名 API 额外要求 types 含 EIP712Domain 定义**。
     - header = base64(JSON{ x402Version:1, scheme:"exact", network, payload:{signature, authorization:{from,to,value,validAfter,validBefore,nonce}} })。
   - 已沉淀脚本（worker/scripts/）：
     - `v1_pay_prepare.mjs`：由付款要求生成 EIP-712 typed data（nonce 随机、validAfter=now-600、validBefore=now+maxTimeoutSeconds）。
     - `v1_pay_sign.mjs`：**Node spawn 直接调 cdp-cli 签名**（避免 PowerShell 剥 JSON 引号），组装 base64 header。
   - cdp CLI 联网需：HTTP(S)_PROXY=127.0.0.1:10809 + `NODE_OPTIONS=--require undici-proxy-preload.cjs`。
6. **最终结果**：浏览器带 `X-PAYMENT: <header>` 请求 /report/NVDA → **HTTP 200** + `{"symbol":"NVDA","note":"x402 Worker 版简化报告...","generated_at":"2026-10-11T05:00:25.964Z"}`；收款钱包 USDC 余额从 $0.06 → **$0.07**（+$0.01 到账 ✅）。

## 四、经验沉淀

- **v1 vs v2**：x402-hono（Cloudflare 文档推荐）是 v1；`@x402/hono` + `@x402/core` 是 v2。cdp CLI 的 x402 util 只吃 v2。v1 付款需自建 EIP-712 签名流程（本实验脚本可复用）。
- **Cloudflare 1010**：workers.dev 对代理/数据中心 IP 直接 403（error 1010），正常用户浏览器可访问。自动化验证需走浏览器（browser-use / CDP）。
- **浏览器 fetch 402**：fetch（Accept: */*）返回 402 JSON accepts；浏览器直接访问（Accept: text/html + Mozilla UA）返回 paywall HTML。
- **PowerShell 引号**：传 JSON 参数给 CLI 必被剥引号 → 用 Node spawn 数组传参。
- **token 过期**：wrangler OAuth token exp 2026-10-11T05:48Z 已接近过期，后续重登走 `wrangler login`（授权页 URL 每次生成、5 分钟内完成、坐标点击无效需 JS b.click()）。

## 五、后续方向

- 换自定义域名（Cloudflare 域名 + 备案/Worker 路由），摆脱 worker.dev 被墙/1010 限制。
- 升级 v2 协议（@x402/hono）以兼容 cdp CLI 标准工具链。
- 把该 Worker 加入 x402 注册中心（bazaar），让其他 Agent 可发现并付费调用。
- 将支付流程封装为 skill（v1 付款头自动构造 → X-PAYMENT → 结果返回）。

---

## 附：付款要求 JSON（线上 /report/NVDA 返回）

```json
{
  "scheme": "exact",
  "network": "base-sepolia",
  "maxAmountRequired": "10000",
  "resource": "https://x402-report.quanyida.workers.dev/report/NVDA",
  "description": "行情快照与观点摘要报告：输入美股/加密代码，返回价格、涨跌及简要市场观点（$0.01/次）",
  "mimeType": "application/json",
  "payTo": "0x6D8F5690D773c68b566CA04F6a63F69740166a39",
  "maxTimeoutSeconds": 300,
  "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  "extra": { "name": "USDC", "version": "2" }
}
```
