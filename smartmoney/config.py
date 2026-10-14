"""smartmoney 数据源配置

数据源探索结论（2026-10-14 实测）：
- 公共 RPC 直连可用（免 key）：base.drpc.org / base.publicnode.com / ethereum-rpc.publicnode.com / solana-rpc.publicnode.com
- eth_getLogs 限制：drpc 100块范围、且全量扫描会 429 限流 → 不适合全网扫描
- eth_call 历史区块查余额：可行（~1.7s/地址），免 key → 信号引擎基于余额快照对比
- Solana getTokenAccountsByOwner：可枚举地址全部 SPL 代币（免 key 全资产）
- Etherscan/BaseScan V2：需免费 API key（chainid=8453 覆盖 Base）→ 后续 S1 全网转账明细用
- CDP Data API MCP：工具故障不可用
- Blockscout 主网：直连超时/代理 403，不可用

信号设计：
- 多链全资产监控：EVM 链 = 原生币 + 显式主流代币列表；Solana = 枚举全部 SPL
- 余额快照对比：[24h前区块, 最新] 两时点差值 → 净流入/流出信号（按 CoinGecko 价格折算 USD）
- S1 全网大额转账：后续用 BaseScan V2 API（需 key）
"""

# ---------- 链配置 ----------
# assets: 显式监控的代币列表（EVM 链）。native 表示原生币，用 eth_getBalance。
#   symbol, contract(原生币为 None), decimals, cg_id(CoinGecko id)
#   cg_id: ethereum/usd-coin/tether/dai/wrapped-ether/bitcoin/solana
CHAINS = {
    "base": {
        "chain_id": 8453,
        "rpc": "https://base.drpc.org",   # 备用: https://base.publicnode.com
        "block_time": 2.0,                 # 秒/块
        "explorer": "https://basescan.org",
        "assets": [
            {"symbol": "ETH",  "contract": None,                                       "decimals": 18, "cg_id": "ethereum"},
            {"symbol": "USDC", "contract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6,  "cg_id": "usd-coin"},
            {"symbol": "USDT", "contract": "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2", "decimals": 6,  "cg_id": "tether"},
            {"symbol": "DAI",  "contract": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", "decimals": 18, "cg_id": "dai"},
            {"symbol": "wETH", "contract": "0x4200000000000000000000000000000000000006", "decimals": 18, "cg_id": "ethereum"},
            {"symbol": "cbBTC", "contract": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", "decimals": 8,  "cg_id": "bitcoin"},
        ],
    },
    "eth": {
        "chain_id": 1,
        "rpc": "https://eth.drpc.org",   # 备用: ethereum-rpc.publicnode.com(403,不可用)
        "block_time": 12.0,
        "explorer": "https://etherscan.io",
        "assets": [
            {"symbol": "ETH",  "contract": None,                                       "decimals": 18, "cg_id": "ethereum"},
            {"symbol": "USDC", "contract": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6,  "cg_id": "usd-coin"},
            {"symbol": "USDT", "contract": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6,  "cg_id": "tether"},
            {"symbol": "DAI",  "contract": "0x6B175474E89094C44Da98b954EedeAC495271d0F", "decimals": 18, "cg_id": "dai"},
            {"symbol": "wETH", "contract": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "decimals": 18, "cg_id": "ethereum"},
            {"symbol": "WBTC", "contract": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "decimals": 8,  "cg_id": "bitcoin"},
        ],
    },
    "solana": {
        "chain_id": None,
        "rpc": "https://api.mainnet-beta.solana.com",  # 官方 RPC，直连受限需代理；备用 solana-rpc.publicnode.com(403)
        "force_proxy": True,  # 直连被墙/限流，强制走本地代理 10809
        "block_time": 0.4,
        "explorer": "https://solscan.io",
        "assets": [
            {"symbol": "SOL", "contract": None, "decimals": 9, "cg_id": "solana"},
            # SPL 代币用 getTokenAccountsByOwner 枚举；这里列出已知 mint 供展示/过滤
            {"symbol": "USDC", "contract": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "decimals": 6, "cg_id": "usd-coin"},
        ],
    },
}

# ---------- 信号阈值 ----------
# 净变动信号阈值（USD），任一资产净变动折算后超过阈值即触发
SIGNAL_THRESHOLD_USD = {
    "base": 100_000,
    "eth": 100_000,
    "solana": 100_000,
}

# 每日简报回溯窗口（小时）
LOOKBACK_HOURS = 24

# ---------- 监控地址库（data/watched_wallets.json 可覆盖）----------
# 优先级：data/watched_wallets.json 存在则用之；否则用 DEFAULT_WATCHED_WALLETS。
# 内置地址均为 2026-10-14 实测可查询的真实地址。
# 注意：公开标签地址多为 ETH 主网热钱包，在各链余额普遍很小，信号价值有限；
# 建议用户自行替换为真正关注的地址（自己的 CEX 充值地址 / 聪明钱地址），格式见 JSON 文件。
DEFAULT_WATCHED_WALLETS = {
    "base": [
        {"name": "Coinbase-1", "address": "0x71660c4005BA85c37ccec55d0C4493E66Cd775d3", "tag": "CEX"},
        {"name": "Coinbase-2", "address": "0x503828976D22510aad0201ac7EC88293211D23Da", "tag": "CEX"},
        {"name": "Coinbase-3", "address": "0xddfAbCdc4D8FfC6D5beaf154f18B778f892A0740", "tag": "CEX"},
        {"name": "Binance-14", "address": "0x56Eddb7aa87536c09CCc2793473599fD21A8b17F", "tag": "CEX"},
        {"name": "Binance-15", "address": "0xDFd5293D8e347dFe492E7ae80DED900e47DB0BC5", "tag": "CEX"},
        {"name": "Binance-16", "address": "0x28C6c06298d514Db089934071355E5743bf21d60", "tag": "CEX"},
        {"name": "用户提供", "address": "0xca0d8f914eda586fbc1b835e8e214eb3f6dbd8f3", "tag": "user"},
    ],
    "eth": [
        {"name": "用户提供", "address": "0xca0d8f914eda586fbc1b835e8e214eb3f6dbd8f3", "tag": "user"},
    ],
    "solana": [],
}

# ---------- ERC20 Transfer 事件 ----------
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# balanceOf(address) selector
BALANCEOF_SELECTOR = "0x70a08231"

# Solana SPL Token 程序 ID
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# HTTP 请求头
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
