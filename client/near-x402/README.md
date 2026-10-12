# near-x402 — NEAR 账户作为 x402 付款方

把 NEAR 账户变成 x402（HTTP 付费协议）的买家：**NEAR 链签名（MPC）+ 1Click 自动注资 → EVM 链 USDC 付款**。

> **代码位置**：完整脚本在 `skills/x402-bazaar-client/scripts/`（本 skill 的唯一代码源，与本地生效的 skill 同步）：
> - `near_signer.py` — NEAR 签名器（官方 py-near SDK）：MPC 链签名、EIP-712 typed-data 签名（digest 修复版）、1Click 自动注资、派生 EVM 地址
> - `x402_bazaar.py` — x402 客户端：registry 发现 + 402 自动付款；新增 `--signer near` 模式（CDP 默认路径保留）

## 依赖

```
pip install py-near x402[evm,svm,httpx] cdp-sdk httpx python-dotenv
```

## 凭证（.env，已被 .gitignore 排除，绝不上传）

```
NEAR_ACCOUNT_ID=<near account id>
NEAR_PRIVATE_KEY=ed25519:...
CDP_API_KEY_ID=...
CDP_API_KEY_SECRET=...
CDP_WALLET_SECRET=...
```

## 用法

```bash
cd skills/x402-bazaar-client/scripts

# 列主网服务
python x402_bazaar.py list --mainnet --limit 40

# 用 NEAR 账户付款调用（主网，需 --mainnet 显式授权）
python x402_bazaar.py call <index> --mainnet --network eip155:8453 \
  --signer near --near-account <id> --near-key-file <keyfile> \
  --near-rpc https://free.rpc.fastnear.com --yes
```

## 能力边界（当前实现）

- ✅ NEAR 侧资产（USDC@NEAR / wNEAR / native NEAR）→ 自动兑换成目标 EVM 链 USDC 注资到派生地址 → 付款
- ✅ 目标链可配置（Base / Arbitrum / Polygon / Ethereum / Optimism）
- ❌ 非 NEAR 链上的余额（如 Arbitrum USDC、Solana USDC）尚未自动归集
- ⏱ 每次付款含 NEAR MPC 链签名等待（约 15-20s 硬成本）+ 目标链结算确认，实测单次约 30s

## 备注

- 1Click 是 NEAR Intents 官方 REST 渠道（官方示例即直调 HTTP），NEAR 链上交互 100% 走官方 py-near SDK
- 私钥不落盘到仓库；实验过程见 `experiments/Near_x402组合实验_P1_NEAR链签名付款闭环.md`
