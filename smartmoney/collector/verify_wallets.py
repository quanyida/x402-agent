"""验证已知 CEX/公开地址在 Base 上的 USDC 余额（eth_call balanceOf）"""
import os
import sys

_SM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SM_ROOT not in sys.path:
    sys.path.insert(0, _SM_ROOT)
from collector.rpc import rpc_call

USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# balanceOf(address) selector
BALANCEOF = "0x70a08231"

# 公开已知的 CEX 热钱包地址（来源：etherscan 标签/公开资料）
KNOWN_WALLETS = {
    "Coinbase-1": "0x71660c4005BA85c37ccec55d0C4493E66Cd775d3",
    "Coinbase-2": "0x503828976D22510aad0201ac7EC88293211D23Da",
    "Coinbase-3": "0xddfAbCdc4D8FfC6D5beaf154f18B778f892A0740",
    "Binance-14": "0x56Eddb7aa87536c09CCc2793473599fD21A8b17F",
    "Binance-15": "0xDFd5293D8e347dFe492E7ae80DED900e47DB0BC5",
    "Binance-16": "0x28C6c06298d514Db089934071355E5743bf21d60",
}


def get_usdc_balance(rpc_url, addr):
    data = BALANCEOF + addr[2:].lower().rjust(64, "0")
    r = rpc_call(rpc_url, "eth_call",
                 [{"to": USDC_BASE, "data": data}, "latest"])
    return int(r, 16) / 1e6


if __name__ == "__main__":
    for rpc_url in ["https://base.drpc.org", "https://publicnode.base"]:
        print(f"=== {rpc_url} ===")
        for name, addr in KNOWN_WALLETS.items():
            try:
                bal = get_usdc_balance(rpc_url, addr)
                print(f"  {name:14s} {addr}  USDC: {bal:>20,.2f}")
            except Exception as e:
                print(f"  {name:14s} {addr}  FAIL: {e}")
