"""推送完整版简报到 Cloudflare KV（供 /smartmoney-brief 付费端点消费）

用法：
    python push_brief.py              # 从 data/latest_brief_full.md 读取并推送
    python push_brief.py --no-build   # 不重新生成简报，直接推送现有内容
"""
import json
import os
import subprocess
import sys

_SM_ROOT = os.path.dirname(os.path.abspath(__file__))
WORKER_DIR = os.path.join(os.path.dirname(_SM_ROOT), "server", "worker")
DATA_DIR = os.path.join(_SM_ROOT, "data")
BRIEF_FULL = os.path.join(DATA_DIR, "latest_brief_full.md")


def build_brief():
    """重新生成简报"""
    subprocess.run([sys.executable, os.path.join(_SM_ROOT, "briefing", "generate_brief.py")],
                   cwd=_SM_ROOT, check=True)


def push_to_kv():
    if not os.path.exists(BRIEF_FULL):
        print(f"未找到 {BRIEF_FULL}，先运行简报生成")
        build_brief()
    with open(BRIEF_FULL, "r", encoding="utf-8") as f:
        content = f.read()
    payload = json.dumps({"date": "2026-10-14", "generated_at": "2026-10-14",
                          "content": content}, ensure_ascii=False)
    tmp = os.path.join(DATA_DIR, "kv_payload.json")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    # wrangler kv key put（Windows 下 npx 是 npx.cmd，subprocess 需 shell=True）
    cmd = f'npx wrangler kv key put latest_brief_full --binding SMARTMONEY_BRIEF --path "{tmp}" --preview false --remote'
    print(f"推送 KV: {cmd}")
    subprocess.run(cmd, cwd=WORKER_DIR, shell=True, check=True)
    os.remove(tmp)
    print("KV 推送完成 ✓")


if __name__ == "__main__":
    if "--no-build" not in sys.argv:
        build_brief()
    push_to_kv()
