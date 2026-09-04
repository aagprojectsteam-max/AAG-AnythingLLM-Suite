"use strict";

const assert = require("assert/strict");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const handler = require("../skill/aag-chess-puzzle/handler.js");
const api = handler._test;

function expectSkillError(callback, code = "invalid_request") {
  assert.throws(callback, error => error?.code === code);
}

async function withUnixServer(callback, response) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "aag-chess-handler-"));
  const socketPath = path.join(directory, "bridge.sock");
  const server = http.createServer((request, reply) => {
    const chunks = [];
    request.on("data", chunk => chunks.push(chunk));
    request.on("end", () => {
      callback(JSON.parse(Buffer.concat(chunks).toString("utf8")), request.url);
      reply.writeHead(response.status, { "Content-Type": "application/json" });
      reply.end(JSON.stringify(response.body));
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(socketPath, resolve);
  });
  try {
    return await api.requestBridge(response.request, {
      socketPath,
      timeoutMs: 1000,
      ...(response.options || {})
    });
  } finally {
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

async function main() {
  assert.deepEqual(api.normalizeArgs({ mate: 3, side: "black" }), {
    action: "generate",
    mate: 3,
    side: "black",
    difficulty: "medium",
    count: 1,
    engine: "auto",
    formats: "png",
    seed: 0,
    max_attempts: 1000,
    stockfish_nodes: 20000,
    density: "auto",
    _difficulty_defaulted: true,
    _seed_defaulted: true
  });
  assert.equal(api.normalizeFormats("PNG + SVG"), "svg+png");
  assert.equal(api.normalizeFormats(["svg", "png"]), "svg+png");
  assert.equal(api.normalizeArgs({ mate: 3, difficulty: "medium" })._difficulty_defaulted, false);
  assert.equal(api.normalizeArgs({ mate: 2 })._seed_defaulted, true);
  assert.equal(api.normalizeArgs({ mate: 2, seed: 0 })._seed_defaulted, false);
  assert.deepEqual(api.normalizeArgs({ action: "hint" }), {
    action: "hint",
    puzzle_number: null,
    public_id: null
  });
  assert.deepEqual(api.normalizeArgs({ action: "solution", puzzle_number: 2 }), {
    action: "solution",
    puzzle_number: 2,
    public_id: null
  });
  expectSkillError(() => api.normalizeArgs({ action: "solution", mate: 2 }));
  expectSkillError(() => api.normalizeArgs({ action: "hint", puzzle_number: 0 }));
  expectSkillError(() => api.normalizeArgs({ action: "solution", public_id: "../../etc/passwd" }));
  expectSkillError(() => api.normalizeArgs({ mate: 2, side: "white; id" }));
  expectSkillError(() => api.normalizeArgs({ mate: 2, count: 11 }));
  expectSkillError(() => api.normalizeArgs({ mate: 2, output: "/tmp/out" }));
  expectSkillError(() => api.normalizeArgs({ mate: 2, stockfish_nodes: 200001 }));
  expectSkillError(() => api.normalizeArgs({ mate: 2, density: "crowded" }));
  assert.equal(api.normalizeArgs({ mate: 2, density: "rich" }).density, "rich");
  expectSkillError(
    () => api.safeRelativePath("../../etc/passwd", "artifact"),
    "unsafe_artifact"
  );
  expectSkillError(
    () => api.safeRelativePath("request-not-a-uuid/manifest.json", "manifest"),
    "unsafe_artifact"
  );
  expectSkillError(
    () => api.assertNoSolutionLeak({ nested: { proof_tree: ["e2e4"] } }),
    "unsafe_bridge_response"
  );

  const safeResponse = {
    schema: "aag-anythingllm-chess-result-v1",
    status: "success",
    generated_count: 1,
    artifacts: []
  };
  let observed;
  const resolved = await withUnixServer(
    payload => {
      observed = payload;
    },
    { status: 200, body: safeResponse, request: { mate: 2, engine: "builtin" } }
  );
  assert.deepEqual(observed, { mate: 2, engine: "builtin" });
  assert.equal(resolved.status, "success");

  assert.deepEqual(
    api.trustedScope({ AAG_WORKSPACE_ID: "abc/unsafe", AAG_THREAD_ID: 17 }),
    { workspace_id: "abc_unsafe", thread_id: "17", user_id: "unknown" }
  );
  assert.equal(
    api.generationMessage({
      generated_count: 1,
      mate: 2,
      side: "white",
      measured_difficulty: "medium"
    }),
    "חידת מט ב־2 מוכנה.\n\nהלבן נוסע.\n\nרמת קושי: בינוני."
  );
  assert.equal(
    api.generationMessage({
      generated_count: 3,
      mate: 3,
      side: "black",
      measured_difficulty: "hard"
    }),
    "3 חידות מט ב־3 מוכנות.\n\nהשחור נוסע.\n\nרמת קושי: קשה."
  );
  const cleanGeneration = api.cleanGenerationResult(
    {
      generated_count: 1,
      mate: 2,
      side: "white",
      measured_difficulty: "medium",
      public_ids: ["aag-0123456789abcdef0123"],
      context_token: `ctx_${"a".repeat(64)}`,
      application_version: "internal-version"
    },
    {
      attachments: [{ sha256: "b".repeat(64) }],
      inline_images: [{
        browser_url: "/api/image-generation/generated-images/img-12345678-1234-4123-8123-123456789abc.png",
        storage_filename: "internal.png"
      }]
    }
  );
  const cleanGenerationText = JSON.stringify(cleanGeneration);
  assert.equal(cleanGeneration.downloads_available, true);
  assert(!cleanGenerationText.includes("ctx_"));
  assert(!cleanGenerationText.includes("aag-012345"));
  assert(!cleanGenerationText.includes("internal-version"));
  assert(!cleanGenerationText.includes("sha256"));
  const cleanFollowup = api.cleanFollowupResult({
    answer_he: "הפתרון:\n\n\u20661. Rc4!\u2069",
    public_id: "aag-0123456789abcdef0123",
    verified_source: { certificate_sha256: "c".repeat(64) }
  });
  assert.deepEqual(cleanFollowup, { answer_he: "הפתרון:\n\n\u20661. Rc4!\u2069" });
  const handlerSource = fs.readFileSync(
    path.join(__dirname, "../skill/aag-chess-puzzle/handler.js"),
    "utf8"
  );
  const pluginSource = fs.readFileSync(
    path.join(__dirname, "../skill/aag-chess-puzzle/plugin.json"),
    "utf8"
  );
  assert(!handlerSource.includes("AAG_CHESS_CONTEXT"));
  assert(!pluginSource.includes("conversation_capsule"));

  let followupPath;
  const followup = await withUnixServer(
    (payload, requestPath) => {
      followupPath = requestPath;
      assert.equal(payload.action, "hint");
    },
    {
      status: 200,
      body: {
        schema: "aag-anythingllm-chess-followup-v1",
        status: "success",
        action: "hint",
        answer_he: "רמז מאומת"
      },
      request: { action: "hint" },
      options: {
        endpoint: "/v1/followup",
        expectedSchema: "aag-anythingllm-chess-followup-v1"
      }
    }
  );
  assert.equal(followup.action, "hint");
  assert.equal(followupPath, "/v1/followup");

  const sent = [];
  const pending = [];
  const emittedMessages = [];
  const superContext = {
    socket: { send: (...items) => sent.push(items) },
    newMessage: message => emittedMessages.push(message),
    trackedChatId: 42,
    handlerProps: {
      invocation: { workspace_id: 11, user_id: null, thread_id: null }
    }
  };
  const inline = await api.publishInlinePng(
    { super: superContext },
    Buffer.from("png-data"),
    "aag-chess-puzzle-0001.png",
    {
      saveGeneratedImage: async ({ buffer }) => ({
        storageFilename: "img-12345678-1234-4123-8123-123456789abc.png",
        filename: "verified.png",
        fileSize: buffer.length
      })
    },
    { registerOutput: (context, type, payload) => pending.push({ type, payload }) },
    {
      get: async ({ id }) => ({
        id,
        workspaceId: 11,
        user_id: null,
        thread_id: null,
        include: false,
        api_session_id: null,
        response: "{}"
      }),
      _update: async (id, value) => {
        assert.equal(id, 42);
        const response = JSON.parse(value.response);
        assert.equal(response.outputs[0].type, "imageGenerationCard");
        assert.equal(value.include, true);
        return true;
      }
    }
  );
  assert.equal(inline.storageFilename, "img-12345678-1234-4123-8123-123456789abc.png");
  assert.equal(sent.length, 0);
  assert.equal(pending[0].type, "imageGenerationCard");
  assert.equal(pending[0].payload.filename, "aag-chess-puzzle-0001.png");
  superContext.newMessage({ from: "WORKSPACE", content: "done" });
  assert.equal(emittedMessages.length, 1);
  assert.equal(emittedMessages[0].outputs[0].type, "imageGenerationCard");
  assert.equal(emittedMessages[0].outputs[0].payload.storageFilename, inline.storageFilename);
  assert.equal(
    inline.browserUrl,
    "/api/image-generation/generated-images/img-12345678-1234-4123-8123-123456789abc.png"
  );
  expectSkillError(() => api.inlineBrowserUrl("../../private.json"), "unsafe_artifact");
  expectSkillError(
    () => api.inlineBrowserUrl("ctx_" + "a".repeat(64) + ".json"),
    "unsafe_artifact"
  );
  await assert.rejects(
    api.primeImageOwnership(
      { trackedChatId: 42, handlerProps: { invocation: { workspace_id: 11 } } },
      inline,
      { get: async () => ({ workspaceId: 99, include: true }) }
    ),
    error => error?.code === "artifact_delivery_unavailable"
  );
  assert.equal(
    await api.primeImageOwnership(
      { handlerProps: { invocation: { workspace_id: 11 } } },
      inline,
      { get: async () => { throw new Error("must not load a browser row"); } }
    ),
    false
  );

  await assert.rejects(
    withUnixServer(
      () => {},
      {
        status: 422,
        body: { status: "error", error: "generation_budget_exhausted", message: "bounded" },
        request: { mate: 2 }
      }
    ),
    error => error?.code === "generation_budget_exhausted"
  );

  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "aag-chess-timeout-"));
  const socketPath = path.join(directory, "bridge.sock");
  const slowServer = http.createServer(() => {});
  await new Promise(resolve => slowServer.listen(socketPath, resolve));
  await assert.rejects(
    api.requestBridge({ mate: 1 }, { socketPath, timeoutMs: 20 }),
    error => error?.code === "skill_timeout"
  );
  await new Promise(resolve => slowServer.close(resolve));
  fs.rmSync(directory, { recursive: true, force: true });

  console.log("anythingllm handler tests: PASS");
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
