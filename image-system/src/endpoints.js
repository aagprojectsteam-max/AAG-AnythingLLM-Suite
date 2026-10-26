"use strict";
// Single source of truth for the local engine endpoints the image agent talks to.
// Defaults preserve the historical Docker bridge gateway. Overrides come only from
// AAG_ENGINE_GATEWAY (host) or the explicit per-service AAG_*_URL variables, and
// every value is validated to be a plain http:// URL on a private address with no
// credentials, path, query or fragment, so a misconfiguration cannot point the
// agent at an arbitrary host.
const { AagError } = require("./errors");

const DEFAULT_GATEWAY = "172.18.0.1";
const PORTS = Object.freeze({ comfy: 18188, hub: 18190, upscale: 18191 });
const PRIVATE_HOST = /^(127\.\d{1,3}\.\d{1,3}\.\d{1,3}|localhost|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})$/;

function validate(raw, name) {
  let parsed;
  try { parsed = new URL(String(raw)); } catch { throw new AagError("ENDPOINT_CONFIG_INVALID", `${name} is not a valid URL.`); }
  if (parsed.protocol !== "http:" || parsed.username || parsed.password || parsed.search || parsed.hash || (parsed.pathname !== "/" && parsed.pathname !== "")) {
    throw new AagError("ENDPOINT_CONFIG_INVALID", `${name} must be a plain http://host:port URL.`);
  }
  if (!PRIVATE_HOST.test(parsed.hostname)) throw new AagError("ENDPOINT_CONFIG_INVALID", `${name} must point at a private address.`);
  return `http://${parsed.hostname}:${parsed.port || "80"}`;
}

function resolve(env = process.env) {
  const gateway = String(env.AAG_ENGINE_GATEWAY || DEFAULT_GATEWAY).trim();
  const build = (service) => validate(env[`AAG_${service.toUpperCase()}_URL`] || `http://${gateway}:${PORTS[service]}`, `AAG_${service.toUpperCase()}_URL`);
  return Object.freeze({
    COMFY: build("comfy"),
    HUB_INTERNAL: build("hub"),
    UPSCALE_INTERNAL: build("upscale"),
    // The public hub URL is what ends up in chat artifacts; it is deliberately
    // fixed to loopback because the frontend and the output policy validate it.
    HUB_PUBLIC: `http://127.0.0.1:${PORTS.hub}`,
  });
}

module.exports = { ...resolve(), resolve, validate, DEFAULT_GATEWAY, PORTS };
