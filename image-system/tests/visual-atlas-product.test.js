"use strict";

const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const {
  PRODUCT_MANIFEST,
  verify,
} = require("../tools/visual-atlas-product");

const projectRoot = path.resolve(__dirname, "../..");

function sealed() {
  return JSON.parse(fs.readFileSync(path.join(projectRoot, PRODUCT_MANIFEST), "utf8"));
}

function hardlinkFixture() {
  const root = fs.mkdtempSync(path.join(projectRoot, ".aag-atlas-product-test-"));
  const product = sealed();
  const files = new Set([...Object.keys(product.metadata), PRODUCT_MANIFEST]);
  for (const entry of product.entries) {
    files.add(entry.reference.path);
    files.add(entry.thumbnail.path);
  }
  for (const relative of files) {
    const destination = path.join(root, relative);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.linkSync(path.join(projectRoot, relative), destination);
  }
  return { root, product };
}

test("Visual Atlas 1.0.0 product gate seals all mandatory product assets", () => {
  const report = verify(projectRoot);
  assert.equal(report.result, "PASS");
  assert.equal(report.expected_families, 28);
  assert.equal(report.expected_styles, 493);
  assert.deepEqual(report.gate, {
    EXPECTED_ATLAS_STYLES: 493,
    REFERENCES_VALID: "493/493",
    THUMBNAILS_VALID: "493/493",
    MANIFEST_INTEGRITY: "PASS",
  });
  assert.equal(sealed().entries.length, 493);
});

test("product gate fails a fresh-install tree with a required asset missing", () => {
  const fixture = hardlinkFixture();
  try {
    fs.unlinkSync(path.join(fixture.root, fixture.product.entries[0].thumbnail.path));
    assert.throws(() => verify(fixture.root), /ENOENT|product asset/i);
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});

test("product gate fails a fresh-install tree with a corrupt Atlas reference", () => {
  const fixture = hardlinkFixture();
  try {
    const target = path.join(fixture.root, fixture.product.entries[0].reference.path);
    fs.unlinkSync(target);
    fs.writeFileSync(target, "corrupt test fixture only");
    assert.throws(() => verify(fixture.root), /reference integrity failed/i);
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});

test("release builder hard-gates and records the mandatory versioned Atlas", () => {
  const source = fs.readFileSync(path.join(projectRoot, "image-agent/tools/build.js"), "utf8");
  assert.match(source, /verifyVisualAtlasProduct/);
  assert.match(source, /mandatory: true/);
  assert.match(source, /canonical-product-root-or-versioned-asset-bundle/);
  assert.match(source, /reference_set_sha256/);
  assert.match(source, /thumbnail_set_sha256/);
});
