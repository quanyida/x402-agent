# x402 实验：AI Agent 自动付费消费 API 的完整闭环

> 归档日期：2026-10-10
> 目标：搭建一个"收费 API + 自动支付客户端"，验证 **AI Agent 可以自主解析 402、自动支付 USDC、拿回资源** 的完整链路。这也是把「Agent 付费调用数据/服务」落地为可复用模板的第一次实战。
> 姊妹篇：`x402实验二_cdp-x402_CLI手动支付闭环.md`（用 cdp CLI 手动构建/签名/编码/curl 打通同一链路）

---

## 1. 实验背景与目标

- **为什么做这件事**：未来 AI Agent 需要自主为数据、算力、API 服务付费。x402（HTTP 402 Payment Required 支付协议）让 HTTP 层原生支持"未付费 → 402 → 自动支付 → 放行"，是 Agent 经济体最直接的支付范式之一。
- **实验链路**（两段式）：

```
┌─ 服务端（收费方）─────────────────────────────┐
│ FastAPI + x402 中间件 + CDP 托管收款钱包       │
│ GET /report/[symbol]  →  $0.01/次 (Base Sepolia USDC) │
└──────────────────────────────────────────────┘
            ↑ 402 + PAYMENT-REQUIRED 头
┌─ 买家端（付费方）─────────────────────────────┐
│ client.py：CDP 托管买家钱包 + x402HttpxClient  │
│ 自动解析 402 → EIP-712 签名 → 链上结算 → 重放请求 │
└──────────────────────────────────────────────┘
```

- **网络**：Base Sepolia Testnet（`eip155:84532`），测试网 USDC，无真实资金风险。

---

## 2. 服务端搭建（已完成）

### 2.1 技术栈
- Python 3.10+ / FastAPI / uvicorn（端口 8402）
- `cdp-sdk` + `x402[evm,svm,fastapi]`
- 报告服务：`yfinance` 实时行情快照（AAOI 示例：price 109.64 USD，+3.53%，市值 9.31B）

### 2.2 关键代码结构（`app/main.py`）
```python
# 收款地址：模块级解析（CDP 托管钱包）
PAY_TO = asyncio.run(resolve_pay_to())   # 0x6D8F5690D773c68b566CA04F6a63F69740166a39
NETWORK = "eip155:84532"

server = x402ResourceServer(HTTPFacilitatorClient(create_facilitator_config()))
server.register(NETWORK, ExactEvmServerScheme())

routes = {
    "GET /report/[symbol]": RouteConfig(
        accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO, price="$0.01", network=NETWORK)],
        mime_type="application/json",
        description="AI-generated report",
    ),
}
app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)  # 模块级注册
```

### 2.3 服务端踩坑记录（4 个）
| # | 问题 | 根因 | 解法 |
|---|------|------|------|
| 1 | `RuntimeError: Cannot add middleware after application has started` | FastAPI lifespan 内调 `add_middleware` 太晚 | 改为**模块级**注册中间件 |
| 2 | 路由 404 / 不匹配 | x402 路由键只支持 `*`、`[param]`、`:param`；`{param}` 会被 re.escape 成字面量 | 路由写成 `GET /report/[symbol]` |
| 3 | 直连 `api.cdp.coinbase.com:443` 超时 | 需要代理 `http://127.0.0.1:10809`；`.env` 代理行对库不生效（库在 import 时读进程环境变量） | 启动时在 PowerShell 层先设 `$env:HTTP_PROXY/HTTPS_PROXY` 再 `python run.py` |
| 4 | SDK 查余额 API 用错 | 正确方法是 `list_token_balances(network='base-sepolia')`（返回 namedtuple `balances=[EvmTokenBalance...]`） | 按返回值结构解析 |

### 2.4 402 保护验证
```bash
curl -i http://localhost:8402/report/AAOI
# → HTTP/1.1 402 Payment Required
# → PAYMENT-REQUIRED: eyJ4NDAyVmVyc2lvbiI6MiwiZXJyb3IiOiJQYXltZW50IHJlcXVpcmVkIiwi...
```

---

## 3. 买家端客户端（本次打通的核心）

### 3.1 技术栈与代码骨架（`client.py`）
```python
# ① 创建/获取 CDP 托管买家钱包
async with CdpClient() as cdp:
    account = await cdp.evm.get_or_create_account(name="x402-client-wallet-1")
    signer = EthAccountSigner(EvmLocalAccount(account))
    # 买家钱包: 0x71ff4146e69993B66ba2a82DaE17709B32830208

    # ② 注册支付方案（exact 固定价格）
    payment_client = x402Client()
    payment_client.register("eip155:84532", ExactEvmScheme(signer))

    # ③ 带支付能力的 HTTP 客户端
    async with x402HttpxClient(payment_client) as http:
        response = await http.get("http://127.0.0.1:8402/report/AAOI")
        await response.aread()
    # → HTTP 200 + 报告 JSON
```

### 3.2 买家端踩坑记录（本次打通的关键）
| # | 问题 | 根因 | 解法 |
|---|------|------|------|
| 1 | CDP SDK 余额查询显示 $0（链上其实有币） | **CDP SDK 索引延迟** | 改用 **Blockscout 链上 API** 实时查询：`https://base-sepolia.blockscout.com/api/v2/addresses/{addr}/token-balances` |
| 2 | SDK 余额为 0 → 误触发 faucet | 同上 | 余额判断统一走 Blockscout |
| 3 | CDP faucet 提交后 10+ 分钟未到账 | CDP 测试网 faucet 偶发失败/慢 | 放弃 faucet，改用 **MetaMask 手动转账** USDC 到买家钱包 |
| 4 | **支付流程签名调用超时**（`HTTPSConnectionPool(host='api.cdp.coinbase.com', port=443)` 直连超时） | cdp-sdk 的 `Urllib3AuthClient` **硬编码 `urllib3.PoolManager()`（不带代理、不读环境变量）** | **Monkey-patch `Urllib3AuthClient.__init__`**，把内部 client 替换为带代理的 `urllib3.ProxyManager(proxy_url=...)` |
| 5 | 脚本内 `os.environ.setdefault(HTTP_PROXY)` 对库不生效 | 库 import 时已创建连接池 | patch 在 import 前执行；另在 PowerShell 层设 `$env:HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` 双保险 |

### 3.3 代理 patch 代码（核心修复）
```python
import urllib3
from urllib3.poolmanager import ProxyManager
from cdp.auth.clients.urllib3.client import Urllib3AuthClient

_orig_init = Urllib3AuthClient.__init__

def _patched_init(self, options, base_url, debug=False):
    _orig_init(self, options, base_url, debug)
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy_url:
        self.client = ProxyManager(proxy_url=proxy_url)

Urllib3AuthClient.__init__ = _patched_init
```

---

## 4. 闭环验证结果

### 4.1 一次成功的自动支付（最终状态）
```
cdp Urllib3AuthClient proxy patch applied
Paying from 0x71ff4146e69993B66ba2a82DaE17709B32830208
USDC balance (on-chain): $4.989000
调用 http://127.0.0.1:8402/report/AAOI ...
HTTP 200
响应内容: {"symbol":"AAOI","quote":{"price":109.64,"change_pct_1d":3.53,
"market_cap":9309125474.137314,"currency":"USD","period":"5d"},
"view":"当前价格 109.64 USD；近 5 日收盘较前一日上涨 3.53%；市值约 9.31B；...",
"disclaimer":"此报告为行情快照与规则化观点摘要，非投资建议。"}
```

### 4.2 链上到账核对（Blockscout 双端确认）
| 钱包 | 地址 | 角色 | 验证结果 |
|------|------|------|----------|
| 收款钱包 | `0x6D8F5690D773c68b566CA04F6a63F69740166a39` | 服务端收款 | **USDC = 20,000 atomic = $0.02**（手动 $0.01 + 本次自动 $0.01）✓ |
| 买家钱包 | `0x71ff4146e69993B66ba2a82DaE17709B32830208` | 客户端付款 | 链上 $4.989（转账 $1 + 早期 $3.989 − 本次 $0.01）✓ |

### 4.3 完整闭环时序
```
client.py 启动
  → GET /report/AAOI（无支付头）
  → 服务端返回 402 + PAYMENT-REQUIRED 头
  → x402 解析支付要求（V2 头优先，V1 body 兜底）
  → 本地构造 EIP-712 支付消息
  → CDP 签名 API 签发支付授权（走代理）
  → 链上结算 $0.01 USDC（买家 → 收款钱包）
  → 重放请求 + 支付头
  → HTTP 200 → 返回 AAOI 报告 JSON
```

---

## 5. 目录与文件清单

- `F:\个人知识库\x402-report-server\`
  - `app\main.py` — 服务端：FastAPI + x402 中间件 + 报告服务（运行中，端口 8402）
  - `client.py` — **买家自动支付客户端（本次闭环的核心产物）**
  - `fund_buyer.py` / `poll_balance.py` / `check_balance.py` — 调试辅助（余额查询/领水）
  - `rpc_balance.py` / `rpc_balance2.py` / `rpc_body.json` — RPC 探测（公开 RPC 均 403，不可用）
  - `.env` — CDP 凭证 + 代理配置（敏感，勿外传）
  - `requirements.txt` — cdp-sdk / x402[evm,svm,fastapi] / uvicorn / fastapi / yfinance / python-dotenv

---

## 6. 已验证做不通的路径（勿重试）

| 方案 | 结果 |
|------|------|
| CDP SDK `list_token_balances` 判断余额 | 索引延迟，链上有币但查为 0 |
| CDP `request_faucet` 等 USDC | 提交 tx `0xe7b14f...` 后 >10min 未到账，不可靠 |
| 公开 RPC：`sepolia.base.org` / `publicnode` / `blockpi` | 全部 403（带代理也 403） |
| Etherscan V1 API | 已弃用，要求迁移 V2 |
| Etherscan V2 API | 需要 API key（未配置） |
| 脚本内/PowerShell 层设 `HTTP(S)_PROXY` 让 cdp-sdk 签名走代理 | 对 `Urllib3AuthClient` 无效（硬编码 PoolManager）→ 需 patch |

---

## 7. 意义与后续扩展方向

**这次打通意味着**：任意 AI Agent 只要集成 x402 客户端，就能对任何实现了 x402 的 API 服务**自主付费**（解析 402 → 签名 → 结算 → 拿资源），无需人工介入。这是 Agent 经济体、Agent 间服务交易的基础设施。

**后续可扩展**：
1. 换标的测试（`TARGET_URL` 环境变量覆盖，如 `/report/NVDA`）
2. `upto` 计量计价（按实际用量结算，适合 token 计费的 LLM API）
3. 接入 NEAR Agent / TAO 组合方案：让 NEAR 侧 Agent 调用本服务自动付费拿行情报告
4. 主网部署（需用户显式确认 + 真实 USDC，当前为 development/testnet）

---

*本文档为实验过程客观归档，涉及凭证信息均脱敏，地址为测试网地址，不构成投资建议。*
