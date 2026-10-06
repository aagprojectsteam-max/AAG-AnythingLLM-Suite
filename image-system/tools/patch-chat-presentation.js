"use strict";
const fs = require("fs"), path = require("path");
function once(source, before, after) {
  if (source.split(before).length !== 2) throw Error("Chat presentation anchor missing/ambiguous: " + before.slice(0, 80));
  return source.replace(before, after);
}
function patchChatPresentation(checkout) {
  const root = path.join(checkout, "frontend/src/components/WorkspaceChat/ChatContainer");
  const edit = (name, transform) => {
    const file = path.join(root, name);
    fs.writeFileSync(file, transform(fs.readFileSync(file, "utf8")));
  };
  edit("index.jsx", source => {
    // The native model picker remains the only owner of model resolution and
    // selection. Both native chat branches give it the same non-scrolling row.
    const header = /(<ChatSettingsMenu\s+history=\{chatHistory\}\s+workspace=\{workspace\}\s+threadSlug=\{activeThreadSlug\}\s*\/>)\s*(\{isMobile && <SidebarMobileHeader \/>\})\s*(<WorkspaceModelPicker workspaceSlug=\{workspace.slug\} \/>)/g;
    if ([...source.matchAll(header)].length !== 2) throw Error("Expected both native chat headers");
    return source.replace(header, '$2\n<header data-testid="aag-chat-header" className="aag-chat-header">$3$1</header>');
  });
  edit("DnDWrapper/index.jsx", source => once(source,
    '      className={`relative flex flex-col h-full w-full md:mt-0 mt-[40px] p-[1px]`}',
    '      data-aag-chat-body="true"\n      className={`relative flex flex-col h-full w-full md:mt-0 mt-[40px] p-[1px]`}'));
  edit("ChatHistory/index.jsx", source => once(source,
    '<div className="w-full max-w-[750px]">',
    '<div data-aag-message-column="true" className="w-full max-w-[750px]">'));
  edit("ChatSettingsMenu/index.jsx", source => once(source,
    '      className={`absolute top-3 md:top-5 z-30 ${hasUserIcon ? "right-[55px] md:right-[67px]" : "right-4 md:right-6"}`}',
    '      data-testid="aag-chat-settings"\n      className={`aag-chat-header-settings ${hasUserIcon ? "aag-reserve-user-icon" : ""}`}'));
  edit("WorkspaceModelPicker/index.jsx", source => {
    source = once(source, 'import { useState, useEffect } from "react";', 'import { useState, useEffect, useId } from "react";');
    source = once(source, 'import { SIDEBAR_TOGGLE_EVENT } from "@/components/Sidebar/SidebarToggle";\n', '');
    const obsolete = /  const \[sidebarOpen, setSidebarOpen\] = useState\([\s\S]*?  \/\/ Fetch current model name for display/;
    if ([...source.matchAll(new RegExp(obsolete.source, "g"))].length !== 1) throw Error("Expected native model sidebar-position effect");
    source = source.replace(obsolete, '  const modelTooltipId = useId();\n  const modelLabel = modelName || t("chat_window.select_model");\n\n  // Fetch current model name for display');
    source = once(source, '        className={`hidden md:block absolute top-2 z-30 transition-all duration-500 ${\n          sidebarOpen ? "left-3" : "left-11"\n        }`}',
      '        className="aag-chat-model-picker" data-testid="aag-chat-model-picker"');
    source = once(source, '          onClick={() => setShowSelector(!showSelector)}',
      '          onClick={() => setShowSelector(!showSelector)}\n          data-testid="aag-chat-model-button"\n          aria-label={modelLabel}\n          aria-describedby={modelTooltipId}\n          aria-expanded={showSelector}\n          title={modelLabel}');
    source = once(source, '          <span\n            className={`text-xs ${\n              showSelector\n                ? "text-white light:text-slate-800"\n                : "text-zinc-500 light:text-slate-500 group-hover:text-white light:group-hover:text-slate-800"\n            }`}\n          >\n            {modelName || t("chat_window.select_model")}\n          </span>',
      '          <bdi dir="auto" data-testid="aag-chat-model-label" className="aag-chat-model-label">{modelLabel}</bdi>');
    source = once(source, '        </button>\n\n        {showSelector && (',
      '        </button>\n        {!showSelector && <span id={modelTooltipId} role="tooltip" className="aag-chat-model-tooltip" dir="auto">{modelLabel}</span>}\n\n        {showSelector && (');
    source = once(source, 'className="absolute left-0 top-full mt-1 bg-zinc-800 light:bg-white border border-zinc-700 light:border-slate-300 rounded-xl shadow-lg w-[620px] overflow-hidden"',
      'className="aag-chat-model-menu absolute left-0 top-full mt-1 bg-zinc-800 light:bg-white border border-zinc-700 light:border-slate-300 rounded-xl shadow-lg overflow-hidden"');
    return source;
  });
}
module.exports = { patchChatPresentation };
