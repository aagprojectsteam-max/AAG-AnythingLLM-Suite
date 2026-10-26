"use strict";
const fs = require('fs'), path = require('path');
function once(source, before, after) {
  if (source.split(before).length !== 2) throw Error('New thread lifecycle anchor missing/ambiguous: ' + before.slice(0, 90));
  return source.replace(before, after);
}
function patchNewThreadComposer(checkout) {
  const chatPath = path.join(checkout, 'frontend/src/components/WorkspaceChat/ChatContainer/index.jsx');
  let source = fs.readFileSync(chatPath, 'utf8');
  source = 'import { composerDrafts, aagNativeSubmission } from "@/utils/aagComposerDraftStore";\n' + source;
  source = once(source, 'let aagPendingNativeSubmission = null;', '// Shared with the native Home → first-thread handoff.');
  source = source.replaceAll('aagPendingNativeSubmission', 'aagNativeSubmission.current');
  const transition = '      if (thread) {\n        aagNativeSubmission.current = {';
  if (source.split(transition).length !== 3) throw Error('Expected both bare-workspace submission paths');
  source = source.replaceAll(transition, '      if (thread) {\n        composerDrafts.handoff(workspace.slug, thread.slug);\n        aagNativeSubmission.current = {');
  fs.writeFileSync(chatPath, source);

  const homePath = path.join(checkout, 'frontend/src/pages/Main/Home/index.jsx');
  source = fs.readFileSync(homePath, 'utf8');
  source = 'import { composerDrafts, aagNativeSubmission } from "@/utils/aagComposerDraftStore";\nimport { AagComposerDraftScope } from "@/utils/useAagComposerDraft";\nimport { clearPromptInputDraft } from "@/hooks/usePromptInputStorage";\n' + source;
  source = once(source, '  const [workspace, setWorkspace] = useState(null);', '  const [workspace, setWorkspace] = useState(null);\n  const [composerDraftId] = useState(() => "home:" + crypto.randomUUID());');
  // Attachment-created threads also replace Home's provider before the first
  // message. Transfer before that remount, using the same stable slug.
  source = once(source, '      if (thread) setThreadSlug(thread.slug);\n    }', '      if (thread) { composerDrafts.handoff(ws.slug, thread.slug, composerDraftId); setThreadSlug(thread.slug); }\n    }');
  source = once(source, '    const { thread } = await Workspace.threads.new(ws.slug);\n    if (thread) setThreadSlug(thread.slug);', '    const { thread } = await Workspace.threads.new(ws.slug);\n    if (thread) { composerDrafts.handoff(ws.slug, thread.slug, composerDraftId); setThreadSlug(thread.slug); }');
  source = once(source, '    const { thread } = await Workspace.threads.new(workspace.slug);\n    if (thread) setThreadSlug(thread.slug);', '    const { thread } = await Workspace.threads.new(workspace.slug);\n    if (thread) { composerDrafts.handoff(workspace.slug, thread.slug, composerDraftId); setThreadSlug(thread.slug); }');
  source = source.replaceAll('<HomeContent\n', '<HomeContent\n          composerDraftId={composerDraftId}\n');
  source = once(source, 'function HomeContent({ workspace, setWorkspace, threadSlug, setThreadSlug })', 'function HomeContent({ workspace, setWorkspace, threadSlug, setThreadSlug, composerDraftId })');
  source = once(source, '  async function submitMessage(message, attachments = []) {', '  async function submitMessage(message, attachments = [], modelMessage = message) {');
  // Do not replace Home's provider mid-submit. Navigate only after handing off.
  source = once(source, '        if (thread) setThreadSlug(thread.slug);', '        if (thread) composerDrafts.handoff(targetWorkspace.slug, thread.slug, composerDraftId);');
  source = once(source, '      sessionStorage.setItem(\n        PENDING_HOME_MESSAGE,\n        JSON.stringify({ message, attachments })\n      );', `      clearPromptInputDraft(threadSlug ?? targetWorkspace.slug);
      aagNativeSubmission.current = { message, modelMessage, attachments, hasInMemoryAttachments: attachments.length > 0 };
      sessionStorage.setItem(
        PENDING_HOME_MESSAGE,
        JSON.stringify({ message, modelMessage, hasInMemoryAttachments: attachments.length > 0 })
      );`);
  source = once(source, '  async function handleSubmit(e) {', '  async function handleSubmit(e, submission = {}) {');
  source = once(source, '    await submitMessage(currentMessage, parseAttachments());', `    await submitMessage(currentMessage,
      [...(submission.composerAttachments || []), ...parseAttachments()],
      submission.modelMessage || currentMessage);`);
  source = once(source, '        className="relative flex md:ml-[2px] md:mr-[16px] md:my-[16px] w-full h-full z-[2]"', '        data-aag-chat-layout="true" className="relative flex md:ml-[2px] md:mr-[16px] md:my-[16px] w-full h-full z-[2]"');
  source = once(source, '        <ChatSettingsMenu />\n        <div className="flex-1 min-w-0 transition-all duration-500 relative', '        <div data-aag-chat-surface="true" className="flex-1 min-w-0 relative');
  source = once(source, '          <WorkspaceModelPicker workspaceSlug={workspace?.slug} />', '          <header data-testid="aag-chat-header" className="aag-chat-header"><WorkspaceModelPicker workspaceSlug={workspace?.slug} /><ChatSettingsMenu /></header>');
  source = once(source, '                <PromptInput\n', '                <AagComposerDraftScope value={composerDraftId}><PromptInput\n');
  source = once(source, '                  threadSlug={threadSlug}\n                />', '                  threadSlug={threadSlug}\n                /></AagComposerDraftScope>');
  fs.writeFileSync(homePath, source);
}
module.exports = { patchNewThreadComposer };
