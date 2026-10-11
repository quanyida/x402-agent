// 用 node child_process.spawn 调用 cdp-cli，规避 PowerShell/cmd 引号剥离问题
const { spawnSync } = require('child_process');
const fs = require('fs');

const CDP_INDEX = "C:\\Users\\23317\\AppData\\Local\\Doubao\\User Data\\sandbox_runtime\\bases\\d0ace100fdff5b2d247847aca1e3d83b\\node\\node_modules\\@coinbase\\cdp-cli\\dist\\index.js";
const BASE = "F:\\个人知识库\\x402-report-server";

function run(args) {
  const res = spawnSync(process.execPath, [CDP_INDEX, ...args], {
    cwd: BASE,
    env: {
      ...process.env,
      HTTP_PROXY: "http://127.0.0.1:10809",
      HTTPS_PROXY: "http://127.0.0.1:10809",
      NODE_OPTIONS: "--require F:\\个人知识库\\x402-report-server\\undici-proxy-preload.cjs",
    },
    encoding: 'utf8',
    maxBuffer: 1024 * 1024 * 10,
  });
  return { status: res.status, stdout: res.stdout, stderr: res.stderr };
}

function main() {
  const addr = process.argv[2];
  const payload = JSON.parse(fs.readFileSync(BASE + "\\_pm_payload.json", "utf8"));
  const D = JSON.stringify(payload.domain);
  const T = JSON.stringify(payload.types);
  const P = payload.primaryType;
  const M = JSON.stringify(payload.message);

  console.log("=== CDP 签名 typed-data ===");
  const r = run([
    "evm", "accounts", "sign", "typed-data", addr,
    `domain:=${D}`,
    `types:=${T}`,
    `primaryType=${P}`,
    `message:=${M}`,
  ]);
  if (r.stdout) console.log(r.stdout);
  if (r.stderr) console.error("[stderr]", r.stderr);
  console.log("exit:", r.status);
  if (r.stdout) {
    try {
      const obj = JSON.parse(r.stdout);
      if (obj.signature) fs.writeFileSync(BASE + "\\_pm_signature.txt", obj.signature, "utf8");
    } catch (e) { /* non-json output */ }
  }
}

main();
