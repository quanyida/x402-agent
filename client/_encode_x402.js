// 用 node child_process.spawn 调用 cdp util x402 encode，规避 shell 引号问题
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
  const sig = fs.readFileSync(BASE + "\\_pm_signature.txt", "utf8").trim();
  const accepts = fs.readFileSync(BASE + "\\_pm_accepts.json", "utf8").trim();
  const payload = JSON.parse(fs.readFileSync(BASE + "\\_pm_payload.json", "utf8"));
  const M = JSON.stringify(payload.message);

  console.log("=== cdp util x402 encode (v2) ===");
  const r = run([
    "util", "x402", "encode",
    "--payment-requirements", accepts,
    "--signature", sig,
    "--authorization", M,
    "--x402-version", "2",
  ]);
  if (r.stdout) console.log(r.stdout);
  if (r.stderr) console.error("[stderr]", r.stderr);
  console.log("exit:", r.status);
  if (r.stdout && r.status === 0) {
    fs.writeFileSync(BASE + "\\_pm_header.txt", r.stdout.trim(), "utf8");
    console.log("header saved, len:", r.stdout.trim().length);
  }
}

main();
