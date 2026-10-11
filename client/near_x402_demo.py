"""Near × x402 组合实验 P0：NEAR 协调层触发 + x402 支付层闭环

链路：
  雇主（NEAR mainnet a86069c1...）→ 任务发布
  → 执行层（本脚本，模拟 NEAR Agent worker）
  → x402 付费调用（CDP 托管钱包付 $0.01 USDC，Base Sepolia）
  → x402-report FastAPI 服务返回报告
  → 交付（输出闭环证据）

支付路径：CDP 托管钱包（P0 降级路径；P2 升级为 NEAR Intents 换 USDC 后由派生地址支付）
"""

import asyncio
import json
import os
import re
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

# 代理（cdp-sdk/httpx 在 import 时读取进程环境变量）
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:10809")
os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:10809")

# cdp-sdk 的 Urllib3AuthClient 硬编码 urllib3.PoolManager()（不带代理），patch 成 ProxyManager
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
    print("[patch] cdp urllib3 proxy applied")
except Exception as e:
    print(f"[patch] failed (non-fatal): {e}")

from cdp import CdpClient  # noqa: E402
from cdp.evm_local_account import EvmLocalAccount  # noqa: E402
from x402 import x402Client  # noqa: E402
from x402.http.clients import x402HttpxClient  # noqa: E402
from x402.mechanisms.evm import EthAccountSigner  # noqa: E402
from x402.mechanisms.evm.exact import ExactEvmScheme  # noqa: E402

# ---- 配置 ----
NEAR_ACCOUNT = "a86069c1b67dd70dd619563fd802d5daff14c4bbbae09d756824c89750fd0077"
NEAR_BIN = r"C:\Users\23317\.cargo\bin\near.exe"
SYMBOL = os.getenv("SYMBOL", "AAOI")
TASK = {"task": "generate_stock_report", "symbol": SYMBOL, "employer": NEAR_ACCOUNT}
TARGET_URL = os.getenv("TARGET_URL", f"http://127.0.0.1:8402/report/{SYMBOL}")
NETWORK = "eip155:84532"  # Base Sepolia


def near_side():
    """NEAR 协调层：查询雇主账户余额（只读 view 命令）。"""
    print("\n=== [NEAR 协调层] 雇主账户 ===")
    cmd = [NEAR_BIN, "tokens", NEAR_ACCOUNT, "view-near-balance",
           "network-config", "mainnet", "now"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        # 优先匹配 "has X NEAR available for transfer"
        m = re.search(r"has\s+([\d.]+)\s*NEAR\s+available", out)
        if not m:
            m = re.search(r"([\d.]+)\s*NEAR", out)
        bal = m.group(1) if m else f"无法解析（{out.strip()[:80]}）"
    except Exception as e:
        bal = f"near-cli 调用失败: {e}"
    print(f"雇主 {NEAR_ACCOUNT[:12]}... 余额: {bal} NEAR")
    print(f"任务: {json.dumps(TASK, ensure_ascii=False)}")
    return bal


async def x402_side():
    """x402 支付层：CDP 托管钱包付费调用 report 服务（自动 402→支付→结算）。"""
    print("\n=== [x402 支付层] 付费调用 ===")
    async with CdpClient() as cdp:
        account = await cdp.evm.get_or_create_account(name="x402-client-wallet-1")
        signer = EthAccountSigner(EvmLocalAccount(account))
        print(f"付款方钱包: {signer.address}")
        print(f"调用 {TARGET_URL}（自动处理 402 → 签名支付 → 链上结算）...")

        payment_client = x402Client()
        payment_client.register(NETWORK, ExactEvmScheme(signer))

        async with x402HttpxClient(payment_client, timeout=60.0) as http:
            resp = await http.get(TARGET_URL)
            await resp.aread()

        print(f"HTTP {resp.status_code}")
        print(f"报告摘要: {resp.text[:200].replace(chr(10), ' ')}")
        return resp.status_code, resp.text


async def main():
    print("=" * 62)
    print("Near × x402 组合实验 P0 —— 最小闭环")
    print("=" * 62)
    bal = near_side()
    status, text = await x402_side()

    print("\n=== [交付] ===")
    if status == 200:
        print("✅ 任务完成：NEAR 雇主触发 → x402 付费 → 报告交付")
        print(f"   雇主: {NEAR_ACCOUNT[:12]}...（{bal} NEAR）")
        print(f"   标的: {SYMBOL} | HTTP 200 | 付费 $0.01 USDC（Base Sepolia）")
    else:
        print(f"❌ 任务失败: HTTP {status} | {text[:100]}")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
