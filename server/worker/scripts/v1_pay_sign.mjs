/**
 * x402 v1 付款：用 cdp CLI 签名 EIP-712 → 组装 X-PAYMENT header
 * 用法: node v1_pay_sign.mjs <typed.json> <req.json> <header-out.txt>
 * 通过 Node spawn 直接调 cdp-cli，避免 shell 引号破坏 JSON
 */
import { readFileSync, writeFileSync } from "fs";
import { execFileSync } from "child_process";

const [typedPath, reqPath, outPath] = process.argv.slice(2);
if (!typedPath || !reqPath || !outPath) {
  console.error("usage: node v1_pay_sign.mjs <typed.json> <req.json> <header-out.txt>");
  process.exit(1);
}

const typed = JSON.parse(readFileSync(typedPath, "utf8"));
const req = JSON.parse(readFileSync(reqPath, "utf8"));
const from = typed.message.from;

const CDP_CLI = "C:\\Users\\23317\\AppData\\Local\\Doubao\\User Data\\sandbox_runtime\\bases\\d0ace100fdff5b2d247847aca1e3d83b\\node\\node_modules\\@coinbase\\cdp-cli\\dist\\index.js";

const args = [
  CDP_CLI,
  "evm", "accounts", "sign", "typed-data", from,
  `domain:=${JSON.stringify(typed.domain)}`,
  `types:=${JSON.stringify(typed.types)}`,
  `primaryType=${typed.primaryType}`,
  `message:=${JSON.stringify(typed.message)}`,
];

console.error("[spawn] cdp evm accounts sign typed-data ...");
const stdout = execFileSync(process.execPath, args, {
  encoding: "utf8",
  env: {
    ...process.env,
    HTTP_PROXY: "http://127.0.0.1:10809",
    HTTPS_PROXY: "http://127.0.0.1:10809",
    NODE_OPTIONS: "--require F:\\个人知识库\\x402-report-server\\undici-proxy-preload.cjs",
  },
  stdio: ["ignore", "pipe", "inherit"],
});

let out;
try {
  out = JSON.parse(stdout);
} catch {
  console.error("raw stdout:", stdout.slice(0, 500));
  process.exit(1);
}
const signature = out.signature;
if (!signature) {
  console.error("no signature in output:", JSON.stringify(out).slice(0, 500));
  process.exit(1);
}
console.error("signature:", signature.slice(0, 40) + "...");

// 组装 v1 payment header
const payment = {
  x402Version: 1,
  scheme: "exact",
  network: req.network,
  payload: {
    signature,
    authorization: {
      from: typed.message.from,
      to: typed.message.to,
      value: typed.message.value,
      validAfter: typed.message.validAfter,
      validBefore: typed.message.validBefore,
      nonce: typed.message.nonce,
    },
  },
};

const header = Buffer.from(JSON.stringify(payment)).toString("base64");
writeFileSync(outPath, header, "utf8");
console.log("header written to", outPath);
console.log("header len:", header.length);
