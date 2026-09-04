import { useEffect, useMemo, useState } from "react";
import { API_BASE } from "@/utils/constants";
import { baseHeaders } from "@/utils/request";
import {
  DEFAULT_UI_LANGUAGE,
  UI_LANGUAGE_KEY,
  loadUiLanguage,
} from "../AagImageComposerPanel/localization";
import "./styles.css";

const LABELS = Object.freeze({
  en: {
    title: "Creating your image",
    requestReceived: "Request received",
    preparingInstructions: "Preparing image instructions",
    workflowStarted: "Image workflow started",
    creatingImage: "Creating image",
    processingResult: "Processing result",
    returningToChat: "Returning image to chat",
    complete: "Complete",
    generationStalled: "Image generation stopped progressing",
    recoveringEngine: "Recovering image engine...",
    imageGenerationFailed: "Image generation failed",
    engineRecovered: "Image engine recovered",
    engineRecoveryRequired: "Image engine recovery is required",
    elapsed: "Elapsed",
    details: "Details",
    errors: {
      ENGINE_STALLED_RECOVERED:
        "Image generation stopped responding. The image engine was safely recovered. You can try again.",
      ENGINE_INTERRUPT_FAILED:
        "Image generation stopped progressing, but a safe engine interrupt could not be completed.",
      ENGINE_SERVICE_RECOVERY_REQUIRED:
        "Image generation stopped responding and the image engine requires controlled recovery before another request.",
      ENGINE_DEVICE_HANG:
        "The image device stopped responding and requires controlled recovery.",
    },
  },
  he: {
    title: "יוצר את התמונה שלך",
    requestReceived: "הבקשה התקבלה",
    preparingInstructions: "מכין את הוראות התמונה",
    workflowStarted: "מנוע התמונה הופעל",
    creatingImage: "יוצר את התמונה",
    processingResult: "מעבד את התוצאה",
    returningToChat: "מחזיר את התמונה לצ׳אט",
    complete: "הושלם",
    generationStalled: "יצירת התמונה הפסיקה להתקדם",
    recoveringEngine: "משחרר את מנוע התמונה...",
    imageGenerationFailed: "יצירת התמונה נכשלה",
    engineRecovered: "מנוע התמונה שוחרר בבטחה",
    engineRecoveryRequired: "נדרש שחזור מבוקר של מנוע התמונה",
    elapsed: "זמן שחלף",
    details: "פרטים",
    errors: {
      ENGINE_STALLED_RECOVERED:
        "יצירת התמונה הפסיקה להתקדם. מנוע התמונה שוחרר בבטחה. אפשר לנסות שוב.",
      ENGINE_INTERRUPT_FAILED:
        "יצירת התמונה הפסיקה להתקדם, אך לא ניתן היה להשלים עצירה בטוחה של המנוע.",
      ENGINE_SERVICE_RECOVERY_REQUIRED:
        "יצירת התמונה הפסיקה להגיב ונדרש שחזור מבוקר של מנוע התמונה לפני בקשה נוספת.",
      ENGINE_DEVICE_HANG:
        "התקן התמונה הפסיק להגיב ונדרש שחזור מבוקר.",
    },
  },
});

function duration(from) {
  const milliseconds = Math.max(0, Date.now() - Date.parse(from || ""));
  if (!Number.isFinite(milliseconds)) return "00:00";
  const seconds = Math.floor(milliseconds / 1000);
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function StageIcon({ state }) {
  if (state === "complete") return <span aria-hidden="true">✓</span>;
  if (state === "failed") return <span aria-hidden="true">✕</span>;
  if (state === "warning") return <span aria-hidden="true">⚠</span>;
  if (state === "current") return <span className="aag-progress-pulse" aria-hidden="true" />;
  return <span aria-hidden="true">○</span>;
}

export default function AagImageProgress({ workspaceSlug, threadSlug }) {
  const [language, setLanguage] = useState(() => loadUiLanguage());
  const [progress, setProgress] = useState(null);
  const [, setClock] = useState(0);

  useEffect(() => {
    const onLanguage = (event) =>
      setLanguage(event?.detail?.language === "he" ? "he" : DEFAULT_UI_LANGUAGE);
    const onStorage = (event) => {
      if (event.key === UI_LANGUAGE_KEY) setLanguage(loadUiLanguage());
    };
    window.addEventListener("aag-composer-language", onLanguage);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("aag-composer-language", onLanguage);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  useEffect(() => {
    if (!threadSlug || workspaceSlug !== "image-generator") {
      setProgress(null);
      return undefined;
    }
    let active = true;
    let timer = null;
    async function poll() {
      try {
        const response = await fetch(
          `${API_BASE}/aag-composer/image-generator/progress/${encodeURIComponent(threadSlug)}`,
          {
            credentials: "same-origin",
            cache: "no-store",
            headers: {
              ...baseHeaders(),
              "X-AAG-Workspace-Path": window.location.pathname,
              "X-AAG-Workspace-Slug": "image-generator",
            },
          }
        );
        const data = await response.json();
        if (active && response.ok) setProgress(data?.active ? data : null);
      } catch {
        // Native history is authoritative; progress polling is enhancement-only.
      } finally {
        if (active) timer = window.setTimeout(poll, 1500);
      }
    }
    void poll();
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [workspaceSlug, threadSlug]);

  useEffect(() => {
    if (!progress?.active) return undefined;
    const timer = window.setInterval(() => setClock((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [progress?.active]);

  const text = LABELS[language] || LABELS.en;
  const visibleFailure =
    text.errors?.[progress?.technicalCode] || progress?.failure || null;
  const current = useMemo(
    () => progress?.stages?.find((stage) => stage.state === "current"),
    [progress]
  );
  if (!progress?.active) return null;

  return (
    <section
      className={`aag-image-progress ${progress.failure ? "is-failed" : ""}`}
      data-testid="aag-image-progress"
      lang={language}
      dir={language === "he" ? "rtl" : "ltr"}
      aria-live="polite"
    >
      <div className="aag-progress-heading">
        <strong>{text.title}</strong>
        {current && (
          <span>
            {text.elapsed}: {duration(progress.activeStageStartedAt || progress.startedAt)}
          </span>
        )}
      </div>
      <ol>
        {(progress.stages || []).map((stage) => (
          <li key={stage.key} className={`is-${stage.state}`}>
            <StageIcon state={stage.state} />
            <span>{text[stage.key] || stage.key}</span>
          </li>
        ))}
      </ol>
      {visibleFailure && (
        <div className="aag-progress-error">
          <p>{visibleFailure}</p>
          {progress.technicalCode && (
            <details>
              <summary>{text.details}</summary>
              <code>{progress.technicalCode}</code>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
