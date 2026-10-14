# smartmoney — 链上聪明钱信号引擎

「聪明钱追踪每日简报」的数据采集与信号引擎。多链全资产余额快照对比 → 净流入/流出信号。

## 当前状态（2026-10-14 M2）

- ✅ **M1 Base 采集器 + 信号引擎**：监控地址多资产余额在 [24h前, now] 两时点对比 → 净变动（USD 折算）
- ✅ **M2 多链全资产扩展**：
  - **Base**（`base.drpc.org`）：ETH/USDC/USDT/DAI/wETH/cbBTC 全资产，7 地址 71.8s 跑通；
    用户地址实测检测到真实变动：24h **-4.83 wETH ≈ -$12,106**
  - **ETH 主网**（`eth.drpc.org`，publicnode 403 不可用）：ETH/USDC/USDT/DAI/wETH/WBTC，10.7s 跑通
  - **Solana**（官方 `api.mainnet-beta.solana.com` 走代理）：SOL + **枚举全部 SPL 代币**，
    4.5s 跑通；Binance 冷钱包实测 1057 万 SOL（≈$1.13B）被正确识别
- ✅ 免 key 公共 RPC + CoinGecko 价格（代理回退），零 API 费用
- ⏳ S1 全网大额转账：暂时跳过（需 BaseScan V2 免费 API key，后续接入）
- ⏳ 真实监控对象待补充：当前内置地址多为 CEX 公开热钱包，信号价值有限

## 数据源探索结论（实测）

| 数据源 | 状态 | 说明 |
|---|---|---|
| 公共 RPC `base.drpc.org` | ✅ 可用 | eth_call / eth_getLogs(≤100块) 均可用 |
| 公共 RPC `base.publicnode.com` | ✅ 可用 | 同上 |
| ETH `eth.drpc.org` | ✅ 可用 | 历史区块 eth_call 可查 |
| ETH `ethereum-rpc.publicnode.com` | ❌ 403 | 直连/代理均 403 |
| Solana `api.mainnet-beta.solana.com` | ✅ 走代理可用 | 直连超时，需本地代理 10809 |
| Solana `solana-rpc.publicnode.com` | ❌ 403 | — |
| Solana `solana.drpc.org` / `1rpc.io/solana` | ❌ 400 | — |
| `1rpc.io/base` | ⚠️ 异常 | getLogs 大窗口静默返回空 |
| CDP Data API MCP | ❌ 不可用 | 工具持续 fetch failed |
| Blockscout 主网 | ❌ 不可用 | 直连超时 / 代理 403 |
| Etherscan/BaseScan V2 | ⏳ 待注册 | 免费 key，S1 用 |
| CoinGecko 价格 | ✅ 可用 | 直连超时→代理回退；10 分钟缓存 |

**为什么不做全网扫描**：Base 上 USDC 转账约 100 条/块，24h 全量 ≈ 450 万条日志；
公共 RPC 分批扫描会触发 429 限流（实测 432 批 40% 失败），工程上不可行。
信号引擎因此采用「监控地址余额快照对比」——快、免费、稳定。

**为什么 Solana 用「基线对比」而非「历史时点」**：Solana 公共 RPC 非归档节点，
无法查 24h 前余额；采用首次采集建基线（`data/sol_baseline.json`）+ 后续对比。

## 使用

```bash
python collector/base_collector.py     # Base 24h 信号 → data/latest_signals_base.json
python collector/eth_collector.py      # ETH 24h 信号  → data/latest_signals_eth.json
python collector/sol_collector.py      # Solana 快照    → data/latest_signals_solana.json
```

## 维护监控地址库

编辑 `data/watched_wallets.json`（优先级高于代码内置默认）：

```json
{
  "base": [
    {"name": "某鲸鱼", "address": "0x...", "tag": "whale"}
  ],
  "eth": [],
  "solana": []
}
```

> ⚠️ 内置的公开 CEX 标签地址多为 ETH 主网热钱包，在 Base 上余额很小、信号价值有限。
> **建议替换为你真正关注的地址**：自己的 CEX 充值地址、关注的聪明钱地址、机构金库等。

## 目录结构

```
smartmoney/
├── config.py                  # 链配置 / 资产表 / 阈值 / 地址库
├── collector/
│   ├── __init__.py
│   ├── rpc.py                 # 通用 JSON-RPC 客户端（直连+代理回退 / force_proxy）
│   ├── prices.py              # CoinGecko 价格（缓存+代理回退）
│   ├── evm_balance.py         # 通用 EVM 多资产引擎（原生+ERC20，USD 折算）
│   ├── base_collector.py      # Base 入口
│   ├── eth_collector.py       # ETH 入口
│   └── sol_collector.py       # Solana 入口（枚举全部 SPL）
└── data/
    ├── watched_wallets.json   # 监控地址库（可编辑）
    ├── latest_signals_{base,eth,solana}.json
    ├── sol_baseline.json      # Solana 基线（自动维护）
    └── prices_cache.json      # 价格缓存（自动维护）
```

## 路线图

- M3: AI 分析 + 简报生成（规则化 → LLM 升级）
- M4: x402 付费端点（完整版 $0.05 解锁）
- M5: X 免费摘要引流
