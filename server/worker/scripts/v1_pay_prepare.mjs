/**
 * x402 v1 付款构造（配合 cdp CLI 签名）
 * 用法:
 *   node v1_pay_prepare.mjs <req.json> <from> <out.json>
 *   生成 EIP-712 typed data 供 cdp evm accounts sign typed-data 签名
 */
import { readFileSync, writeFileSync } from "fs";
import { webcrypto } from "crypto";

const [reqPath, from, outPath] = process.argv.slice(2);
if (!reqPath || !from || !outPath) {
  console.error("usage: node v1_pay_prepare.mjs <req.json> <from> <out.json>");
  process.exit(1);
}

const req = JSON.parse(readFileSync(reqPath, "utf8"));

// nonce: 32 bytes hex
const bytes = webcrypto.getRandomValues(new Uint8Array(32));
const nonce = "0x" + [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");

const now = Math.floor(Date.now() / 1000);
const validAfter = String(now - 600);
const validBefore = String(now + Number(req.maxTimeoutSeconds));

const message = {
  from,
  to: req.payTo,
  value: req.maxAmountRequired,
  validAfter,
  validBefore,
  nonce,
};

const domain = {
  name: req.extra.name,       // "USDC"
  version: req.extra.version, // "2"
  chainId: 84532,             // base-sepolia
  verifyingContract: req.asset,
};

const types = {
  EIP712Domain: [
    { name: "name", type: "string" },
    { name: "version", type: "string" },
    { name: "chainId", type: "uint256" },
    { name: "verifyingContract", type: "address" },
  ],
  TransferWithAuthorization: [
    { name: "from", type: "address" },
    { name: "to", type: "address" },
    { name: "value", type: "uint256" },
    { name: "validAfter", type: "uint256" },
    { name: "validBefore", type: "uint256" },
    { name: "nonce", type: "bytes32" },
  ],
};

const typedData = {
  domain,
  types,
  primaryType: "TransferWithAuthorization",
  message,
};

writeFileSync(outPath, JSON.stringify(typedData, null, 2));
console.log("typed data written to", outPath);
console.log("primaryType:", typedData.primaryType);
console.log("from:", from, "-> to:", req.payTo, "value:", req.maxAmountRequired);
