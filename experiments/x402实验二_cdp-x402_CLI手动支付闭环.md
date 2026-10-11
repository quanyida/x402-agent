# x402 实验（二）：cdp-x402 skill 手动 Buyer 流程调用收费 API

> 归档日期：2026-10-10
> 关联：`x402实验_AI_Agent自动付费闭环.md`（SDK 自动支付路线，HTTP 200 + $0.02 已到账）
> 本次：用 **cdp CLI（cdp-x402 skill）** 以"手动构建 → 签名 → 编码 → curl"方式打通同一条收费链路，验证 **不带 SDK 也能完成 x402 支付**（Agent 可纯靠 CLI 指令闭环）。

---

## 1. 实验目标

- 目标服务：`http://127.0.0.1:8402/report/AAOI`（FastAPI + x402 中间件，$0.01/次，Base Sepolia）
- 支付方：CDP 托管买家钱包 `0x71ff4146e69993B66ba2a82DaE17709B32830208`（x402-client-wallet-1）
- 收款方：`0x6D8F5690D773c68b566CA04F6a63F69740166a39`（x402-report-receiver）
- 与第一份归档的区别：**第一份用 Python SDK 自动处理 402；本次用 cdp CLI 命令逐步骤手工构造支付**，验证协议层的可操作性。

---

## 2. 完整执行流程（5 步，cdp-x402 skill EVM buyer flow）

### 第 0 步：启动服务 + 获取支付要求
```powershell
# 启动 HTTP server（后台，PID 34344，端口 8402）
$env:HTTP_PROXY = "http://127.0.0.1:10809"
$env:HTTPS_PROXY = "http://127.0.0.1:10809"
python run.py

# 无支付访问 → 402 + PAYMENT-REQUIRED 头（V2）
curl -i http://127.0.0.1:8402/report/AAOI
```
`PAYMENT-REQUIRED` 头解码后是**完整对象**（含 `resource` + `accepts` 数组）。

### 第 1 步：解析买家账户地址
```powershell
cdp evm accounts by-name x402-client-wallet-1   # 位置参数，不是 name=
# → {"address":"0x71ff4146e69993B66ba2a82DaE17709B32830208", ...}
```

### 第 2 步：构建 EIP-712 支付载荷
```powershell
cdp util x402 build --from <addr>
```
**关键坑**：build 期望的 payment requirements 是 **`accepts` 数组**（如 `[{"scheme":"exact",...}]`），**不是**完整 PAYMENT-REQUIRED 对象。从解码对象中提取 `accepts` 传入。

### 第 3 步：CDP 签名（托管钱包云端签名）
```powershell
cdp evm accounts sign typed-data <addr> "domain:=<json>" "types:=<json>" "primaryType=TransferWithAuthorization" "message:=<json>"
```
载荷结构：`EIP712Domain` + `TransferWithAuthorization`（from/to/value/validAfter/validBefore/nonce），链 Base Sepolia（chainId 84532）。

### 第 4 步：编码支付头（V2）
```powershell
cdp util x402 encode --payment-requirements '<accepts>' --signature <sig> --authorization '<message>' --x402-version 2
# → 912 字符 base64 支付头
```

### 第 5 步：curl 带支付头调用
```powershell
curl -H "X-PAYMENT: <header>" -H "PAYMENT-SIGNATURE: <header>" http://127.0.0.1:8402/report/AAOI
# → HTTP 200 + 报告 JSON
```

---

## 3. 闭环结果

```
HTTP/1.1 200 OK
Payment-Response: {"success": true, "payer": "0x71ff...08",
  "transaction": "0x843b8e08cd53cf32cec2b8046343dbea658acba89411034794ca3eed3125f942",
  "network": "eip155:84532"}
{"symbol":"AAOI","quote":{"price":109.64,"change_pct_1d":3.53,"market_cap":9309125474.137314,...},"view":"...","disclaimer":"..."}
```

| 指标 | 值 |
|---|---|
| HTTP 状态 | **200 OK**（支付验证通过） |
| 链上交易 | `0x843b8e08cd53cf32cec2b8046343dbea658acba89411034794ca3eed3125f942` |
| 金额 | 10000 atomic = $0.01 USDC（Base Sepolia） |
| 报告 | AAOI：$109.64，+3.53%，市值 $9.31B |

---

## 4. 过程中踩的坑（本次新增，勿重踩）

| # | 问题 | 根因 | 解法 |
|---|------|------|------|
| 1 | `cdp evm accounts by-name name=x` 报 missing argument | cdp-cli 2.x 是**位置参数**，不是 `name=` | `cdp evm accounts by-name x402-client-wallet-1` |
| 2 | `build` 报 invalid payment requirements JSON | **REQ 应为 `accepts` 数组**，非完整 PAYMENT-REQUIRED 对象 | 解码后提取 `.accepts` 传入（本实验存 `_pm_accepts.json`） |
| 3 | PowerShell 直接传 JSON 参数 → `domain` 报 invalid JSON（引号被剥） | PowerShell/cmd 调 cdp.ps1 二次转发时 JSON 内引号丢失 | **用 Node `child_process.spawn` 以参数数组调用 cdp-cli**（`_sign_x402.js` / `_encode_x402.js`），彻底规避 shell 引号转义 |
| 4 | `_pm_payload.json` 带 UTF-8 BOM → Node JSON.parse 报 Unexpected token | PowerShell `Set-Content -Encoding UTF8` 写 BOM | 用 `[System.IO.File]::WriteAllText(..., New-Object System.Text.UTF8Encoding $false)` 无 BOM 重写 |
| 5 | `cdp util x402 build` 输出混入 undici-proxy stderr | cdp CLI 走代理注入 `NODE_OPTIONS` 时打印提示 | 重定向时 `2>$null` 丢弃 stderr，只保留 stdout JSON |
| 6 | `--jq` 过滤未生效 | cdp-cli 该命令需确认 --jq 支持位置（本次用 PowerShell ConvertFrom-Json 替代） | 优先本地 JSON 解析 |

---

## 5. 产物与中间文件

- `F:\个人知识库\x402-report-server\`
  - `_pm_req.json` — 完整 PAYMENT-REQUIRED 解码对象
  - `_pm_accepts.json` — 提取出的 accepts 数组（build/encode 的输入）
  - `_pm_payload.json` — EIP-712 载荷（build 输出）
  - `_pm_signature.txt` — CDP 签名（sign 输出）
  - `_pm_header.txt` — 编码后的支付头（encode 输出）
  - `_sign_x402.js` / `_encode_x402.js` — **Node spawn 调用 cdp-cli 的脚本（规避 shell 引号问题的关键工具）**
  - `_http_out.log` / `_http_err.log` — HTTP server 运行日志

---

## 6. 结论与意义

1. **协议可操作性验证**：x402 支付不依赖特定 SDK，Agent 可通过 CLI 指令（build → sign → encode → curl）完整闭环——这正是 cdp-x402 skill 给 Agent 的"徒手支付"能力。
2. **两条路线对照**：
   - SDK 路线（`client.py`）：自动处理 402，适合集成进 Agent 代码
   - CLI 路线（本次）：逐步可审计、可脚本化，适合 Agent 按 skill 指令执行
3. **对 Codex 接入的意义**：Codex 侧若走"skill 指令"路线（收到 402 → 按 skill 构建支付 → 重试），本实验证明该模式**协议层可行**；剩余不确定点是 Codex 内置 MCP 客户端能否在重试时携带 `_meta["x402/payment"]`（需实测）。

---

*本文档为实验过程客观归档，地址为测试网地址，不构成投资建议。*
