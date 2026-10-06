"use strict";
const fs = require("fs"), path = require("path");
function once(source, before, after) {
  if (source.split(before).length !== 2) throw Error("Sidebar patch anchor missing/ambiguous: " + before.slice(0, 90));
  return source.replace(before, after);
}
function patchSidebarPresentation(checkout) {
  const root = path.join(checkout, "frontend/src/components/Sidebar");
  const file = path.join(root, "index.jsx");
  let source = fs.readFileSync(file, "utf8");
  source = 'import AagSidebarResize, { useSidebarWidth } from "./AagSidebarResize";\n' + source;
  source = once(source, "export default function Sidebar() {", "export default function Sidebar() {\n  const sidebarWidth = useSidebarWidth();");
  source = once(source, 'width: showSidebar ? "292px" : "0px",', 'width: showSidebar ? `${sidebarWidth.width}px` : "0px",');
  source = once(source, 'className="relative transition-all duration-500"', 'id="aag-desktop-sidebar" data-testid="aag-sidebar"\n        className="relative aag-desktop-sidebar"');
  source = once(source, '        {canToggleSidebar && (', '        {showSidebar && <AagSidebarResize {...sidebarWidth} />}\n        {canToggleSidebar && (');
  source = once(source, 'flex w-[250px] min-w-[250px]', 'flex w-full min-w-0 mx-[22px] pr-[30px]');
  source = once(source, 'min-w-[250px] p-[10px]', 'min-w-0 p-[10px]');
  source = once(source, 'min-w-[235px] min-h-0', 'min-w-0 min-h-0');
  for (const id of ["workspace-name", "workspace-thread-name"]) {
    source = once(source, `id="${id}"`, `id="${id}"\n          openEvents={{ mouseover: true, focus: true }}\n          closeEvents={{ mouseout: true, blur: true }}\n          globalCloseEvents={{ escape: true }}`);
  }
  fs.writeFileSync(file, source);
  const toggle = path.join(root, "SidebarToggle/index.jsx");
  source = fs.readFileSync(toggle, "utf8");
  source = once(source, 'top-[18px] left-[248px]', 'top-[18px] right-[20px]');
  fs.writeFileSync(toggle, source);
  const item = path.join(root, "ActiveWorkspaces/ThreadContainer/ThreadItem/index.jsx");
  source = fs.readFileSync(item, "utf8");
  source = once(source, 'className="h-full"', 'className="h-full shrink-0"');
  source = once(source, 'flex w-full items-center justify-between pr-2 group/thread', 'flex flex-1 min-w-0 items-center justify-between pr-2 group/thread');
  source = once(source, 'className="w-full pl-2 py-1 overflow-hidden"', 'className="flex-1 min-w-0 pl-2 py-1 overflow-hidden"\n            aria-label={threadLabel(visibleOrdinal, thread.name)}');
  source = once(source, 'text-left text-sm truncate max-w-[150px]', 'aag-ordinal-label text-sm');
  source = once(source, '{threadLabel(visibleOrdinal, thread.name)}\n            </p>', '{visibleOrdinal && <span className="aag-ordinal-number" data-testid="aag-thread-number" dir="ltr">{visibleOrdinal} -</span>}\n              <bdi className="aag-ordinal-title" data-testid="aag-thread-title" dir="auto">{thread.name}</bdi>\n            </p>');
  source = once(source, 'ref={optionsContainer} className="flex items-center"', 'ref={optionsContainer} className="flex shrink-0 items-center"');
  fs.writeFileSync(item, source);

  const workspaces = path.join(root, "ActiveWorkspaces/index.jsx");
  source = fs.readFileSync(workspaces, "utf8");
  source = once(source, "  if (loading) {", `  useEffect(() => {
    const removeConfirmedWorkspace = event => {
      setWorkspaces(current => current.filter(workspace => workspace.slug !== event.detail.slug));
    };
    window.addEventListener("aag-workspace-deleted", removeConfirmedWorkspace);
    return () => window.removeEventListener("aag-workspace-deleted", removeConfirmedWorkspace);
  }, []);

  if (loading) {`);
  source = once(source, 'to={paths.workspace.chat(workspace.slug)}', `to={paths.workspace.chat(workspace.slug)}
                          data-testid="aag-workspace-link"
                          data-workspace-slug={workspace.slug}
                          data-workspace-ordinal={index + 1}
                          data-tooltip-id="workspace-name"
                          data-tooltip-content={(index + 1) + " - " + workspace.name}
                          aria-label={(index + 1) + " - " + workspace.name}`);
  source = once(source, 'flex flex-grow w-[75%]', 'flex flex-1 min-w-0');
  source = once(source, 'flex flex-row justify-between w-full items-center', 'flex flex-row justify-between w-full min-w-0 items-center');
  source = once(source, 'cursor-grab mr-[3px]', 'cursor-grab shrink-0 mr-[3px]');
  const start = source.indexOf('                            <div\n                              data-tooltip-id="workspace-name"');
  const end = source.indexOf('                            {user?.role !== "default"', start);
  if (start < 0 || end < 0) throw Error("Workspace title anchor missing");
  source = source.slice(0, start) + `                            <div className={"aag-ordinal-label flex-1 min-w-0 text-[14px] leading-loose " + (isActive ? "font-bold text-white light:text-blue-900" : "font-medium")}>
                              <span className="aag-ordinal-number" data-testid="aag-workspace-number" dir="ltr">{index + 1} -</span>
                              <bdi className="aag-ordinal-title" data-testid="aag-workspace-title" dir="auto">{workspace.name}</bdi>
                            </div>
` + source.slice(end);
  source = once(source, 'flex items-center gap-x-[2px] transition-opacity', 'flex shrink-0 items-center gap-x-[2px] transition-opacity');
  fs.writeFileSync(workspaces, source);

  // Native delete requests and stable slugs are unchanged. Only confirmed
  // deletion removes a presentation row, before the existing navigation.
  const model = path.join(checkout, "frontend/src/models/workspace.js");
  source = fs.readFileSync(model, "utf8");
  const deleteStart = source.indexOf("  delete: async function (slug) {");
  const deleteEnd = source.indexOf("  wipeVectorDb:", deleteStart);
  if (deleteStart < 0 || deleteEnd < 0) throw Error("Workspace delete anchor missing");
  const deletion = once(source.slice(deleteStart, deleteEnd), "    return result;", '    if (result) window.dispatchEvent(new CustomEvent("aag-workspace-deleted", { detail: { slug } }));\n    return result;');
  fs.writeFileSync(model, source.slice(0, deleteStart) + deletion + source.slice(deleteEnd));
}
module.exports = { patchSidebarPresentation };
