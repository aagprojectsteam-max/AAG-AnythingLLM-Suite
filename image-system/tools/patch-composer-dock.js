"use strict";
const fs = require("fs"), path = require("path");
function once(source, before, after) {
  if (source.split(before).length !== 2) throw Error("Composer layout anchor missing/ambiguous: " + before.slice(0, 80));
  return source.replace(before, after);
}
function patchComposerDock(checkout) {
  const root = path.join(checkout, "frontend/src/components/WorkspaceChat/ChatContainer");
  const input = path.join(root, "PromptInput/index.jsx");
  let source = fs.readFileSync(input, "utf8");
  source = 'import AagComposerDock from "./AagComposerDock";\nimport useAagComposerDraft from "@/utils/useAagComposerDraft";\n' + source;
  source = once(source, '  const [composerMode, setComposerMode] = useState("auto");\n  const activeWorkspaceSlug = workspace?.slug || workspaceSlug;', '  const activeWorkspaceSlug = workspace?.slug || workspaceSlug;\n  const [composerMode, setComposerMode] = useAagComposerDraft(activeWorkspaceSlug, threadSlug, "mode", "auto");');
  source = once(source, '    <div\n      id="prompt-input-wrapper"', '    <AagComposerDock\n      enabled={isImageComposerEnabledForCurrentWorkspace || activeWorkspaceSlug === "image-generator"}\n      workspaceSlug={activeWorkspaceSlug} threadSlug={threadSlug}');
  source = once(source, '      </form>\n    </div>', '      </form>\n    </AagComposerDock>');
  source = once(source, '    setPromptInput,\n  });', '    setPromptInput,\n    workspaceSlug: activeWorkspaceSlug,\n    threadSlug,\n  });');
  source = once(source, '<div className="relative w-[95vw] md:w-[750px]">', '<div data-aag-native-input-stack="true" className="relative w-[95vw] md:w-[750px]">');
  source = once(source, '<div className="bg-zinc-800 light:bg-white light:border', '<div data-aag-native-editor="true" className="bg-zinc-800 light:bg-white light:border');
  fs.writeFileSync(input, source);
  // Upstream retains the previous loaded conversation while fetching the next.
  // Its route-based draft hook can otherwise write the old draft under the new
  // URL. Keep the existing storage format and debounce, using the displayed
  // native component's stable scope, just like its existing submit handler.
  const hook = path.join(checkout, "frontend/src/hooks/usePromptInputStorage.js");
  source = fs.readFileSync(hook, "utf8");
  source = once(source, 'import { useParams } from "react-router-dom";\n', '');
  source = once(source, 'export default function usePromptInputStorage({ promptInput, setPromptInput }) {\n  const { threadSlug = null, slug: workspaceSlug } = useParams();', 'export default function usePromptInputStorage({ promptInput, setPromptInput, workspaceSlug, threadSlug = null }) {');
  fs.writeFileSync(hook, source);
  const chat = path.join(root, "index.jsx");
  source = fs.readFileSync(chat, "utf8");
  const layout = 'className="relative flex md:ml-[2px] md:mr-[16px] md:my-[16px] w-full h-full z-[2]"';
  if (source.split(layout).length !== 3) throw Error("Expected both native chat layout branches");
  source = source.replaceAll(layout, 'data-aag-chat-layout="true" ' + layout);
  const surface = '<div className="flex-1 min-w-0 relative md:rounded-[16px]';
  if (source.split(surface).length !== 3) throw Error("Expected both native chat surfaces");
  source = source.replaceAll(surface, '<div data-aag-chat-surface="true" className="flex-1 min-w-0 relative md:rounded-[16px]');
  // Keep chat settings attached to the chat, not the combined chat/panel root.
  const menu = /(\s*<ChatSettingsMenu\s+history=\{chatHistory\}\s+workspace=\{workspace\}\s+threadSlug=\{activeThreadSlug\}\s*\/>)(\s*<div data-aag-chat-surface="true"[^>]+>)/g;
  if ([...source.matchAll(menu)].length !== 2) throw Error("Expected two native chat settings menus");
  source = source.replace(menu, '$2$1');
  fs.writeFileSync(chat, source);
}
module.exports = { patchComposerDock };
