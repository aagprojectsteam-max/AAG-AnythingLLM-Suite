import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useParams } from "react-router-dom";
import { SidebarSimple, X, ArrowSquareOut } from "@phosphor-icons/react";
import { readLayout, saveLayout, normalizeLayout, panelGeometry, panelKeyboardWidth, PANEL_MIN } from "@/utils/aagComposerLayout";
import "./aagComposerDock.css";

// A stable portal target owns the original native form and Composer. Moving
// that DOM node changes placement, never React identity or the submission path.
export default function AagComposerDock({ children, className, enabled, workspaceSlug, threadSlug }) {
  const outlet = useRef(null), aside = useRef(null), frame = useRef(0), drag = useRef(null);
  const [host] = useState(() => document.createElement("div"));
  const [layout, setLayout] = useState(() => readLayout(window.localStorage));
  const current = useRef(layout); current.current = layout;
  const [available, setAvailable] = useState(0);
  const route = useParams();
  const scopeReady = (!route.slug || route.slug === workspaceSlug) &&
    (route.threadSlug || null) === (threadSlug || null);
  const geometry = panelGeometry(layout, available);
  const docked = enabled && layout.mode === "panel" && layout.open && geometry.fits;
  const closed = enabled && layout.mode === "panel" && !layout.open;
  const constrained = enabled && layout.mode === "panel" && layout.open && !geometry.fits;

  function update(patch, persist = true) {
    const next = normalizeLayout({ ...current.current, ...patch });
    current.current = next;
    setLayout(next);
    if (persist) saveLayout(window.localStorage, next);
  }

  useLayoutEffect(() => {
    const root = outlet.current?.closest("[data-aag-chat-layout]");
    outlet.current.appendChild(host);
    if (!root) return;
    const panel = document.createElement("aside");
    panel.className = "aag-composer-dock-slot";
    panel.setAttribute("aria-label", "Image Composer side panel");
    panel.dataset.testid = "aag-composer-panel";
    panel.hidden = true;
    root.appendChild(panel); aside.current = panel;
    const measure = () => {
      let occupied = 0;
      for (const child of root.children) {
        if (child === panel || child.hasAttribute("data-aag-chat-surface")) continue;
        const css = getComputedStyle(child);
        if (css.position !== "absolute" && css.position !== "fixed") occupied += child.getBoundingClientRect().width;
      }
      const width = Math.max(0, root.clientWidth - occupied);
      setAvailable(previous => previous === width ? previous : width);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    for (const child of root.children) if (child !== panel && !child.hasAttribute("data-aag-chat-surface")) observer.observe(child);
    measure();
    return () => {
      observer.disconnect(); cancelAnimationFrame(frame.current);
      // Only remove the layout node owned by this instance.
      outlet.current?.appendChild(host); panel.remove(); aside.current = null;
      root.removeAttribute("data-aag-composer-docked");
    };
  }, [host]);

  useLayoutEffect(() => {
    const panel = aside.current, root = outlet.current?.closest("[data-aag-chat-layout]");
    if (!panel || !root) return;
    const target = docked ? panel : outlet.current;
    const focused = host.contains(document.activeElement) ? document.activeElement : null;
    if (host.parentNode !== target) target.appendChild(host);
    panel.hidden = !docked;
    panel.dataset.side = layout.side;
    panel.style.width = `${geometry.width}px`;
    root.dataset.aagComposerDocked = docked ? layout.side : "false";
    if (focused && focused.getClientRects().length) focused.focus({ preventScroll: true });
  }, [docked, layout.side, geometry.width, host]);

  useEffect(() => {
    function changed(event) {
      if (event.key === "aag.image-composer.layout.v1") {
        const next = readLayout(window.localStorage); current.current = next; setLayout(next);
      }
    }
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, []);

  function finishDrag(event) {
    if (!drag.current || (event.pointerId !== undefined && event.pointerId !== drag.current.id)) return;
    cancelAnimationFrame(frame.current);
    if (drag.current.next !== undefined) update({ width: drag.current.next }, false);
    drag.current = null;
    saveLayout(window.localStorage, current.current);
  }
  function resizeKey(event) {
    const next = panelKeyboardWidth(event.key, geometry.width, geometry.maximum, layout.side, event.shiftKey);
    if (next === null) return;
    event.preventDefault(); update({ width: next });
  }
  function closePanel() {
    update({ open: false });
    requestAnimationFrame(() => host.querySelector('[data-testid="aag-composer-reopen"]')?.focus());
  }
  function reopen() {
    update({ open: true });
    requestAnimationFrame(() => host.querySelector('[data-testid="aag-composer-close"]')?.focus());
  }

  return <div id="prompt-input-wrapper" ref={outlet}
    className={`${className} aag-composer-outlet`} data-docked={docked}>
    {createPortal(<div className="aag-composer-presentation" data-placement={docked ? "panel" : "popup"}
      data-closed={closed} data-side={layout.side} data-constrained={constrained} data-scope-ready={scopeReady}
      data-workspace-scope={workspaceSlug} data-thread-scope={threadSlug || ""}
      onSubmitCapture={event => { if (!scopeReady) { event.preventDefault(); event.stopPropagation(); } }}>
      <div className="aag-composer-scroll">
      {enabled && (closed ? <div className="aag-composer-launcher-row">
        <button type="button" data-testid="aag-composer-reopen" onClick={reopen}
          title="Open Image Composer" aria-label="Open Image Composer" aria-expanded="false">
          <SidebarSimple size={16} /> Image Composer
        </button>
      </div> : <div className="aag-composer-layout-bar" dir="ltr" aria-label="Image Composer layout">
        {docked && <label>Panel side<select aria-label="Image Composer panel side"
          data-testid="aag-composer-side" value={layout.side} onChange={event => update({ side: event.target.value })}>
          <option value="left">Left</option><option value="right">Right</option>
        </select></label>}
        <span className="aag-composer-layout-spacer" />
        <div className="aag-composer-layout-actions">
        <button type="button" data-testid="aag-composer-dock-toggle"
          aria-pressed={layout.mode === "panel"}
          title={layout.mode === "panel" ? "Undock Image Composer" : "Dock Image Composer"}
          aria-label={layout.mode === "panel" ? "Undock Image Composer" : "Dock Image Composer"}
          onClick={() => update({ mode: layout.mode === "panel" ? "popup" : "panel", open: true })}>
          {layout.mode === "panel" ? <ArrowSquareOut size={18} /> : <SidebarSimple size={18} />}
        </button>
        {layout.mode === "panel" && <button type="button" data-testid="aag-composer-close"
          title="Close Image Composer panel" aria-label="Close Image Composer panel" onClick={closePanel}><X size={18} /></button>}
        </div>
      </div>)}
      {constrained && <p className="aag-composer-layout-note" role="status">Side panel needs more room. Showing Popup; your panel preferences are saved.</p>}
      <div className="aag-composer-native-form" inert={!scopeReady ? "" : undefined}>{children}</div>
      </div>
      {docked && <div role="separator" tabIndex={0} aria-label="Resize Image Composer panel" aria-orientation="vertical"
        aria-valuemin={PANEL_MIN} aria-valuemax={geometry.maximum} aria-valuenow={geometry.width}
        aria-valuetext={`${geometry.width} pixels`} title="Resize panel · arrow keys, Home, End; Enter resets width"
        data-testid="aag-composer-resizer" className="aag-composer-resizer" onKeyDown={resizeKey}
        onPointerDown={event => {
          if (event.button !== 0) return;
          event.preventDefault(); event.currentTarget.focus(); event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { id: event.pointerId, x: event.clientX, width: geometry.width };
        }} onPointerMove={event => {
          if (!drag.current || drag.current.id !== event.pointerId) return;
          drag.current.next = Math.max(PANEL_MIN, Math.min(geometry.maximum,
            drag.current.width + (event.clientX - drag.current.x) * (layout.side === "left" ? 1 : -1)));
          cancelAnimationFrame(frame.current);
          frame.current = requestAnimationFrame(() => { if (drag.current) update({ width: drag.current.next }, false); });
        }} onPointerUp={finishDrag} onPointerCancel={finishDrag} onLostPointerCapture={finishDrag} />}
    </div>, host)}
  </div>;
}
