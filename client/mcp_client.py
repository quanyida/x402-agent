"""x402 报告服务 - MCP 付费客户端（方案 A 闭环验证）

包装 MCP 客户端使其具备 x402 自动支付能力：
  - ping             → 免费，直通
  - generate_report  → 付费 $0.01 USDC，自动支付 + 重试 + 返回报告

用法:
  python mcp_client.py <symbol>
示例:
  python mcp_client.py AAOI
"""

import asyncio
import os
import sys

from cdp import CdpClient
from cdp.evm_local_account import EvmLocalAccount
from dotenv import load_dotenv
from x402 import x402Client
from x402.mcp import create_x402_mcp_client
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact import ExactEvmScheme

load_dotenv()

SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:4022")
NETWORK = "eip155:84532"  # Base Sepolia


async def main() -> None:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "AAOI").strip().upper()

    async with CdpClient() as cdp:
        account = await cdp.evm.get_or_create_account(name="x402-client-wallet-1")
        signer = EthAccountSigner(EvmLocalAccount(account))
        print(f"Paying from {signer.address}")

        if os.getenv("CDP_FUND_FROM_FAUCET", "").lower() == "true":
            tx = await cdp.evm.request_faucet(
                address=signer.address, network="base-sepolia", token="usdc"
            )
            print(f"Requested USDC from the CDP faucet: {tx}")
            print("Wait for it to confirm, then re-run without the flag to pay.")
            return

        payment_client = x402Client()
        payment_client.register(NETWORK, ExactEvmScheme(signer))

        async with create_x402_mcp_client(payment_client, SERVER_URL) as mcp:
            tools = (await mcp.list_tools()).tools
            print("Tools:", ", ".join(t.name for t in tools))

            ping = await mcp.call_tool("ping", {})
            print(f"ping -> {ping.content[0].text}")

            report = await mcp.call_tool("generate_report", {"symbol": symbol})
            print(f"generate_report({symbol}) -> {report.content[0].text}")

            receipt = report.payment_response
            tx_hash = (
                receipt.get("transaction")
                if isinstance(receipt, dict)
                else getattr(receipt, "transaction", None)
            )
            if tx_hash:
                print(f"Settled tx: {tx_hash}")


if __name__ == "__main__":
    asyncio.run(main())
