"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const endpoints = require("../src/endpoints");

test("defaults preserve the historical docker bridge gateway", () => {
  const resolved = endpoints.resolve({});
  assert.equal(resolved.COMFY, "http://172.18.0.1:18188");
  assert.equal(resolved.HUB_INTERNAL, "http://172.18.0.1:18190");
  assert.equal(resolved.UPSCALE_INTERNAL, "http://172.18.0.1:18191");
  assert.equal(resolved.HUB_PUBLIC, "http://127.0.0.1:18190");
});

test("gateway override rewrites every internal endpoint", () => {
  const resolved = endpoints.resolve({ AAG_ENGINE_GATEWAY: "172.17.0.1" });
  assert.equal(resolved.COMFY, "http://172.17.0.1:18188");
  assert.equal(resolved.HUB_INTERNAL, "http://172.17.0.1:18190");
  assert.equal(resolved.UPSCALE_INTERNAL, "http://172.17.0.1:18191");
  assert.equal(resolved.HUB_PUBLIC, "http://127.0.0.1:18190");
});

test("explicit service URL wins over the gateway", () => {
  const resolved = endpoints.resolve({ AAG_ENGINE_GATEWAY: "172.17.0.1", AAG_COMFY_URL: "http://127.0.0.1:8188" });
  assert.equal(resolved.COMFY, "http://127.0.0.1:8188");
  assert.equal(resolved.HUB_INTERNAL, "http://172.17.0.1:18190");
});

test("non-private, non-http, credentialed or pathed endpoints are rejected", () => {
  for (const env of [
    { AAG_COMFY_URL: "http://8.8.8.8:18188" },
    { AAG_COMFY_URL: "http://example.com:18188" },
    { AAG_HUB_URL: "https://127.0.0.1:18190" },
    { AAG_HUB_URL: "http://127.0.0.1:18190/files" },
    { AAG_UPSCALE_URL: "http://user:pass@127.0.0.1:18191" },
    { AAG_UPSCALE_URL: "http://127.0.0.1:18191?x=1" },
    { AAG_ENGINE_GATEWAY: "evil.example" },
    { AAG_COMFY_URL: "not a url" },
  ]) {
    assert.throws(() => endpoints.resolve(env), error => error.code === "ENDPOINT_CONFIG_INVALID", JSON.stringify(env));
  }
});

test("consumers import the shared endpoints", () => {
  const comfy = require("../src/comfy");
  const identity = require("../src/identity");
  const adapters = require("../src/adapters");
  assert.equal(comfy.COMFY, endpoints.COMFY);
  assert.equal(comfy.HUB, endpoints.HUB_INTERNAL);
  assert.equal(identity.COMFY, endpoints.COMFY);
  assert.equal(adapters.HUB_INTERNAL, endpoints.HUB_INTERNAL);
  assert.equal(adapters.UPSCALE_INTERNAL, endpoints.UPSCALE_INTERNAL);
});
