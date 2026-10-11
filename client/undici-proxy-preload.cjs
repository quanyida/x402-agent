// undici 全局代理注入：让 Node 内置 fetch 走本地代理
// 用法：$env:NODE_OPTIONS = "--require C:\path\to\undici-proxy-preload.cjs"
const { setGlobalDispatcher, EnvHttpProxyAgent } = require('undici');

// EnvHttpProxyAgent 从 HTTP_PROXY / HTTPS_PROXY 环境变量自动读取代理
const agent = new EnvHttpProxyAgent();
setGlobalDispatcher(agent);
console.error('[undici-proxy] global dispatcher set (env proxies)');
