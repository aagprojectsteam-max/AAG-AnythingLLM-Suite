#!/usr/bin/env node
"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const https = require("https");
const path = require("path");

const projectRoot = path.resolve(__dirname, "../..");
const endpoint = "/api/aag-composer/image-generator";
const product = JSON.parse(
  fs.readFileSync(path.join(projectRoot, "visual-atlas/manifest/product-assets.json"), "utf8")
);
const taxonomy = JSON.parse(
  fs.readFileSync(
    path.join(
      projectRoot,
      "image-agent/integrations/model-neutral-compatibility/composer/visual-taxonomy.json"
    ),
    "utf8"
  )
);

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function parseArguments(argv) {
  const option = (name, fallback = null) => {
    const index = argv.indexOf(name);
    return index >= 0 ? argv[index + 1] : fallback;
  };
  return {
    baseUrl: option("--base-url", "https://anythingllm.localhost"),
    workspacePath: option(
      "--workspace-path",
      "/workspace/image-generator/t/896a89d0-5579-4b71-b7e5-427abfcbf64d"
    ),
    output: option("--output"),
    authorization: process.env.AAG_ANYTHINGLLM_AUTHORIZATION || "",
    insecure: argv.includes("--insecure"),
  };
}

function request(baseUrl, requestPath, { headers = {}, insecure = false } = {}) {
  const target = new URL(requestPath, baseUrl);
  const transport = target.protocol === "https:" ? https : http;
  return new Promise((resolve, reject) => {
    const req = transport.request(
      target,
      {
        method: "GET",
        headers,
        ...(target.protocol === "https:" ? { rejectUnauthorized: !insecure } : {}),
      },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () =>
          resolve({
            status: response.statusCode,
            headers: response.headers,
            body: Buffer.concat(chunks),
            url: target.toString(),
          })
        );
      }
    );
    req.on("error", reject);
    req.setTimeout(15_000, () => req.destroy(new Error("Atlas endpoint request timed out")));
    req.end();
  });
}

async function main() {
  const args = parseArguments(process.argv.slice(2));
  const selected = product.entries.find(
    (entry) => entry.key === "cinematic-film-still/feature-film-look"
  );
  const alternative = product.entries.find(
    (entry) => entry.key === "cinematic-film-still/documentary-film"
  );
  if (!selected || !alternative) throw new Error("Required production acceptance styles are absent");
  const version = `${selected.reference.sha256.slice(0, 16)}-webp192-v1`;
  const commonHeaders = {
    Accept: "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "X-AAG-Workspace-Path": args.workspacePath,
    "X-AAG-Workspace-Slug": "image-generator",
    ...(args.authorization ? { Authorization: args.authorization } : {}),
  };
  const thumbnail = await request(
    args.baseUrl,
    `${endpoint}/atlas-thumbnail/${selected.key}?v=${version}`,
    { headers: commonHeaders, insecure: args.insecure }
  );
  const preview = await request(
    args.baseUrl,
    `${endpoint}/atlas-preview/${selected.key}?v=${selected.reference.sha256.slice(0, 16)}-png512-v1`,
    { headers: commonHeaders, insecure: args.insecure }
  );
  const second = await request(
    args.baseUrl,
    `${endpoint}/atlas-thumbnail/${alternative.key}?v=${alternative.reference.sha256.slice(0, 16)}-webp192-v1`,
    { headers: commonHeaders, insecure: args.insecure }
  );
  const rejectedWithoutContext = await request(
    args.baseUrl,
    `${endpoint}/atlas-thumbnail/${selected.key}?v=guard-check`,
    {
      headers: args.authorization ? { Authorization: args.authorization } : {},
      insecure: args.insecure,
    }
  );
  const rejectedWrongWorkspace = await request(
    args.baseUrl,
    `${endpoint}/atlas-thumbnail/${selected.key}?v=guard-check-2`,
    {
      headers: {
        ...commonHeaders,
        "X-AAG-Workspace-Path": "/workspace/not-image-generator",
      },
      insecure: args.insecure,
    }
  );
  const family = taxonomy.families.find((item) => item.id === "cinematic-film-still");
  const style = family?.subfamilies.find((item) => item.id === "feature-film-look");
  const checks = {
    exact_taxonomy_entry: style?.label === "Feature-film look",
    authenticated_fetch_without_referer_200: thumbnail.status === 200,
    thumbnail_content_type: thumbnail.headers["content-type"] === "image/webp",
    thumbnail_hash_matches_seal: sha256(thumbnail.body) === selected.thumbnail.sha256,
    thumbnail_nonempty: thumbnail.body.length === selected.thumbnail.bytes,
    preview_200: preview.status === 200,
    preview_content_type: preview.headers["content-type"] === "image/png",
    preview_hash_matches_manifest: sha256(preview.body) === selected.reference.sha256,
    alternative_thumbnail_200: second.status === 200,
    changed_style_returns_different_bytes:
      sha256(second.body) === alternative.thumbnail.sha256 &&
      sha256(second.body) !== sha256(thumbnail.body),
    immutable_private_cache:
      thumbnail.headers["cache-control"] === "private, max-age=31536000, immutable",
    nosniff: thumbnail.headers["x-content-type-options"] === "nosniff",
    same_origin_resource_policy:
      thumbnail.headers["cross-origin-resource-policy"] === "same-origin",
    missing_workspace_context_rejected: rejectedWithoutContext.status === 403,
    wrong_workspace_rejected: rejectedWrongWorkspace.status === 403,
  };
  const report = {
    schema: "aag.visual-atlas.production-endpoint-regression.v1",
    captured_at: new Date().toISOString(),
    origin: args.baseUrl,
    workspace_path: args.workspacePath,
    request_mode: "authenticated-fetch-with-explicit-workspace-context-no-referer",
    selected_style: selected.key,
    thumbnail_url: thumbnail.url,
    thumbnail_status: thumbnail.status,
    thumbnail_content_type: thumbnail.headers["content-type"],
    thumbnail_sha256: sha256(thumbnail.body),
    preview_url: preview.url,
    preview_status: preview.status,
    preview_content_type: preview.headers["content-type"],
    preview_sha256: sha256(preview.body),
    checks,
    passed: Object.values(checks).filter(Boolean).length,
    failed: Object.values(checks).filter((value) => !value).length,
    result: Object.values(checks).every(Boolean) ? "PASS" : "FAIL",
  };
  const output = `${JSON.stringify(report, null, 2)}\n`;
  if (args.output) {
    fs.mkdirSync(path.dirname(path.resolve(args.output)), { recursive: true });
    fs.writeFileSync(path.resolve(args.output), output, { mode: 0o600 });
  }
  process.stdout.write(output);
  if (report.result !== "PASS") process.exitCode = 1;
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
