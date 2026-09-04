"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");
const path = require("node:path");

const {
  composerCanonicalJson,
  composerInvocationPrompt,
  composerModelAttachments,
  composerModelHistory,
  composerRuntimeAttachments,
  installComposerFailurePersistence,
  installComposerHistoryPersistence,
  visibleComposerPrompt,
  withComposerVisibleHistory,
} = require("../integrations/anythingllm/aagComposerHistory");

const COMPATIBILITY_ROOT = path.resolve(
  __dirname,
  "../integrations/model-neutral-compatibility"
);

function pythonSignedEnvelope(data) {
  const script = String.raw`
import json, sys
from types import SimpleNamespace
from compatibility import compose_request
from server import Application
data = json.load(sys.stdin)
message, _attachments = compose_request(data)
signer = SimpleNamespace(auth_token="cross-runtime-test-key")
signed = Application._sign_composer_message(signer, message)
print(json.dumps({"modelMessage": signed}, ensure_ascii=False))
`;
  const result = spawnSync("python3", ["-c", script], {
    cwd: COMPATIBILITY_ROOT,
    input: JSON.stringify(data),
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout).modelMessage;
}

function envelope(userRequest = "שועל רודף אחרי חתול") {
  const intent = {
    aspect_ratio: "auto",
    count: 1,
    creative_direction: { visual_family: "fantasy" },
    operation: "generate",
    semantics: {
      explicit_constraints: {
        count: 1,
        operation: "generate",
        visual_family: "fantasy",
      },
      model_discretion_fields: ["aspect_ratio"],
    },
    user_request_sha256: crypto
      .createHash("sha256")
      .update(userRequest, "utf8")
      .digest("hex"),
  };
  return (
    "Trusted Composer context\n" +
    `AAG_COMPOSER_STRUCTURED_REQUIREMENTS_V1=${JSON.stringify(intent)}\n` +
    `USER_CREATIVE_DIRECTION=\n${userRequest}\n` +
    `AAG_COMPOSER_INTENT_SIGNATURE_V1=${"a".repeat(64)}`
  );
}

test("visible history extracts exact user text and fails closed on malformed envelopes", () => {
  const exact = "  שועל רודף אחרי חתול\n";
  const signed = envelope(exact);
  assert.equal(visibleComposerPrompt(signed), exact);
  const tampered = signed.replace("שועל", "כלב");
  assert.equal(visibleComposerPrompt(tampered), "");
  assert.throws(
    () => composerInvocationPrompt(tampered),
    (error) => error.code === "AAG_COMPOSER_ENVELOPE_INVALID"
  );
  assert.equal(visibleComposerPrompt("ordinary native chat"), "ordinary native chat");
  assert.equal(composerInvocationPrompt("ordinary native chat"), "ordinary native chat");
});

test("RFC 8785 Composer numbers are stable across Python and JavaScript", () => {
  const values = [
    1.0,
    0.0,
    2.0,
    100.0,
    0.72,
    0.95,
    0.123456,
    0.000001,
    1e-7,
  ];
  const script = String.raw`
import json, sys
from composer_canonical import composer_canonical_json
values = json.load(sys.stdin)
print(json.dumps([composer_canonical_json(value) for value in values]))
`;
  const result = spawnSync("python3", ["-c", script], {
    cwd: COMPATIBILITY_ROOT,
    input: JSON.stringify(values),
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(
    values.map(composerCanonicalJson),
    JSON.parse(result.stdout)
  );
  assert.deepEqual(values.slice(0, 4).map(composerCanonicalJson), ["1", "0", "2", "100"]);
});

test("diagnosed large Atlas envelope yields only the short authoritative request", () => {
  const request =
    "A cinematic film still of a small spacecraft landing in a quiet desert at sunset.";
  assert.equal(request.length, 81);
  const signed = pythonSignedEnvelope({
    mode: "advanced",
    free_text: request,
    operation: "generate",
    visual_family: "cinematic-film-still",
    visual_subfamily: "feature-film-look",
    atlas_selection_mode: "manual_taxonomy",
    aspect_ratio: "16:9",
  });
  assert.ok(signed.length > 4000, `expected envelope >4000, got ${signed.length}`);
  assert.match(signed, /"confidence":1(?:[,}])/);
  assert.doesNotMatch(signed, /"confidence":1\.0(?:[,}])/);
  assert.equal(visibleComposerPrompt(signed), request);
  assert.equal(composerInvocationPrompt(signed), request);
  assert.equal(composerInvocationPrompt(signed).length, 81);
});

test("standard AnythingLLM history plugin is adapted rather than replaced", async () => {
  const calls = [];
  const plugin = withComposerVisibleHistory({
    _store: async (_aibitat, values) => calls.push(["store", values.prompt]),
    _storeSpecial: async (_aibitat, values) =>
      calls.push(["special", values.prompt]),
  });
  const signed = envelope();
  await plugin._store({}, { prompt: signed, response: "ok" });
  await plugin._storeSpecial({}, { prompt: signed, response: "ok" });
  assert.deepEqual(calls, [
    ["store", "שועל רודף אחרי חתול"],
    ["special", "שועל רודף אחרי חתול"],
  ]);
});

test("all native AnythingLLM persistence boundaries store only exact visible text", async () => {
  const calls = [];
  const WorkspaceChats = {
    async new(values) {
      calls.push(["new", values]);
      return { chat: { id: 17 } };
    },
    async upsert(id, values) {
      calls.push(["upsert", id, values]);
      return { chat: { id } };
    },
  };
  const signed = envelope();
  installComposerHistoryPersistence({ WorkspaceChats });
  installComposerHistoryPersistence({ WorkspaceChats });
  await WorkspaceChats.new({ prompt: signed, response: {} });
  await WorkspaceChats.upsert(17, {
    prompt: signed,
    response: { text: "ok" },
  });
  await WorkspaceChats.new({ prompt: "ordinary native chat", response: {} });
  assert.equal(calls.length, 3);
  assert.equal(calls[0][1].prompt, "שועל רודף אחרי חתול");
  assert.equal(calls[1][2].prompt, "שועל רודף אחרי חתול");
  assert.equal(calls[2][1].prompt, "ordinary native chat");
});

test("signed Composer references stay private to governed tools", () => {
  const signed = envelope();
  const privateAttachments = [
    { name: "reference.jpg", contentString: "data:image/jpeg;base64,AAAA" },
  ];
  assert.deepEqual(
    composerModelAttachments(signed, privateAttachments),
    [],
    "the language model must receive signed reference semantics without the binary"
  );
  assert.equal(
    composerRuntimeAttachments(signed, privateAttachments, []).at(0),
    privateAttachments[0],
    "the governed tool must retain the server-held reference"
  );
});

test("continued Composer turns bind the current message attachment, never stale invocation data", () => {
  const signed = envelope("Use the current reference");
  const staleInvocation = [
    { name: "stale.jpg", contentString: "data:image/jpeg;base64,OLD" },
  ];
  const currentMessage = [
    { name: "current.jpg", contentString: "data:image/jpeg;base64,NEW" },
  ];
  assert.equal(
    composerRuntimeAttachments(signed, staleInvocation, currentMessage, {
      allowInvocationFallback: false,
    }).at(0),
    currentMessage[0]
  );
  assert.deepEqual(
    composerRuntimeAttachments(signed, staleInvocation, [], {
      allowInvocationFallback: false,
    }),
    [],
    "a later signed turn without an upload must fail closed instead of reusing an old image"
  );
});

test("continued Composer binaries are stripped only from the model-facing history copy", () => {
  const signed = envelope("Use this uploaded person");
  const attachment = {
    name: "reference.jpg",
    contentString: "data:image/jpeg;base64,AAAA",
  };
  const nativeMessages = [
    { role: "user", content: signed, attachments: [attachment] },
    {
      role: "user",
      content: "ordinary vision message",
      attachments: [attachment],
    },
  ];
  const modelMessages = composerModelHistory(nativeMessages);
  assert.equal(modelMessages[0].attachments, undefined);
  assert.equal(modelMessages[1].attachments[0], attachment);
  assert.equal(
    nativeMessages[0].attachments[0],
    attachment,
    "AnythingLLM's native message must retain the attachment for persistence"
  );
});

test("ordinary native chat attachments keep AnythingLLM behavior", () => {
  const attachments = [
    { name: "ordinary.jpg", contentString: "data:image/jpeg;base64,AAAA" },
  ];
  assert.equal(
    composerModelAttachments("ordinary native chat", attachments).at(0),
    attachments[0]
  );
  assert.equal(
    composerRuntimeAttachments("ordinary native chat", [], attachments).at(0),
    attachments[0]
  );
});

test("native agent failure persists one normal visible row without a duplicate job", async () => {
  let onError;
  const writes = [];
  const signed = envelope();
  const aibitat = {
    trackedChatId: 17,
    _chats: [
      {
        from: "@user",
        content: signed,
        attachments: [
          { name: "reference.jpg", contentString: "data:image/jpeg;base64,AAAA" },
        ],
      },
    ],
    handlerProps: {
      invocation: { workspace_id: 10, user_id: 2, thread_id: 3 },
      log: () => {},
    },
    onError(callback) {
      onError = callback;
    },
  };
  installComposerFailurePersistence({
    aibitat,
    WorkspaceChats: {
      async upsert(id, values) {
        writes.push({ id, values });
      },
    },
  });
  await onError(new Error("Image runtime unavailable"));
  await onError(new Error("Image runtime unavailable"));
  assert.equal(writes.length, 1);
  assert.equal(writes[0].id, 17);
  assert.equal(writes[0].values.prompt, "שועל רודף אחרי חתול");
  assert.equal(writes[0].values.response.text, "Image runtime unavailable");
  assert.equal(
    writes[0].values.response.attachments[0].name,
    "reference.jpg",
    "native failed-turn history must retain the current attachment for reload"
  );
  assert.equal(writes[0].values.include, true);
});
