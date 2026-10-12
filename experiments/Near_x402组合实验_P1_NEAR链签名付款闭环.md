# Near×x402 组合实验 P1：NEAR 链签名付款闭环

> 日期：2026-10-12
> 仓库：quanyida/x402-agent（client/near-x402/）
> 状态：✅ 真实主网账户跑通

## 1. 目标

把 NEAR 账户变成 x402（HTTP 付费协议）的付款方：

```
NEAR 账户（MPC 链签名）
    │  1Click 自动注资（NEAR 资产 → 派生 Base 地址 USDC）
    ▼
派生 EVM 地址 0x3e9e790e4b54e1e885da833a45041021fc027394
    │  EIP-712 exact 付款（$0.001 USDC）
    ▼
Seller Agent（Base 主网，收 USDC）
```

Buyer 的资产在 NEAR 上，Seller 在 Base 收 USDC —— 中间由 **NEAR MPC 签名 + 1Click 意图兑换** 桥接，全程无本地私钥、无 CDP 托管。

## 2. 架构与关键组件

| 组件 | 实现 |
|---|---|
| NEAR 账户 | `a86069c1b67dd70dd619563fd802d5daff14c4bbbae09d756824c89750fd0077`（真实主网） |
| 签名 | py-near（官方 SDK）→ MPC 合约 `v1.signer` 门限签名（epsilon 派生 `ethereum-1`） |
| 派生地址 | `0x3e9e790e4b54e1e885da833a45041021fc027394`（Keccak-256(x‖y)[-20:]） |
| 兑换注资 | 1Click（`1click.chaindefuser.com`，NEAR Intents 官方 REST） |
| 付款 | x402 v2 `exact` scheme，EIP-712 `transferWithAuthorization` |

## 3. 踩坑记录（均已修复）

### 3.1 EIP-712 digest 根因（核心突破）
- 症状：服务端 402 header 明文 `invalid_exact_evm_payload_signature`，本地 recover 却匹配派生地址。
- 根因：x402 SDK 的 `signable.body` 是 **struct_hash**，不是最终 digest；最终 digest =
  `keccak(b"\x19\x01" + signable.header + signable.body)`。
- 修复：`near_signer.py` `sign_typed_data` 按 x402 服务端验签路径（eip712.py `hash_typed_data`，DOMAIN_TYPES 合并、nonce 为 bytes32）逐字节一致。修复后全链路 **HTTP 200**。

### 3.2 1Click quote 参数坑
- `/v0/quote` 400 缺字段 → 补 `depositType`/`refundType`/`recipientType`（ORIGIN_CHAIN/DESTINATION_CHAIN）+ `deadline`（ISO8601）。
- "No liquidity" → amount 必须是 **origin 资产原子单位**（wNEAR 1e24、USDC 1e6），按 wNEAR 市价 ≈$5.82 换算。
- 链上失败 storage 未注册 → wrap 时 `near_deposit` + 恒 `storage_deposit(account_id=deposit_addr, 0.05 NEAR)` + `ft_transfer` 多 action 注册守卫。
- `/v0/status` 需**同时传 correlationId+depositAddress** 双参数，否则一直 TIMEOUT。

### 3.3 余额探测 bug（主网误注资）
- 症状：每次付款都显示 balance $0.0000 → 重复触发 1Click 注资（单次多花 ~40s）。
- 根因：主网余额探测走 `base.blockscout.com`，大陆不可达 → 恒返回 0。
- 修复：主网 EVM 链改走 **eth_call balanceOf**（`mainnet.base.org` 等 RPC + 各链 USDC 合约地址），testnet 保留 blockscout 兜底。

## 4. 实测结果

### 4.1 付款闭环（#36 ping，$0.001）
- 首次全链路跑通：**HTTP 200** `{"pong":true}`，付给 `0xf567Cfe0FDb87A1D6Ca330e3f30f0D3DA0F7cb98`
- NEAR tx：`XpReu7uN2aPbEXgmNYwvgJiyw4sCicTmP8ubg1RoXwp`

### 4.2 币价服务（#7 ApiToll Crypto Prices，$0.001）
| 指标 | 修复前 | 修复后 |
|---|---|---|
| 总耗时 | 71.5s | **31.2s** |
| 余额探测 | $0.0000（误判）→ 重复注资 | $0.1925（eth_call 正确）→ 直接付款 |
| 结果 | HTTP 200 | HTTP 200（BTC $84,031 / ETH $2,560.76 / SOL $112.81，DefiLlama） |

### 4.3 注资 tx（真实主网）
- `AR3zd71VTA1CvsvJRqc6jrE11jzmv48QTiDzgP3bkchK`
- `XpReu7uN2aPbEXgmNYwvgJiyw4sCicTmP8ubg1RoXwp`
- `26zs6mVE8P8NLB66CdUyLTUuJdpxGJJb9ySSRRGxcrNf`

## 5. 账户资产（2026-10-12 查询）

| 资产 | 位置 | 金额 |
|---|---|---|
| NEAR（原生） | NEAR 链 | 0.3250 NEAR（≈$1.90） |
| USDC | NEAR 链 | 0.035036 |
| USDC | Base（派生地址） | 0.191506 |

## 6. 结论与边界

- ✅ **已闭环**：NEAR 账户签名 → 派生 EVM 地址 → x402 付款（真实主网，HTTP 200）
- ⏱ 单次 ~31s ≈ NEAR MPC 门限签名等待（15-20s 硬成本）+ Base 结算确认（5-10s）+ 网络
- ❌ 未做：非 NEAR 链余额自动归集（1Click 支持任意 origin，卡在签名层：需对应链私钥或 MPC 多路径派生）
- 🔒 凭证仅存本地 `.env`（仓库 .gitignore 已排除），私钥不落盘
