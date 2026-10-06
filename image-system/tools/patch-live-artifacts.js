"use strict";
// Applied to the pinned upstream reducer by the frontend builder and exercised
// with that same reducer in the cross-boundary regression test.
function patchLiveArtifacts(source) {
  const replace = (before, after) => {
    if (source.split(before).length !== 2) throw new Error("Live artifact patch anchor missing or ambiguous");
    source = source.replace(before, after);
  };
  replace('import { v4 } from "uuid";', 'import { v4 } from "uuid";\nimport { hasImageOutputs, hasMessageContent, mergeImageMessage } from "@/utils/aagLiveArtifacts";');
  replace('  // toolApprovalRequest doesn\'t have content field, so check separately',
    '  // An artifact-only reply has valid content in outputs, with no prose.\n  // Handle it before the upstream text-content guard.\n  if (data.type === "imageGenerationCard" && hasImageOutputs(data)) {\n    return setChatHistory(prev => mergeImageMessage(prev, data, v4()));\n  }\n\n  // toolApprovalRequest doesn\'t have content field, so check separately');
  source = source.replaceAll('prev.filter((msg) => !!msg.content)', 'prev.filter(hasMessageContent)')
    .replaceAll('msg?.content\n                  ? msg', 'hasMessageContent(msg)\n                  ? msg');
  return source;
}
module.exports = { patchLiveArtifacts };
