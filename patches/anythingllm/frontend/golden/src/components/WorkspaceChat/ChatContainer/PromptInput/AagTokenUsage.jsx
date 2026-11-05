import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { API_BASE } from "@/utils/constants";
import { baseHeaders } from "@/utils/request";
import { SAVE_LLM_SELECTOR_EVENT } from "./LLMSelector/action";
import { usageView } from "./aagTokenUsage";
import "./aagTokenUsage.css";

export default function AagTokenUsage({ workspaceSlug, threadSlug, history, prompt,
  systemPrompt, streaming, agent, hasAttachments }) {
  const { i18n } = useTranslation();
  const he = /^(he|iw)(-|$)/i.test(i18n.resolvedLanguage || i18n.language || "");
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(prompt);
  useEffect(() => {
    const timer = setTimeout(() => setDraft(prompt), 180);
    return () => clearTimeout(timer);
  }, [prompt]);
  useEffect(() => {
    let alive = true, controller = null, timer = null;
    async function refresh() {
      controller?.abort();
      controller = new AbortController();
      const current = controller;
      setMeta(null); setLoading(true);
      clearTimeout(timer);
      timer = setTimeout(() => current.abort(), 8000);
      try {
        const response = await fetch(API_BASE + "/aag-usage/" + encodeURIComponent(workspaceSlug), {
          headers: baseHeaders(), signal: current.signal, cache: "no-store"
        });
        if (!response.ok) throw Error("metadata unavailable");
        const data = await response.json();
        if (alive && controller === current) setMeta({ ...data, slug: workspaceSlug });
      } catch {
        if (alive && controller === current) setMeta(null);
      } finally {
        if (alive && controller === current) { clearTimeout(timer); setLoading(false); }
      }
    }
    if (workspaceSlug) refresh(); else setLoading(false);
    const onFocus = () => { if (document.visibilityState === "visible" && workspaceSlug) refresh(); };
    window.addEventListener(SAVE_LLM_SELECTOR_EVENT, refresh);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      alive = false; clearTimeout(timer); controller?.abort();
      window.removeEventListener(SAVE_LLM_SELECTOR_EVENT, refresh);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [workspaceSlug]);
  useEffect(() => { setDraft(prompt); setOpen(false); }, [workspaceSlug, threadSlug]);
  const metadata = meta?.slug === workspaceSlug ? meta : null;
  const view = useMemo(() => usageView({ history, prompt: draft, systemPrompt, metadata,
    streaming, agent, hasAttachments }), [history, draft, systemPrompt, metadata, streaming, agent, hasAttachments]);
  if (!workspaceSlug) return null;
  const s = (en, hebrew) => he ? hebrew : en;
  const n = value => value === null || value === undefined ? "—" : value.toLocaleString(he ? "he-IL" : "en-US");
  const { context, output } = view;
  const progress = (used, max, label) => max && used !== null ? (
    <div className={"aag-token-track " + (used >= max ? "full" : used / max >= 0.8 ? "near" : "")}
      role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={max}
      aria-valuenow={Math.min(max, used)} aria-valuetext={s("Approximately ", "בערך ") + n(used) + " / " + n(max)}>
      <span style={{ width: Math.min(100, used / max * 100) + "%" }} />
    </div>
  ) : null;
  return (
    <section className="aag-token-usage" dir={he ? "rtl" : "ltr"} aria-label={s("Token usage", "ניצול טוקנים")} data-testid="aag-token-usage">
      <div className="aag-token-heading"><strong>{s("Tokens", "טוקנים")}</strong>
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}>
          {s("Estimates · details", "הערכות · פרטים")} {open ? "▴" : "▾"}
        </button>
      </div>
      <div className="aag-token-grid">
        <div className="aag-token-cell">
          <span>{s("Conversation context", "מקום בשיחה")}</span>
          <b>{loading ? s("Loading…", "טוען…") : context.limit === null ?
            s("Limit unavailable", "המגבלה לא זמינה") :
            s("≈ " + n(context.remaining) + " left", "נותרו כ־" + n(context.remaining))}</b>
          {progress(context.used, context.limit, s("Estimated context usage", "הערכת ניצול חלון ההקשר"))}
          <small>{s("≈ ", "כ־")}<bdi>{n(context.used)} / {n(context.limit)}</bdi>{s(" used", " בשימוש")}</small>
        </div>
        <div className="aag-token-cell">
          <span>{streaming ? s("Current response", "תשובה נוכחית") : s("Latest response", "תשובה אחרונה")}</span>
          <b>{output.used === null ? (streaming ? s("Waiting for data…", "ממתין לנתונים…") : s("Usage not reported yet", "טרם דווח שימוש")) :
            (output.estimated ? "≈ " : "") + n(output.used) + s(" used", " נוצלו")}</b>
          {progress(output.used, output.limit, s("Response token usage", "ניצול טוקנים לתשובה"))}
          <small>{output.remaining !== null ? s("Up to ≈ " + n(output.remaining) + " more", "עד כ־" + n(output.remaining) + " נוספים") :
            s("Remaining budget unavailable", "יתרת התקציב לא דווחה")}</small>
        </div>
        <div className="aag-token-cell">
          <span>{s("Provider allowance", "מכסת ספק")}</span>
          <b>{s("Not reported", "לא מדווחת")}</b>
          <small>{s("Usage is not account balance", "נתוני שימוש אינם יתרת חשבון")}</small>
        </div>
      </div>
      {open && <div className="aag-token-details">
        <div><bdi>{metadata?.model || s("Model metadata unavailable", "נתוני המודל אינם זמינים")}</bdi></div>
        <p>{s("Context is an estimate, not a hard limit check. It uses visible text and, when comparable, the last request's metrics. Retrieved documents, images, tools, hidden reasoning and history trimming can change the actual count.",
          "המקום בשיחה הוא הערכה, ולא בדיקת מגבלה. החישוב מבוסס על טקסט גלוי ועל נתוני הבקשה האחרונה כשאפשר להשוות. מסמכים שנשלפו, תמונות, כלים, חשיבה פנימית וקיצור היסטוריה יכולים לשנות את המספר בפועל.")}</p>
        <p>{s("The limit is the application's configured/cached context window. A local server may run with a different limit. Streaming output is estimated from received text; final usage comes from AnythingLLM metrics, which may also be estimated. Agent totals may cover several model calls.",
          "המגבלה נלקחת מהגדרות ומנתוני המודל שבמערכת; שרת מקומי עשוי לפעול עם מגבלה אחרת. בזמן כתיבה נספרת הערכת הטקסט שהתקבל. בסיום מוצגים נתוני AnythingLLM, שעשויים גם הם להיות הערכה. נתוני סוכן עשויים לכלול כמה קריאות למודל.")}</p>
        <p>{s("Response remaining is only shown for a known configured request cap. It is not a prediction of response length. Provider credit, daily quotas and reset times are not exposed by the connected chat interfaces; no balance is inferred.",
          "יתרה לתשובה מוצגת רק כשמוכרת תקרת הבקשה המוגדרת; זו אינה תחזית לאורך התשובה. ממשקי השיחה המחוברים אינם מספקים יתרת אשראי, מכסה יומית או זמן איפוס, ולכן לא מחושבת יתרה משוערת.")}</p>
        {view.lastInput !== null && <p>{s("Latest recorded input: ", "טוקני קלט שנרשמו לאחרונה: ")}<bdi>{n(view.lastInput)}</bdi></p>}
        {context.over && <p className="aag-token-warning">{s("The estimate exceeds the configured window. Some history may be shortened by the application.", "ההערכה חורגת מהחלון המוגדר. המערכת עשויה לקצר חלק מהיסטוריית השיחה.")}</p>}
      </div>}
    </section>
  );
}
