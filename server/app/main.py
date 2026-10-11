"""x402 收费报告服务 - FastAPI 实现

路由: GET /report/{symbol}  → 行情快照 + 观点摘要报告（$0.01/次，exact scheme）
网络: eip155:84532 (Base Sepolia, development 环境)
"""

import asyncio
import os
from contextlib import asynccontextmanager

from cdp import CdpClient
from cdp.x402 import create_facilitator_config
from dotenv import load_dotenv
from fastapi import FastAPI
from x402.http import HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

load_dotenv()

NETWORK = "eip155:84532"  # Base Sepolia
PRICE = "$0.01"           # 每次调用 $0.01

# 模块级服务对象（不含 PAY_TO 解析，pay_to 在 lifespan 中补齐）
server = x402ResourceServer(HTTPFacilitatorClient(create_facilitator_config()))
server.register(NETWORK, ExactEvmServerScheme())


def _build_routes(pay_to: str) -> dict:
    """构造路由配置。pay_to 来自 .env 或 CDP 托管钱包。"""
    return {
        "GET /report/[symbol]": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=pay_to,
                    price=PRICE,
                    network=NETWORK,
                )
            ],
            mime_type="application/json",
            description="行情快照与观点摘要报告：输入美股/加密代码，返回价格、涨跌、市值及简要市场观点",
        ),
    }


async def _resolve_pay_to() -> str:
    """解析收款地址：优先 .env 的 PAY_TO，否则从 CDP 托管钱包获取。"""
    env_pay_to = os.getenv("PAY_TO", "").strip()
    if env_pay_to:
        return env_pay_to

    async with CdpClient(
        api_key_id=os.environ["CDP_API_KEY_ID"],
        api_key_secret=os.environ["CDP_API_KEY_SECRET"],
        wallet_secret=os.environ["CDP_WALLET_SECRET"],
    ) as cdp:
        wallet = await cdp.evm.get_or_create_account("x402-report-receiver")
        return wallet.address


# 模块级解析 pay_to（uvicorn 导入本模块时无 running loop，asyncio.run 安全）
_PAY_TO = asyncio.run(_resolve_pay_to())

# 模块级注册中间件（启动后再 add_middleware 会报 RuntimeError）
app = FastAPI()
app.add_middleware(
    PaymentMiddlewareASGI,
    routes=_build_routes(_PAY_TO),
    server=server,
)
print(f"x402 server ready — receiving payments at {_PAY_TO}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


@app.get("/report/{symbol}")
async def report(symbol: str):
    """收费路由：返回行情快照与观点摘要。"""
    from app.services.report_service import build_report

    return await build_report(symbol)
