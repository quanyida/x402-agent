"""x402 收费报告服务 - MCP server 实现（方案 A）

供 OpenClaw / Codex 等 Agent 通过 MCP 协议调用：
  - generate_report (付费, $0.01 USDC)  → 复用 app/services/report_service.build_report
  - ping (免费)                          → 健康检查

传输: SSE (http://localhost:4022)
网络: eip155:84532 (Base Sepolia, development 环境)

运行:
  python -m app.mcp_server
"""

import asyncio
import os

from cdp import CdpClient
from cdp.x402 import create_facilitator_config
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from x402.http import HTTPFacilitatorClientSync
from x402.mcp import create_payment_wrapper
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.schemas import ResourceConfig
from x402.server import x402ResourceServerSync

from app.services.report_service import build_report

load_dotenv()

PORT = int(os.getenv("MCP_PORT", "4022"))
NETWORK = "eip155:84532"  # Base Sepolia
PRICE = "$0.01"           # 每次调用 $0.01


async def resolve_pay_to() -> str:
    """返回 PAY_TO（.env 优先），否则用 CDP 托管钱包地址。"""
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


def main() -> None:
    pay_to = asyncio.run(resolve_pay_to())

    # 资源服务器 + 支付方案（sync 版，官方 MCP 示例用法）
    resource_server = x402ResourceServerSync(
        HTTPFacilitatorClientSync(create_facilitator_config())
    )
    resource_server.register(NETWORK, ExactEvmServerScheme())
    resource_server.initialize()

    # 构建支付需求（等价于 HTTP 版的 PaymentOption）
    accepts = resource_server.build_payment_requirements(
        ResourceConfig(
            scheme="exact",
            network=NETWORK,
            pay_to=pay_to,
            price=PRICE,
            extra={"name": "USDC", "version": "2"},  # EIP-712 domain 参数
        )
    )

    # 付费工具包装器：verify + settle
    paid = create_payment_wrapper(resource_server, accepts=accepts)

    mcp_server = FastMCP("x402 CDP Report Service", host="0.0.0.0", port=PORT)

    @mcp_server.tool(
        name="generate_report",
        description=(
            "Generate a market snapshot and structured opinion summary for a stock or "
            "crypto symbol. Requires payment of $0.01 USDC on Base Sepolia."
        ),
    )
    @paid
    async def generate_report_tool(symbol: str) -> dict:
        return await build_report(symbol)

    @mcp_server.tool(name="ping", description="A free health check tool")
    async def ping() -> str:
        return "pong"

    print(f"x402 CDP MCP server running on http://localhost:{PORT}")
    print(f"  Receiving payments at {pay_to}")
    print("  Tools: generate_report (paid $0.01), ping (free)")
    mcp_server.run(transport="sse")


if __name__ == "__main__":
    main()
