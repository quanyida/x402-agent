"""x402 买家客户端：自动支付并获取报告

流程：
1. 创建/获取买家钱包（x402-client-wallet-1）
2. 检查 Base Sepolia USDC 余额，不足则领 faucet 并轮询等待到账
3. 用 x402HttpxClient 调用受保护端点（自动解析 402 -> 支付 -> 结算 -> 拿资源）
4. 输出 HTTP 状态与报告内容
"""

import asyncio
import json
import os
import sys
import urllib.request

from dotenv import load_dotenv

load_dotenv()

# 代理（cdp-sdk/httpx 在 import 时读取进程环境变量）
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:10809")
os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:10809")

# cdp-sdk 的 Urllib3AuthClient 硬编码 urllib3.PoolManager()（不带代理），
# 签名请求直连 api.cdp.coinbase.com 会超时。此处 patch Urllib3AuthClient：
# 将其内部 client 换成带代理的 urllib3.ProxyManager。
try:
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
    print("cdp Urllib3AuthClient proxy patch applied")
except Exception as e:  # 不影响主流程
    print(f"cdp proxy patch failed (non-fatal): {e}")

from cdp import CdpClient
from cdp.evm_local_account import EvmLocalAccount
from x402 import x402Client
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact import ExactEvmScheme

TARGET_URL = os.getenv("TARGET_URL", "http://127.0.0.1:8402/report/AAOI")
NETWORK = "eip155:84532"  # Base Sepolia
FAUCET_TIMEOUT_S = 120
USDC_CONTRACT = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # Base Sepolia USDC
BLOCKSCOUT = "https://base-sepolia.blockscout.com"


def chain_usdc_balance(address: str) -> float:
    """通过 Blockscout 查询链上实时 USDC 余额（不依赖 SDK 索引）。"""
    url = f"{BLOCKSCOUT}/api/v2/addresses/{address}/token-balances"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        for entry in data:
            token = entry.get("token", {})
            if token.get("symbol") == "USDC":
                decimals = int(token.get("decimals", 6))
                return int(entry.get("value", 0)) / (10**decimals)
    except Exception as e:
        print(f"Blockscout 查询失败（{e}），余额视为 0")
    return 0.0


async def ensure_usdc(cdp, signer_address: str) -> None:
    """检查 USDC 余额（链上实时），不足则领水并等待到账。"""
    account = await cdp.evm.get_or_create_account(name="x402-client-wallet-1")

    usdc_amount = chain_usdc_balance(signer_address)
    print(f"USDC balance (on-chain): ${usdc_amount:.6f}")
    if usdc_amount > 0:
        return

    # 领水
    print("USDC 余额不足，请求 faucet...")
    await cdp.evm.request_faucet(
        address=signer_address,
        network="base-sepolia",
        token="usdc",
    )
    print("faucet 已请求，等待到账...")
    # 轮询等待
    for i in range(FAUCET_TIMEOUT_S // 5):
        await asyncio.sleep(5)
        usdc_amount = chain_usdc_balance(signer_address)
        if usdc_amount > 0:
            print(f"USDC 已到账: ${usdc_amount:.6f}")
            return
    print("等待超时，请稍后重试")
    sys.exit(1)


async def main() -> None:
    async with CdpClient() as cdp:
        account = await cdp.evm.get_or_create_account(name="x402-client-wallet-1")
        signer = EthAccountSigner(EvmLocalAccount(account))
        print(f"Paying from {signer.address}")  # 这是要充值的地址

        await ensure_usdc(cdp, signer.address)

        payment_client = x402Client()
        payment_client.register(NETWORK, ExactEvmScheme(signer))

        async with x402HttpxClient(payment_client) as http:
            print(f"调用 {TARGET_URL} ...")
            response = await http.get(TARGET_URL)
            await response.aread()

        print(f"HTTP {response.status_code}")
        print("响应内容:")
        print(response.text[:800])


if __name__ == "__main__":
    asyncio.run(main())
