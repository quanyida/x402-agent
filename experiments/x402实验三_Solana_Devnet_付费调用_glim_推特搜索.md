# x402 实验三：Solana Devnet 付费调用 glim.sh 推特搜索（失败根因定位）

日期：2026-10-11
状态：**卡点已定位，属服务方配置问题，客户端无解**

## 目标

用 `x402-bazaar-client` skill 付费调用注册表里的 Solana Devnet 服务 glim.sh
（X/Twitter 搜索），查询近期 AI 新闻。

## 尝试过程

### 1. 首次调用（EVM 分支）
- `call 0 --input-file _glim_input.json`（body `{"query":"AI news lang:en min_faves:50"}`）
- 返回 `HTTP 402 {"error":"payment_failed","reason":"invalid_payload"}`
- 根因：脚本只注册了 `ExactEvmScheme`（EVM 签名），而 glim 首选网络是 Solana，
  生成的是 EVM 格式 payload → 服务端无法解析。

### 2. 补齐 SVM 支持（已改代码）
- `x402_bazaar.py` 增加：
  - `--input-file` / `--svm-keyfile` 参数（PowerShell 引号坑）
  - `_pay_and_call` 的 SVM 分支：`net.startswith("solana:")` 时用本地
    `KeypairSigner` + `ExactSvmScheme(signer)` 注册，不再走 CDP EVM 账户
  - 动态注册 `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp`（Solana Devnet 真实 CAIP-2）
- 关键接口事实：
  - `ExactSvmScheme(signer: ClientSvmSigner, rpc_url=None)`
  - `ClientSvmSigner` Protocol 要求本地 `solders.Keypair`（`.address`/`.keypair`/`.sign_transaction`）
  - CDP `SolanaAccount` 是远程托管签名（`sign_transaction(str)` 走 API），**不满足**协议
  - `FacilitatorKeypairSigner` 也是服务端本地 keypair 场景，客户端不可用

### 3. 建立本地 Solana 付款钱包（setup_svm.py，已跑通）
- 生成本地 keypair：`6jHiREPLfwMQqF76eTLcTER6eHr1Ry7jP72nmCY1qCLd`
  （私钥存 `.svm_keypair.json`，仅 devnet 测试币）
- CDP 托管 Solana 账户：`GLNGxXKwCjoZ2ivwie84QrxgWnJmSqBgJVnQ75Rqh8kt`
  - `request_faucet("sol")`、`request_faucet("usdc")` 均成功
  - `transfer(to=本地keypair, 1.5 USDC, "usdc", "devnet")` 成功
    tx=`4zW76CdyUeN9xinxa1AtBR7fEeMMmuL285AjnkUr6XkMq4fk7oBFf2PS9S8k19qfMt9fVMSpKqKVzLH25YmZdqsM`
- RPC 验证：本地钱包 devnet USDC = 1.5（mint `4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`，
  token account `BTuKx13PpJAHuwKMdb3a3hHtPACh1znix1y3nTSwJfpa`）

### 4. 二次调用（SVM 分支）
- 返回 `HTTP 402 {"error":"payment_failed","reason":"invalid_exact_svm_payload_transaction_simulation_failed"}`
  suggestion: "The paying Solana wallet has no USDC token account"

## 根因（已实证）

**glim.sh 服务方配置错误：`network` 标成 Solana Devnet，但 `asset` 用了 Solana 主网 USDC mint。**

```
accepts[0] = {
  "network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",  # Solana Devnet genesis hash
  "asset":   "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # Solana 主网 USDC mint
}
```

证据链：
1. x402 SVM client 源码 `exact/client.py:110`：`mint = Pubkey.from_string(requirements.asset)`
   —— 直接用 accepts 里的 asset 构造转账指令，**不做 devnet 映射**；
2. devnet 上不存在主网 USDC mint（devnet USDC 是 `4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`），
   因此 payer 的 ATA 派生不出来 → 模拟失败；
3. 同注册表中的另一个 Solana 服务 **SolEnrich** 配置完全相同
   （`solana:5eykt4...` + `EPjFWdd5...`），是服务方的统一（错误）惯例；
4. SolEnrich 有 3549 次调用但 uniquePayers 仅 6 —— 极可能是服务方自测/内部测试数据，
   不是外部真实付费用户。

## 结论与替代方案

- **Solana Devnet 通道当前不可用**（两个 Solana 测试网服务都是这个配置），客户端无法修复；
- 可选替代：
  1. glim.sh 的 **Base 主网**通道（accepts[1]，eip155:8453，payTo
     `0xC751344Ee09B5159160173F77D4a1169bCd386A1`）——需要给付款钱包
     `0x71ff4146e69993B66ba2a82DaE17709B32830208` 充 Base 主网真实 USDC（用户未同意）；
  2. 换已验证可用的 Base Sepolia 服务：Wallets Demo Weather（$0.001）、Omni Terminal（$0.01）；
  3. 向 glim.sh 反馈：应把 Solana 通道的 asset 改为 devnet USDC mint，或把 network 改为主网。

## 本轮新增/修改文件

- 修改 `C:\Users\23317\.agents\skills\x402-bazaar-client\scripts\x402_bazaar.py`
  （SVM 分支、--input-file、--svm-keyfile、动态网络注册）
- 新增 `F:\个人知识库\x402-report-server\setup_svm.py`（CDP 领水→转本地 keypair）
- 新增 `F:\个人知识库\x402-report-server\check_svm_balance.py` / `query_devnet.py`（余额/状态查询）
- 新增 `F:\个人知识库\x402-report-server\.svm_keypair.json`（**敏感**：devnet 私钥，仅测试币）
- 清理 38 个调试临时文件

## 备注

- CDP `request_faucet` 只能给 CDP 托管账户自身领水，不能指定任意地址；
- Solana 测试网 CAIP-2 是 `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp`
  （不是 `solana:901`），脚本已动态注册处理；
- 付款走 `feePayer` 字段（服务方付 gas），付款方只需持有 USDC，无需 SOL；
  但本地 keypair 也领了 SOL（备用）。
