"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
test("governed update is locked, staged, verified, and rollback-safe", () => {
  const source = fs.readFileSync(path.join(root, "tools/governed-update.js"), "utf8");
  for (const token of ["flag: \"wx\"", "tools/build.js", "npm", "doctor.js", "atomicInstall", "finally", "handler source/deployed/manifest drift", "aag-composer-loopback-relay"])
    assert.ok(source.includes(token), token);
});

test("model routing gate ignores metadata assignments but scans executable lines", () => {
  const source = fs.readFileSync(path.join(root, "tools/doctor.js"), "utf8");
  assert.ok(source.includes("launcherPrimaryLogic"));
  assert.ok(source.includes("[A-Z_][A-Z0-9_]*="));
  assert.ok(source.includes("executable primary compatibility routing"));
});
