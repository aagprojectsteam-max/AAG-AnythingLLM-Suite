"use strict";
const fs = require("fs"), path = require("path");
function patchTokenUsage(checkout, overlay) {
  const root = path.join(checkout, "frontend/src/components/WorkspaceChat/ChatContainer/PromptInput");
  const input = path.join(root, "index.jsx");
  let text = fs.readFileSync(input, "utf8");
  const anchor = '            <ToolsMenu';
  if (text.split(anchor).length !== 2) throw Error("Token meter input anchor missing/ambiguous");
  text = 'import AagTokenUsage from "./AagTokenUsage";\n' + text;
  text = text.replace(anchor, '            <AagTokenUsage workspaceSlug={activeWorkspaceSlug} threadSlug={threadSlug} history={history} prompt={promptInput} systemPrompt={workspace?.openAiPrompt || ""} streaming={isStreaming || agentSessionActive} agent={agentSessionActive} hasAttachments={attachments.length > 0} />\n' + anchor);
  fs.writeFileSync(input, text);
  for (const name of ["AagTokenUsage.jsx", "aagTokenUsage.js", "aagTokenUsage.css"])
    fs.copyFileSync(path.join(overlay, name), path.join(root, name));
  // Tag websocket agent aggregates without changing the streaming contract.
  const agentPath = path.join(checkout, "frontend/src/utils/chat/agent.js");
  text = fs.readFileSync(agentPath, "utf8");
  const metric = '{ ...msg, metrics: data.content.metrics }';
  if (text.split(metric).length !== 2) throw Error("Token meter agent metrics anchor missing/ambiguous");
  fs.writeFileSync(agentPath, text.replace(metric, '{ ...msg, aagAgentUsage: true, metrics: data.content.metrics }'));
}
module.exports = { patchTokenUsage };
