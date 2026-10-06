import { useEffect, useRef, useState } from "react";
import { DEFAULT_WIDTH, MIN_WIDTH, sidebarMaximum, clampSidebarWidth, readSidebarWidth, saveSidebarWidth, sidebarKeyboardWidth } from "@/utils/aagSidebarWidth";
import "./aagSidebar.css";

export function useSidebarWidth() {
  const [preferred, setPreferred] = useState(() => {
    try { return readSidebarWidth(window.localStorage); }
    catch { return DEFAULT_WIDTH; }
  });
  const [maximum, setMaximum] = useState(() => sidebarMaximum(window.innerWidth));
  useEffect(() => {
    const resize = () => setMaximum(sidebarMaximum(window.innerWidth));
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  function change(value) {
    const next = clampSidebarWidth(value, maximum);
    setPreferred(next);
    try { saveSidebarWidth(window.localStorage, next); } catch { /* Private storage. */ }
  }
  // A smaller window temporarily clamps the layout, not the saved preference.
  return { width: clampSidebarWidth(preferred, maximum), maximum, change };
}

export default function AagSidebarResize({ width, maximum, change }) {
  const drag = useRef(null);
  const [dragging, setDragging] = useState(false);
  function end(event, cancel = false) {
    if (!drag.current || (event.pointerId != null && event.pointerId !== drag.current.id)) return;
    const start = drag.current;
    drag.current = null;
    setDragging(false);
    if (cancel) change(start.width);
    if (event.currentTarget.hasPointerCapture?.(start.id)) event.currentTarget.releasePointerCapture(start.id);
  }
  return <div
    className={`aag-sidebar-resizer${dragging ? " is-dragging" : ""}`}
    role="separator" aria-label="Resize sidebar" aria-orientation="vertical"
    aria-valuemin={MIN_WIDTH} aria-valuemax={maximum} aria-valuenow={width}
    aria-valuetext={`${width} pixels`} aria-controls="aag-desktop-sidebar"
    title="Drag to resize sidebar. Arrow keys adjust width; Enter resets."
    tabIndex={0} data-testid="aag-sidebar-resizer"
    onPointerDown={event => {
      if (event.button !== 0 || drag.current) return;
      event.preventDefault();
      event.currentTarget.focus();
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { id: event.pointerId, x: event.clientX, width };
      setDragging(true);
    }}
    onPointerMove={event => {
      if (drag.current?.id === event.pointerId) change(drag.current.width + event.clientX - drag.current.x);
    }}
    onPointerUp={event => end(event)}
    onPointerCancel={event => end(event, true)}
    onLostPointerCapture={event => end(event)}
    onDoubleClick={() => change(DEFAULT_WIDTH)}
    onKeyDown={event => {
      if (event.key === "Escape" && drag.current) { end(event, true); event.preventDefault(); return; }
      const next = sidebarKeyboardWidth(event.key, width, maximum, event.shiftKey);
      if (next !== null) { event.preventDefault(); change(next); }
    }}
  />;
}
