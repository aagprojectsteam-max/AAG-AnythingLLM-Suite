import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { API_BASE } from "@/utils/constants";
import { baseHeaders } from "@/utils/request";
import StorageFiles from "@/models/files";
import {
  DEFAULT_UI_LANGUAGE,
  UI_LANGUAGES,
  loadUiLanguage,
  optionLabel,
  setStoredUiLanguage,
  taxonomyLabel,
  uiText,
} from "./localization";
import "./styles.css";

const RECENTS_KEY = "aag.image-composer.v1.1.recent-styles";
const ATLAS_SIZE_KEY = "aag.image-composer.v1.2.atlas-thumbnail-size";
const ATLAS_SIZES = Object.freeze(["small", "medium", "large"]);
const ENDPOINT = `${API_BASE}/aag-composer/image-generator`;
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];
const UNVALIDATED_IDENTITY_STYLE_CUE = /(?:\b(?:illustrat(?:e|ed|ion|ive)|children(?:'s|s)?[- ]book|storybook|watercolou?r|gouache|comic(?:[- ]book)?|cartoon|anime|manga|oil[- ]paint(?:ed|ing)?|colou?red[- ]pencil|pencil[- ]drawing|line[- ]art|sketch|cel[- ]shad(?:ed|ing)|pixel[- ]art|vector[- ]art|claymation|papercut|paper[- ]cut|origami|low[- ]poly|3d[- ]render)\b|איור|מאויר|מאוירת|מצויר|מצוירת|ספר\s*ילדים|צבעי\s*מים|גואש|קומיקס|קריקטורה|אנימה|מנגה|ציור\s*שמן|עיפרון\s*צבעוני|רישום|סקיצה|אמנות\s*פיקסל|וקטורי|חימר|אוריגמי|תלת[־-]?ממד)/iu;

const INITIAL = Object.freeze({
  operation: "create",
  editMode: "preserve",
  referencePurpose: "identity",
  outputPurpose: "auto",
  batchRelationship: "auto",
  visualFamily: "auto",
  visualSubfamily: "auto",
  atlasSelectionMode: "auto",
  background: "auto",
  visibleText: "auto",
  aspectRatio: "auto",
  countPreset: "3",
  customCount: "6",
  quality: "auto",
  finalOutputQuality: "standard",
  sourcePolicy: "current_attachment",
  preservation: "subject",
  scale: "auto",
  seed: "",
});

const PURPOSES = [
  ["auto", "Auto"],
  ["general", "General"],
  ["wallpaper", "Wallpaper"],
  ["social", "Social graphic"],
  ["poster", "Poster"],
  ["product_commercial", "Product / commercial"],
  ["presentation", "Presentation"],
  ["print", "Print"],
  ["thumbnail", "Thumbnail"],
  ["banner", "Banner"],
];

const RATIOS = [
  ["auto", "Auto"],
  ["1:1", "1:1 Square"],
  ["4:3", "4:3 Landscape classic"],
  ["3:2", "3:2 Photography landscape"],
  ["16:9", "16:9 Widescreen"],
  ["9:16", "9:16 Vertical / phone"],
  ["landscape", "Automatic landscape"],
  ["portrait", "Automatic portrait"],
];

const AagImageComposerPanel = forwardRef(function AagImageComposerPanel(
  { mode, onModeChange, prompt, history = [], threadSlug = null, disabled = false },
  ref
) {
  const [settings, setSettings] = useState({ ...INITIAL });
  const [taxonomy, setTaxonomy] = useState(null);
  const [language, setLanguage] = useState(() => loadUiLanguage());
  const [files, setFiles] = useState([]);
  const [sourceIndex, setSourceIndex] = useState("1");
  const [previewUrls, setPreviewUrls] = useState([]);
  const [previousArtifactPreviewUrl, setPreviousArtifactPreviewUrl] =
    useState(null);
  const [previousArtifactBlob, setPreviousArtifactBlob] = useState(null);
  const [expanded, setExpanded] = useState(true);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [recents, setRecents] = useState(() => loadRecents());
  const [atlasOpen, setAtlasOpen] = useState(false);
  const [atlasSearch, setAtlasSearch] = useState("");
  const [atlasFamily, setAtlasFamily] = useState("all");
  const [atlasLimit, setAtlasLimit] = useState(48);
  const [atlasSize, setAtlasSize] = useState(() => loadAtlasSize());
  const [previewTarget, setPreviewTarget] = useState(null);
  const csrfRef = useRef(null);
  const fileInputRef = useRef(null);
  const prepareLockRef = useRef(false);
  const threadScopeRef = useRef(threadSlug);

  const tr = (key, english) => uiText(language, key, english);

  const setValue = (name, value) =>
    setSettings((current) => ({ ...current, [name]: value }));

  const api = useCallback(async (path, options = {}) => {
    const response = await fetch(`${ENDPOINT}/${path}`, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: {
        ...baseHeaders(),
        "X-AAG-Workspace-Path": window.location.pathname,
        "X-AAG-Workspace-Slug": "image-generator",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const data = await response.json().catch(() => ({
      error: { message: "The local Composer bridge returned an invalid response." },
    }));
    if (!response.ok)
      throw new Error(
        data?.error?.message || `Composer request failed with HTTP ${response.status}.`
      );
    return data;
  }, []);

  const establishSession = useCallback(async () => {
    const data = await api("session");
    csrfRef.current = data.csrf;
    return data.csrf;
  }, [api]);

  useEffect(() => {
    let active = true;
    let retryTimer = null;
    let retryAttempt = 0;

    async function loadTaxonomy() {
      try {
        const catalog = await api("taxonomy");
        if (!active) return;
        if (
          !Array.isArray(catalog?.families) ||
          catalog.families.length !== 28 ||
          catalog.families.reduce(
            (total, family) => total + (family.subfamilies?.length || 0),
            0
          ) !== 493
        )
          throw new Error("The complete Composer visual catalog is unavailable.");
        setTaxonomy(catalog);
        setStatus((current) =>
          current?.source === "taxonomy" ? null : current
        );
      } catch (error) {
        if (!active) return;
        const retryDelay = Math.min(
          500 * 2 ** Math.min(retryAttempt, 3),
          4000
        );
        retryAttempt += 1;
        setStatus((current) =>
          current?.source === "session"
            ? current
            : {
                source: "taxonomy",
                kind: "working",
                title: "Loading Composer taxonomy",
                lines: [
                  `Retrying automatically in ${retryDelay / 1000} seconds. ${error.message}`,
                ],
              }
        );
        retryTimer = window.setTimeout(loadTaxonomy, retryDelay);
      }
    }

    loadTaxonomy();
    return () => {
      active = false;
      if (retryTimer) window.clearTimeout(retryTimer);
    };
  }, [api]);

  useEffect(() => {
    let active = true;
    establishSession().catch((error) => {
      if (!active) return;
      setStatus({
        source: "session",
        kind: "error",
        title: "Composer session unavailable",
        lines: [error.message],
      });
    });
    return () => {
      active = false;
    };
  }, [establishSession]);

  useEffect(() => {
    const urls = files.map((file) => URL.createObjectURL(file));
    setPreviewUrls(urls);
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, [files]);

  const family = useMemo(
    () =>
      taxonomy?.families?.find(
        (entry) => entry.id === settings.visualFamily
      ) || null,
    [taxonomy, settings.visualFamily]
  );

  const selectedStyle = useMemo(
    () =>
      family?.subfamilies?.find(
        (entry) => entry.id === settings.visualSubfamily
      ) || null,
    [family, settings.visualSubfamily]
  );

  useEffect(() => {
    const previous = threadScopeRef.current;
    if (previous && previous !== threadSlug) {
      setSettings((current) => ({
        ...current,
        visualFamily: "auto",
        visualSubfamily: "auto",
        atlasSelectionMode: "auto",
      }));
      setAtlasOpen(false);
      setPreviewTarget(null);
    }
    threadScopeRef.current = threadSlug;
  }, [threadSlug]);

  const validRecents = useMemo(() => {
    if (!taxonomy) return [];
    return recents.filter((recent) => {
      const recentFamily = taxonomy.families.find(
        (entry) => entry.id === recent.family
      );
      return Boolean(
        recentFamily &&
          (recent.subfamily === "auto" ||
            recentFamily.subfamilies.some(
              (entry) => entry.id === recent.subfamily
            ))
      );
    });
  }, [recents, taxonomy]);

  const latestThreadArtifact = useMemo(
    () => findLatestThreadArtifact(history),
    [history]
  );

  const isBatch = settings.operation === "batch";
  const isReference = settings.operation === "reference";
  const isEdit = settings.operation === "transform";
  const isUpscale = settings.operation === "upscale";
  const isGeneration = !isEdit && !isUpscale;
  const isRestyle = isEdit && settings.editMode === "restyle";
  const isIdentityReference =
    isReference && settings.referencePurpose === "identity";
  const showsCreativeStyle =
    (isGeneration && !isIdentityReference) || isRestyle;
  const usesSource = isReference || isEdit || isUpscale;
  const currentSource =
    usesSource && settings.sourcePolicy === "current_attachment";

  useEffect(() => {
    let active = true;
    let objectUrl = null;
    setPreviousArtifactPreviewUrl(null);
    setPreviousArtifactBlob(null);
    if (!latestThreadArtifact?.storageFilename) return () => {};

    StorageFiles.image(latestThreadArtifact.storageFilename)
      .then((blob) => {
        if (!active || !blob) return;
        objectUrl = URL.createObjectURL(blob);
        setPreviousArtifactBlob(blob);
        setPreviousArtifactPreviewUrl(objectUrl);
      })
      .catch(() => {
        // The governed runtime revalidates the source. A failed thumbnail must
        // not replace or authorize a different image.
      });

    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [latestThreadArtifact?.storageFilename]);

  function resetAdvanced() {
    setSettings({ ...INITIAL });
    setFiles([]);
    setSourceIndex("1");
    setStatus(null);
    setAtlasOpen(false);
    setPreviewTarget(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function chooseMode(nextMode) {
    if (nextMode === "auto") {
      resetAdvanced();
      setExpanded(false);
    } else {
      setExpanded(true);
    }
    onModeChange(nextMode);
  }

  function chooseLanguage(nextLanguage) {
    const safeLanguage = nextLanguage === "he" ? "he" : DEFAULT_UI_LANGUAGE;
    setLanguage(safeLanguage);
    setStoredUiLanguage(safeLanguage);
    window.dispatchEvent(
      new CustomEvent("aag-composer-language", {
        detail: { language: safeLanguage },
      })
    );
  }

  function chooseOperation(operation) {
    const nextUsesSource = ["reference", "transform", "upscale"].includes(
      operation
    );
    const nextIsGeneration = ["create", "batch", "reference"].includes(
      operation
    );
    setSettings((current) => ({
      ...current,
      operation,
      editMode: operation === "transform" ? "preserve" : current.editMode,
      outputPurpose: nextIsGeneration ? current.outputPurpose : "auto",
      visualFamily: nextIsGeneration ? current.visualFamily : "auto",
      visualSubfamily: nextIsGeneration ? current.visualSubfamily : "auto",
      atlasSelectionMode: nextIsGeneration
        ? current.atlasSelectionMode
        : "auto",
      background: nextIsGeneration ? current.background : "auto",
      visibleText: nextIsGeneration ? current.visibleText : "auto",
      aspectRatio: nextIsGeneration ? current.aspectRatio : "auto",
      quality: nextIsGeneration ? current.quality : "auto",
      finalOutputQuality:
        operation === "upscale" ? "standard" : current.finalOutputQuality,
      batchRelationship:
        operation === "batch" ? current.batchRelationship : "auto",
      seed: operation === "create" ? current.seed : "",
      sourcePolicy: nextUsesSource
        ? latestThreadArtifact
          ? "previous_artifact"
          : "current_attachment"
        : "current_attachment",
      preservation: operation === "transform" ? current.preservation : "subject",
    }));
    if (!nextUsesSource) {
      setFiles([]);
      setSourceIndex("1");
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function chooseEditMode(editMode) {
    setSettings((current) => ({
      ...current,
      editMode,
      visualFamily:
        editMode === "preserve" ? "auto" : current.visualFamily,
      visualSubfamily:
        editMode === "preserve" ? "auto" : current.visualSubfamily,
      atlasSelectionMode:
        editMode === "preserve" ? "auto" : current.atlasSelectionMode,
    }));
  }

  function chooseReferencePurpose(referencePurpose) {
    setSettings((current) => ({
      ...current,
      referencePurpose,
      visualFamily:
        referencePurpose === "identity" ? "auto" : current.visualFamily,
      visualSubfamily:
        referencePurpose === "identity" ? "auto" : current.visualSubfamily,
      atlasSelectionMode:
        referencePurpose === "identity" ? "auto" : current.atlasSelectionMode,
      quality: referencePurpose === "identity" ? "auto" : current.quality,
    }));
  }

  function chooseSourcePolicy(sourcePolicy) {
    setSettings((current) => ({
      ...current,
      sourcePolicy,
      preservation:
        sourcePolicy === "previous_artifact" ? "subject" : current.preservation,
    }));
    if (sourcePolicy === "previous_artifact") {
      setFiles([]);
      setSourceIndex("1");
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function chooseFiles(event) {
    const selected = [...event.target.files];
    setFiles(selected);
    setSourceIndex(selected.length ? "1" : "");
  }

  function chooseFamily(value) {
    setSettings((current) => ({
      ...current,
      visualFamily: value,
      visualSubfamily: "auto",
      atlasSelectionMode: "auto",
    }));
  }

  function chooseSubfamily(value) {
    setSettings((current) => ({
      ...current,
      visualSubfamily: value,
      atlasSelectionMode:
        current.visualFamily !== "auto" && value !== "auto"
          ? "manual_taxonomy"
          : "auto",
    }));
  }

  function chooseAtlasStyle(familyId, subfamilyId) {
    setSettings((current) => ({
      ...current,
      visualFamily: familyId,
      visualSubfamily: subfamilyId,
      atlasSelectionMode: "manual_browse",
    }));
    setAtlasOpen(false);
    setPreviewTarget(null);
  }

  function clearAtlasStyle() {
    setSettings((current) => ({
      ...current,
      visualFamily: "auto",
      visualSubfamily: "auto",
      atlasSelectionMode: "auto",
    }));
    setPreviewTarget(null);
  }

  function chooseAtlasSize(value) {
    const safeValue = ATLAS_SIZES.includes(value) ? value : "medium";
    setAtlasSize(safeValue);
    storeAtlasSize(safeValue);
  }

  function applyRecent(recent) {
    setSettings((current) => ({
      ...current,
      visualFamily: recent.family,
      visualSubfamily: recent.subfamily,
      atlasSelectionMode:
        recent.subfamily === "auto" ? "auto" : "manual_taxonomy",
    }));
  }

  async function collectPayload() {
    const text = prompt;
    if (!text.trim()) throw new Error("Description is required in the message box below.");
    if (text.length > 12000)
      throw new Error("Description must be 12,000 characters or fewer.");
    if (isIdentityReference && UNVALIDATED_IDENTITY_STYLE_CUE.test(text))
      throw new Error(
        tr(
          "identityStyleConflict",
          "Person identity preservation currently supports validated realistic rendering only. For broader stylization, choose General visual reference."
        )
      );
    if (showsCreativeStyle) {
      const selectedFamily = taxonomy?.families?.find(
        (entry) => entry.id === settings.visualFamily
      );
      if (settings.visualFamily !== "auto" && !selectedFamily)
        throw new Error("Choose an existing visual family from the list.");
      if (
        settings.visualSubfamily !== "auto" &&
        (!selectedFamily ||
          !selectedFamily.subfamilies.some(
            (entry) => entry.id === settings.visualSubfamily
          ))
      )
        throw new Error("Choose an existing subfamily for the selected family.");
    }

    const count = isBatch
      ? Number(
          settings.countPreset === "custom"
            ? settings.customCount
            : settings.countPreset
        )
      : 1;
    if (isBatch && (!Number.isInteger(count) || count < 2 || count > 10))
      throw new Error("Batch count must be a whole number from 2 through 10.");

    const seed = settings.seed === "" ? "auto" : Number(settings.seed);
    if (
      seed !== "auto" &&
      (!Number.isInteger(seed) || seed < 0 || seed > 2147483647)
    )
      throw new Error(
        "Seed must be a whole number from 0 through 2,147,483,647."
      );

    const materializesPreviousReference =
      isReference && settings.sourcePolicy === "previous_artifact";
    let selectedFiles = currentSource ? files : [];
    if (
      usesSource &&
      settings.sourcePolicy === "previous_artifact" &&
      !latestThreadArtifact
    )
      throw new Error(
        isEdit
          ? tr(
              "noImageForEdit",
              "No generated image is available in this thread. Choose Uploaded image and attach a valid source."
            )
          : isReference
            ? tr(
                "noImageForReference",
                "No generated image is available in this thread. Choose Uploaded image and attach a valid reference."
              )
            : tr(
              "noImageForUpscale",
              "No generated image is available in this thread. Choose Uploaded image and attach a valid source."
            )
      );
    if (materializesPreviousReference) {
      if (!previousArtifactBlob)
        throw new Error(
          tr(
            "referenceArtifactUnavailable",
            "The selected thread artifact could not be read as a reference. Choose Uploaded image or try again."
          )
        );
      const artifactSha256 = await sha256Blob(previousArtifactBlob);
      if (artifactSha256 !== latestThreadArtifact.artifactSha256)
        throw new Error(
          tr(
            "referenceArtifactChanged",
            "The selected thread artifact failed its integrity check and was not attached."
          )
        );
      selectedFiles = [
        new File(
          [previousArtifactBlob],
          latestThreadArtifact.storageFilename,
          { type: "image/png" }
        ),
      ];
    }
    if (currentSource && (selectedFiles.length < 1 || selectedFiles.length > 8))
      throw new Error(
        tr(
          "sourceUploadRequired",
          "Choose between 1 and 8 uploaded source images."
        )
      );
    if (
      isEdit &&
      settings.preservation === "identity" &&
      selectedFiles.length !== 1
    )
      throw new Error(
        "Recognizable person preservation requires exactly one current source image."
      );
    if (
      isReference &&
      settings.referencePurpose === "identity" &&
      selectedFiles.length !== 1
    )
      throw new Error(
        tr(
          "identityReferenceCount",
          "Person identity reference requires exactly one source image containing one clearly visible person."
        )
      );
    if (selectedFiles.some((file) => !IMAGE_TYPES.includes(file.type)))
      throw new Error("Sources must be PNG, JPEG, or WebP.");
    if (selectedFiles.some((file) => file.size > 15 * 1024 * 1024))
      throw new Error("Each source image must be 15 MB or smaller.");
    if (
      selectedFiles.reduce((total, file) => total + file.size, 0) >
      16 * 1024 * 1024
    )
      throw new Error("Source images are too large for one protected request.");
    const attachments = [];
    for (const file of selectedFiles) {
      attachments.push({
        name: file.name,
        mime: file.type,
        contentString: await fileAsDataUrl(file),
      });
    }

    return {
      mode: "advanced",
      free_text: text,
      operation: ["create", "batch"].includes(settings.operation)
        ? "generate"
        : isReference
          ? "transform"
          : settings.operation,
      edit_mode: isEdit ? settings.editMode : "not_applicable",
      visual_family:
        showsCreativeStyle ? settings.visualFamily : "auto",
      visual_subfamily:
        showsCreativeStyle ? settings.visualSubfamily : "auto",
      atlas_selection_mode:
        showsCreativeStyle ? settings.atlasSelectionMode : "auto",
      aspect_ratio: isGeneration ? settings.aspectRatio : "auto",
      count,
      quality:
        isGeneration && !isIdentityReference ? settings.quality : "auto",
      final_output_quality: isUpscale
        ? "standard"
        : settings.finalOutputQuality,
      source_policy: isReference
        ? "current_attachment"
        : usesSource
          ? settings.sourcePolicy
          : "auto",
      source_index: isReference
        ? materializesPreviousReference
          ? 1
          : Number(sourceIndex)
        : currentSource
          ? Number(sourceIndex)
          : "none",
      preservation: isEdit
        ? settings.preservation
        : isReference
          ? settings.referencePurpose === "identity"
            ? "identity"
            : "subject"
          : "none",
      scale: isUpscale
        ? settings.scale === "auto"
          ? "auto"
          : Number(settings.scale)
        : "none",
      seed: settings.operation === "create" ? seed : "auto",
      output_purpose: isGeneration ? settings.outputPurpose : "auto",
      background: isGeneration ? settings.background : "auto",
      visible_text: isGeneration ? settings.visibleText : "auto",
      batch_relationship: isBatch ? settings.batchRelationship : "auto",
      reference_purpose: isReference
        ? settings.referencePurpose
        : "not_applicable",
      reference_source: isReference
        ? materializesPreviousReference
          ? "latest_thread_artifact"
          : "current_upload"
        : "not_applicable",
      reference_artifact_sha256: materializesPreviousReference
        ? latestThreadArtifact.artifactSha256
        : "none",
      source_instruction: "",
      attachments,
    };
  }

  async function request(path, payload) {
    const csrf = csrfRef.current || (await establishSession());
    return api(path, {
      method: "POST",
      headers: { "X-AAG-CSRF": csrf },
      body: JSON.stringify(payload),
    });
  }

  async function prepare() {
    if (busy || disabled || prepareLockRef.current) return null;
    prepareLockRef.current = true;
    setBusy(true);
    setStatus({
      kind: "working",
      title: tr("attachingSelections", "Attaching Composer selections"),
      lines: [
        tr(
          "validatingNormalMessage",
          "Validating the controls for the normal chat message…"
        ),
      ],
    });
    try {
      const payload = await collectPayload();
      const data = await request("prepare", payload);
      if (typeof data?.modelMessage !== "string" || !data.modelMessage)
        throw new Error("The trusted Composer context was not returned safely.");
      if (showsCreativeStyle)
        saveRecent(settings.visualFamily, settings.visualSubfamily, setRecents);
      setStatus({
        kind: "success",
        title: tr("selectionsAttached", "Composer selections attached"),
        lines: [
          tr(
            "sendingNormalMessage",
            "Sending this message through the normal AnythingLLM conversation."
          ),
        ],
      });
      return {
        modelMessage: data.modelMessage,
        composerAttachments: payload.attachments,
      };
    } catch (error) {
      setStatus({
        kind: "error",
        title: tr("messageNotSent", "Message not sent"),
        lines: [error.message],
      });
      return null;
    } finally {
      prepareLockRef.current = false;
      setBusy(false);
    }
  }

  useImperativeHandle(ref, () => ({ prepare }), [
    busy,
    disabled,
    prompt,
    settings,
    files,
    sourceIndex,
  ]);

  return (
    <section
      className={`aag-inline-composer ${mode === "advanced" ? "is-advanced" : ""} ${atlasOpen || previewTarget ? "atlas-open" : ""}`}
      data-aag-inline-composer="v1.3"
      lang={language}
      dir={language === "he" ? "rtl" : "ltr"}
      aria-label={tr("composerControls", "AAG Image Composer mode and controls")}
    >
      <div className="aag-composer-bar">
        <div className="aag-composer-identity">
          <span>AAG IMAGE</span>
          <small>Composer UI V1.3</small>
        </div>
        <div
          className="aag-composer-modes"
          role="radiogroup"
          aria-label={tr("modeGroup", "Image Composer mode")}
        >
          <button
            type="button"
            role="radio"
            aria-checked={mode === "auto"}
            className={mode === "auto" ? "active" : ""}
            onClick={() => chooseMode("auto")}
          >
            {tr("autoMode", "AUTO")}
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={mode === "advanced"}
            className={mode === "advanced" ? "active" : ""}
            onClick={() => chooseMode("advanced")}
          >
            {tr("advancedMode", "ADVANCED")}
          </button>
        </div>
        <label className="aag-composer-language">
          <span>{tr("language", "Language")}</span>
          <select
            data-testid="aag-ui-language"
            value={language}
            onChange={(event) => chooseLanguage(event.target.value)}
          >
            {UI_LANGUAGES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {mode === "advanced" && (
          <button
            type="button"
            className="aag-composer-collapse"
            aria-expanded={expanded}
            aria-controls="aag-composer-controls"
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded
              ? tr("hideControls", "Hide controls")
              : tr("showControls", "Show controls")}
          </button>
        )}
      </div>

      {mode === "advanced" && expanded && (
        <div id="aag-composer-controls" className="aag-composer-controls">
          <p className="aag-composer-intro">
            {tr(
              "intro",
              "Set production constraints here, then keep your natural-language request prominent in the message box below."
            )}
          </p>

          <ComposerGroup title={tr("whatToCreate", "What to create")} open>
            <div className="aag-composer-grid">
              <Field label={tr("operation", "Operation")}>
                <select
                  data-testid="aag-operation"
                  value={settings.operation}
                  onChange={(event) => chooseOperation(event.target.value)}
                >
                  <option value="create">
                    {optionLabel(language, "operation", "create", "Create one image")}
                  </option>
                  <option value="batch">
                    {optionLabel(language, "operation", "batch", "Batch / series")}
                  </option>
                  <option value="reference">
                    {optionLabel(
                      language,
                      "operation",
                      "reference",
                      "Create from reference"
                    )}
                  </option>
                  <option value="transform">
                    {optionLabel(language, "operation", "transform", "Edit / transform")}
                  </option>
                  <option value="upscale">
                    {optionLabel(language, "operation", "upscale", "Upscale / enhance")}
                  </option>
                </select>
              </Field>
              {isGeneration && (
                <Field
                  label={tr("intendedUse", "Intended use")}
                  hint={tr("modelGuidance", "Model guidance")}
                >
                  <OptionSelect
                    language={language}
                    scope="purpose"
                    value={settings.outputPurpose}
                    options={PURPOSES}
                    onChange={(value) => setValue("outputPurpose", value)}
                  />
                </Field>
              )}
              {isEdit && (
                <Field
                  label={tr("editMode", "Edit mode")}
                  help={tr(
                    "editModeHelp",
                    "Preserve mode keeps the source appearance and every property you do not explicitly change in the normal chat message. Choose Restyle only when you intend to transform the visual style."
                  )}
                >
                  <OptionSelect
                    language={language}
                    scope="editMode"
                    dataTestId="aag-edit-mode"
                    value={settings.editMode}
                    options={[
                      ["preserve", "Preserve current appearance"],
                      ["restyle", "Restyle image"],
                    ]}
                    onChange={chooseEditMode}
                  />
                </Field>
              )}
              {isReference && (
                <Field
                  label={tr("referencePurpose", "Reference purpose")}
                  help={tr(
                    "referencePurposeHelp",
                    "Person identity uses the validated one-person photographic identity route and fails closed for missing, multiple, hidden, or unevaluable faces. General visual reference does not claim person-identity preservation."
                  )}
                >
                  <OptionSelect
                    language={language}
                    scope="referencePurpose"
                    dataTestId="aag-reference-purpose"
                    value={settings.referencePurpose}
                    options={[
                      ["identity", "Preserve person identity"],
                      ["general_visual", "Preserve general visual reference"],
                    ]}
                    onChange={chooseReferencePurpose}
                  />
                </Field>
              )}
              {isIdentityReference && (
                <p
                  className="aag-composer-note"
                  data-testid="aag-identity-realistic-capability"
                >
                  {tr(
                    "identityRealisticCapability",
                    "Identity preservation currently uses validated realistic rendering."
                  )}
                </p>
              )}
              {isBatch && (
                <Field
                  label={tr("seriesRelationship", "Series relationship")}
                  hint={tr("modelGuidance", "Model guidance")}
                >
                  <OptionSelect
                    language={language}
                    scope="relationship"
                    value={settings.batchRelationship}
                    options={[
                      ["independent", "Independent images"],
                      ["same_concept_different_compositions", "Same concept, different compositions"],
                      ["coordinated_series", "Coordinated series"],
                      ["variations", "Variations"],
                    ]}
                    onChange={(value) => setValue("batchRelationship", value)}
                  />
                </Field>
              )}
            </div>
          </ComposerGroup>

          {(isGeneration || isRestyle) && showsCreativeStyle && (
            <ComposerGroup
              title={tr("styleAppearance", "Style and appearance")}
              open
            >
              <p className="aag-composer-note">
                {tr(
                  "styleNote",
                  "Style guides the model-authored prompt. Every artifact remains raster, including vector-, logo-, map-, infographic-, and pattern-like selections."
                )}
              </p>
              <div className="aag-composer-grid two-column">
                <Field
                  label={tr("visualFamily", "Visual family")}
                  hint={tr("modelGuidance", "Model guidance")}
                  help={
                    taxonomy
                      ? `${taxonomy.families.length} ${tr(
                          "familyCountSuffix",
                          "families available"
                        )}`
                      : tr(
                          "familyLoadingHelp",
                          "Visual families are loading…"
                        )
                  }
                >
                  <select
                    data-testid="aag-visual-family"
                    disabled={!taxonomy}
                    value={settings.visualFamily}
                    onChange={(event) => chooseFamily(event.target.value)}
                  >
                    <option value="auto">
                      {taxonomy
                        ? optionLabel(language, "taxonomy", "auto", "Auto")
                        : tr("loadingFamilies", "Loading visual families…")}
                    </option>
                    {(taxonomy?.families || []).map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {taxonomyLabel(language, entry.id, entry, true)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field
                  label={tr("visualSubfamily", "Visual subfamily")}
                  hint={tr("modelGuidance", "Model guidance")}
                  help={
                    family
                      ? `${family.subfamilies.length} ${tr(
                          "subfamilyCountSuffix",
                          "subfamilies available"
                        )}`
                      : tr("chooseFamily", "Choose a family first")
                  }
                >
                  <select
                    data-testid="aag-visual-subfamily"
                    disabled={!family}
                    value={settings.visualSubfamily}
                    onChange={(event) => chooseSubfamily(event.target.value)}
                  >
                    <option value="auto">
                      {optionLabel(language, "taxonomy", "auto", "Auto")}
                    </option>
                    {(family?.subfamilies || []).map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {taxonomyLabel(language, family.id, entry)}
                      </option>
                    ))}
                  </select>
                </Field>
                {isGeneration && (
                  <Field
                    label={tr("background", "Background")}
                    hint={tr("modelGuidance", "Model guidance")}
                    help={tr(
                      "backgroundHelp",
                      "Transparent pixels are not guaranteed by the current image engine."
                    )}
                  >
                    <OptionSelect
                      language={language}
                      scope="background"
                      value={settings.background}
                      options={[
                        ["auto", "Auto"],
                        ["solid_plain", "Solid / plain"],
                        ["scene_background", "Scene background"],
                        ["isolated_no_background", "Isolated look / no visible background"],
                      ]}
                      onChange={(value) => setValue("background", value)}
                    />
                  </Field>
                )}
                {isGeneration && (
                  <Field
                    label={tr("visibleText", "Visible text")}
                    hint={tr("modelGuidance", "Model guidance")}
                    help={tr(
                      "visibleTextHelp",
                      "Controls text intended to appear inside the image. Exact rendered spelling is not guaranteed by the current image engine."
                    )}
                  >
                    <OptionSelect
                      language={language}
                      scope="visibleText"
                      value={settings.visibleText}
                      options={[
                        ["auto", "Auto"],
                        ["none", "Avoid visible text"],
                        ["model_decides", "Model decides"],
                      ]}
                      onChange={(value) => setValue("visibleText", value)}
                    />
                  </Field>
                )}
              </div>
              <div className="aag-atlas-actions">
                <button
                  type="button"
                  data-testid="aag-browse-visual-atlas"
                  onClick={() => {
                    setAtlasSearch("");
                    setAtlasFamily(
                      settings.visualFamily === "auto"
                        ? "all"
                        : settings.visualFamily
                    );
                    setAtlasLimit(48);
                    setAtlasOpen(true);
                  }}
                >
                  {tr("browseAtlas", "Browse Visual Atlas")}
                </button>
                <span>
                  {tr(
                    "atlasBrowseHelp",
                    "Explore the completed Atlas visually; selecting a card applies that style to this Composer request."
                  )}
                </span>
              </div>
              {selectedStyle && (
                <div
                  className="aag-atlas-selection"
                  data-testid="aag-selected-atlas-style"
                  data-atlas-mode={settings.atlasSelectionMode}
                >
                  <button
                    type="button"
                    className="aag-atlas-selection-image"
                    onClick={() =>
                      setPreviewTarget({ family, style: selectedStyle })
                    }
                    aria-label={tr("openLargePreview", "Open larger style preview")}
                  >
                    <AtlasImage
                      kind="atlas-thumbnail"
                      familyId={family.id}
                      subfamilyId={selectedStyle.id}
                      assetSha256={selectedStyle.atlas?.thumbnail_sha256}
                      alt={`${taxonomyLabel(
                        language,
                        family.id,
                        selectedStyle
                      )} ${tr("stylePreview", "style preview")}`}
                    />
                  </button>
                  <div>
                    <small>{tr("selectedVisualStyle", "Selected visual style")}</small>
                    <strong>
                      {taxonomyLabel(language, family.id, selectedStyle)}
                    </strong>
                    <span>
                      {taxonomyLabel(language, family.id, family, true)} →{" "}
                      {taxonomyLabel(language, family.id, selectedStyle)}
                    </span>
                    {selectedStyle.description && (
                      <p>{selectedStyle.description}</p>
                    )}
                    <div className="aag-atlas-selection-buttons">
                      <button type="button" onClick={() => setAtlasOpen(true)}>
                        {tr("changeStyle", "Change")}
                      </button>
                      <button
                        type="button"
                        data-testid="aag-clear-atlas-style"
                        onClick={clearAtlasStyle}
                      >
                        {tr("clearStyle", "Clear style")}
                      </button>
                    </div>
                  </div>
                </div>
              )}
              {validRecents.length > 0 && (
                <div className="aag-composer-recents">
                  <span>{tr("recentStyles", "Recent styles")}</span>
                  <div>
                    {validRecents.map((recent) => {
                      const recentFamily = taxonomy.families.find(
                        (entry) => entry.id === recent.family
                      );
                      const recentSubfamily = recentFamily.subfamilies.find(
                        (entry) => entry.id === recent.subfamily
                      );
                      return (
                        <button
                          key={`${recent.family}:${recent.subfamily}`}
                          type="button"
                          onClick={() => applyRecent(recent)}
                        >
                          {taxonomyLabel(language, recentFamily.id, recentFamily, true)}
                          {recentSubfamily
                            ? ` / ${taxonomyLabel(
                                language,
                                recentFamily.id,
                                recentSubfamily
                              )}`
                            : ""}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </ComposerGroup>
          )}

          {isGeneration && (
            <ComposerGroup title={tr("sizeQuantity", "Size and quantity")} open>
              <div className="aag-composer-grid">
                <Field
                  label={tr("aspectRatio", "Aspect ratio")}
                  help={tr(
                    "aspectRatioHelp",
                    "3:4, 2:3, 21:9, and custom dimensions are intentionally unavailable because the live engine does not honor them exactly."
                  )}
                >
                  <OptionSelect
                    language={language}
                    scope="ratio"
                    value={settings.aspectRatio}
                    options={
                      isReference && settings.referencePurpose === "identity"
                        ? RATIOS.filter(([value]) =>
                            ["auto", "landscape", "portrait"].includes(value)
                          )
                        : RATIOS
                    }
                    onChange={(value) => setValue("aspectRatio", value)}
                  />
                </Field>
                {isBatch && (
                  <Field label={tr("imageCount", "Image count")}>
                    <OptionSelect
                      language={language}
                      scope="count"
                      dataTestId="aag-count-preset"
                      value={settings.countPreset}
                      options={[
                        ["2", "2"],
                        ["3", "3"],
                        ["4", "4"],
                        ["5", "5"],
                        ["10", "10"],
                        ["custom", "Custom 2–10"],
                      ]}
                      onChange={(value) => setValue("countPreset", value)}
                    />
                  </Field>
                )}
                {isBatch && settings.countPreset === "custom" && (
                  <Field label={tr("customCount", "Custom count")}>
                    <input
                      type="number"
                      min="2"
                      max="10"
                      step="1"
                      inputMode="numeric"
                      value={settings.customCount}
                      onChange={(event) => setValue("customCount", event.target.value)}
                    />
                  </Field>
                )}
              </div>
            </ComposerGroup>
          )}

          {usesSource && (
            <ComposerGroup title={tr("sourceChanges", "Source and changes")} open>
              <div className="aag-composer-grid two-column">
                <Field
                  label={tr("sourceImage", "Source image")}
                  help={
                    settings.sourcePolicy === "previous_artifact"
                      ? latestThreadArtifact
                        ? `${tr(
                            "sourcePreviousHelp",
                            "Uses the latest generated image artifact in this thread."
                          )} ${tr("selectedSource", "Selected:")} ${
                            latestThreadArtifact.filename
                          }`
                        : tr(
                            "sourcePreviousUnavailableHelp",
                            "No generated image is currently available in this thread. Choose Uploaded image instead."
                          )
                      : tr(
                          "sourceUploadHelp",
                          "Upload one or more images with this normal chat turn, then choose the one to use."
                        )
                  }
                >
                  <select
                    data-testid="aag-source-policy"
                    value={settings.sourcePolicy}
                    onChange={(event) => chooseSourcePolicy(event.target.value)}
                  >
                    <option
                      value="previous_artifact"
                      disabled={!latestThreadArtifact}
                    >
                      {latestThreadArtifact
                        ? tr(
                            "previousArtifact",
                            "Last generated image in this thread"
                          )
                        : tr(
                            "noPreviousArtifact",
                            "Last generated image — none available"
                          )}
                    </option>
                    <option value="current_attachment">
                      {optionLabel(
                        language,
                        "source",
                        "current_attachment",
                        "Uploaded image"
                      )}
                    </option>
                  </select>
                </Field>
                {currentSource && (
                  <Field
                    label={tr("sourceImages", "Source images")}
                    help={tr(
                      "sourceImagesHelp",
                      "Up to 8 PNG, JPEG, or WebP images; 15 MB each and 22 MB encoded total."
                    )}
                  >
                    <input
                      ref={fileInputRef}
                      data-testid="aag-source-upload"
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      multiple
                      onChange={chooseFiles}
                    />
                  </Field>
                )}
                {currentSource && (
                  <Field
                    label={tr("useUpload", "Use this upload")}
                    help={tr(
                      "useUploadHelp",
                      "Only the selected numbered upload is used as the production source."
                    )}
                  >
                    <select
                      data-testid="aag-source-index"
                      value={sourceIndex}
                      onChange={(event) => setSourceIndex(event.target.value)}
                    >
                      {files.map((file, index) => (
                        <option key={`${file.name}:${index}`} value={String(index + 1)}>
                          {tr("upload", "Upload")} #{index + 1} — {file.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
                {isEdit && (
                  <Field
                    label={tr("preservation", "Preservation")}
                    help={tr(
                      "preservationHelp",
                      "Person identity requires exactly one current human source image."
                    )}
                  >
                    <select
                      data-testid="aag-preservation"
                      value={settings.preservation}
                      onChange={(event) => setValue("preservation", event.target.value)}
                    >
                      <option value="subject">
                        {optionLabel(
                          language,
                          "preservation",
                          "subject",
                          "Preserve subject / content"
                        )}
                      </option>
                      <option
                        value="identity"
                        disabled={settings.sourcePolicy === "previous_artifact"}
                      >
                        {optionLabel(
                          language,
                          "preservation",
                          "identity",
                          "Preserve the same recognizable person"
                        )}
                      </option>
                    </select>
                  </Field>
                )}
              </div>
              {settings.sourcePolicy === "previous_artifact" &&
                latestThreadArtifact && (
                  <div
                    className="aag-composer-source-summary"
                    data-testid="aag-previous-artifact-summary"
                    aria-live="polite"
                  >
                    {previousArtifactPreviewUrl ? (
                      <img
                        src={previousArtifactPreviewUrl}
                        alt={tr(
                          "selectedSourcePreview",
                          "Preview of the selected source image"
                        )}
                      />
                    ) : (
                      <span className="aag-composer-source-placeholder" aria-hidden="true">
                        ◫
                      </span>
                    )}
                    <div>
                      <strong>
                        {tr(
                          "previousArtifact",
                          "Last generated image in this thread"
                        )}
                      </strong>
                      <span>{latestThreadArtifact.filename}</span>
                      {!previousArtifactPreviewUrl && (
                        <small>
                          {tr(
                            "sourcePreviewUnavailable",
                            "Preview unavailable; the governed source will still be revalidated when you send."
                          )}
                        </small>
                      )}
                    </div>
                  </div>
                )}
              {currentSource && previewUrls.length > 0 && (
                <div className="aag-composer-sources" aria-live="polite">
                  {files.map((file, index) => (
                    <button
                      key={`${file.name}:${index}`}
                      type="button"
                      className={sourceIndex === String(index + 1) ? "selected" : ""}
                      onClick={() => setSourceIndex(String(index + 1))}
                      aria-label={`${tr("useUpload", "Use upload")} ${
                        index + 1
                      }: ${file.name}`}
                    >
                      <img src={previewUrls[index]} alt="" />
                      <span>#{index + 1} {file.name}</span>
                    </button>
                  ))}
                </div>
              )}
            </ComposerGroup>
          )}

          {(isGeneration || isEdit || isUpscale) && (
            <ComposerGroup
              title={tr("qualityProcessing", "Quality and processing")}
              open
            >
              <div className="aag-composer-grid">
              {isGeneration && !isIdentityReference && (
                <Field
                  label={tr("technicalQuality", "Technical quality")}
                  help={tr(
                    "technicalQualityHelp",
                    "This controls the technical route, not artistic detail or beauty."
                  )}
                >
                  <OptionSelect
                    language={language}
                    scope="quality"
                    dataTestId="aag-technical-quality"
                    value={settings.quality}
                    options={[
                      ["auto", "Auto — accepted V5.2 behavior"],
                      ["fast", "Fast"],
                      ["balanced", "Balanced"],
                      ["quality", "Maximum technical quality"],
                    ]}
                    onChange={(value) => setValue("quality", value)}
                  />
                </Field>
              )}
              {isIdentityReference && (
                <div
                  className="aag-composer-quality-status"
                  data-testid="aag-identity-generation-quality"
                >
                  <span>{tr("identityGeneration", "Identity generation")}</span>
                  <strong>
                    {tr("validatedIdentityQuality", "Validated identity quality")}
                  </strong>
                  <small>
                    {tr(
                      "identityTechnicalQualityHelp",
                      "The locked validated identity recipe remains unchanged."
                    )}
                  </small>
                </div>
              )}
              {!isUpscale && (
                <Field
                  label={tr("finalOutputQuality", "Final output quality")}
                  help={tr(
                    "finalOutputQualityHelp",
                    "Enhanced 2× runs only after the governed image has been generated and verified."
                  )}
                >
                  <OptionSelect
                    language={language}
                    scope="finalOutputQuality"
                    dataTestId="aag-final-output-quality"
                    value={settings.finalOutputQuality}
                    options={[
                      ["standard", "Standard"],
                      ["enhanced_2x", "Enhanced 2×"],
                    ]}
                    onChange={(value) => setValue("finalOutputQuality", value)}
                  />
                </Field>
              )}
              {isUpscale && (
                <Field label={tr("upscaleFactor", "Upscale factor")}>
                  <OptionSelect
                    language={language}
                    scope="scale"
                    dataTestId="aag-upscale-factor"
                    value={settings.scale}
                    options={[
                      ["auto", "Auto — backend default"],
                      ["2", "2×"],
                      ["3", "3×"],
                      ["4", "4×"],
                    ]}
                    onChange={(value) => setValue("scale", value)}
                  />
                </Field>
              )}
              {settings.operation === "create" && (
                <Field
                  label={tr("seed", "Seed")}
                  help={tr(
                    "seedHelp",
                    "Optional exact seed for one created image. Leave blank for Auto."
                  )}
                >
                  <input
                    type="number"
                    min="0"
                    max="2147483647"
                    step="1"
                    inputMode="numeric"
                    placeholder={optionLabel(language, "taxonomy", "auto", "Auto")}
                    value={settings.seed}
                    onChange={(event) => setValue("seed", event.target.value)}
                  />
                </Field>
              )}
              </div>
            </ComposerGroup>
          )}

          <ComposerGroup title={tr("specialRequirements", "Special requirements")}>
            <div className="aag-composer-capabilities">
              <p>
                <strong>{tr("modelDirectsTitle", "The model still directs:")}</strong>{" "}
                {tr(
                  "modelDirectsBody",
                  "composition, camera and framing beyond the selected ratio, lighting, materials, textures, atmosphere, depth, visual hierarchy, and professional prompt wording."
                )}
              </p>
              <p>
                <strong>{tr("limitsTitle", "Current image-engine limits:")}</strong>{" "}
                {tr(
                  "limitsBody",
                  "no true SVG/vector export; transparent pixels, exact rendered text, verified seamless tiling, GIS/geographic precision, and deterministic infographic or diagram geometry are not guaranteed."
                )}
              </p>
            </div>
          </ComposerGroup>

          <div className="aag-composer-actions">
            <span>
              <strong>{tr("selectionsReady", "Composer selections ready")}</strong>{" "}
              {tr(
                "nextMessageHelp",
                "These settings will be applied to your next normal chat message."
              )}{" "}
              {tr(
                "sendHelp",
                "Use Enter or the normal send button; there is no separate Composer submission."
              )}
            </span>
          </div>

          {status && (
            <div
              className={`aag-composer-status ${status.kind}`}
              role="status"
              aria-live="polite"
            >
              <strong>{status.title}</strong>
              {status.lines.map((line, index) => (
                <p key={`${index}:${line}`}>{line}</p>
              ))}
            </div>
          )}
        </div>
      )}
      {atlasOpen && taxonomy && (
        <AtlasBrowser
          taxonomy={taxonomy}
          language={language}
          selectedFamily={settings.visualFamily}
          selectedSubfamily={settings.visualSubfamily}
          search={atlasSearch}
          familyFilter={atlasFamily}
          limit={atlasLimit}
          size={atlasSize}
          tr={tr}
          onSearch={(value) => {
            setAtlasSearch(value);
            setAtlasLimit(48);
          }}
          onFamily={(value) => {
            setAtlasFamily(value);
            setAtlasLimit(48);
          }}
          onMore={() => setAtlasLimit((value) => value + 48)}
          onSize={chooseAtlasSize}
          onInspect={(previewFamily, previewStyle) =>
            setPreviewTarget({ family: previewFamily, style: previewStyle })
          }
          onSelect={chooseAtlasStyle}
          onClose={() => setAtlasOpen(false)}
        />
      )}
      {previewTarget && (
        <AtlasLightbox
          family={previewTarget.family}
          style={previewTarget.style}
          language={language}
          selected={
            settings.visualFamily === previewTarget.family.id &&
            settings.visualSubfamily === previewTarget.style.id
          }
          tr={tr}
          onClose={() => setPreviewTarget(null)}
          onSelect={() =>
            chooseAtlasStyle(previewTarget.family.id, previewTarget.style.id)
          }
        />
      )}
    </section>
  );
});

function atlasAssetUrl(kind, familyId, subfamilyId, assetSha256 = "") {
  const derivative = kind === "atlas-thumbnail" ? "webp192-v1" : "png512-v1";
  const version = `${String(assetSha256).slice(0, 16) || "unversioned"}-${derivative}`;
  return `${ENDPOINT}/${kind}/${encodeURIComponent(
    familyId
  )}/${encodeURIComponent(subfamilyId)}?v=${encodeURIComponent(version)}`;
}

function AtlasImage({
  kind,
  familyId,
  subfamilyId,
  assetSha256,
  alt,
  lazy = false,
}) {
  const imageRef = useRef(null);
  const [visible, setVisible] = useState(!lazy);
  const [blobUrl, setBlobUrl] = useState(null);
  const [state, setState] = useState(lazy ? "deferred" : "loading");
  const protectedUrl = useMemo(
    () => atlasAssetUrl(kind, familyId, subfamilyId, assetSha256),
    [kind, familyId, subfamilyId, assetSha256]
  );

  useEffect(() => {
    if (!lazy || visible || !imageRef.current) return undefined;
    if (!("IntersectionObserver" in window)) {
      setVisible(true);
      return undefined;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "240px" }
    );
    observer.observe(imageRef.current);
    return () => observer.disconnect();
  }, [lazy, visible]);

  useEffect(() => {
    if (!visible) return undefined;
    const controller = new AbortController();
    let localBlobUrl = null;
    setState("loading");
    setBlobUrl(null);

    fetch(protectedUrl, {
      credentials: "same-origin",
      cache: "force-cache",
      signal: controller.signal,
      headers: {
        ...baseHeaders(),
        "X-AAG-Workspace-Path": window.location.pathname,
        "X-AAG-Workspace-Slug": "image-generator",
      },
    })
      .then(async (response) => {
        const expectedType =
          kind === "atlas-thumbnail" ? "image/webp" : "image/png";
        const responseType = String(response.headers.get("content-type") || "")
          .split(";", 1)[0]
          .toLowerCase();
        if (!response.ok || responseType !== expectedType)
          throw new Error(
            `Atlas asset request failed with HTTP ${response.status} (${responseType || "no content type"}).`
          );
        const blob = await response.blob();
        if (!blob.size) throw new Error("Atlas asset response was empty.");
        return blob;
      })
      .then((blob) => {
        if (controller.signal.aborted) return;
        localBlobUrl = URL.createObjectURL(blob);
        setBlobUrl(localBlobUrl);
        setState("loaded");
      })
      .catch((error) => {
        if (error.name !== "AbortError") {
          console.error("[AAG Visual Atlas asset]", error.message);
          setState("error");
        }
      });

    return () => {
      controller.abort();
      if (localBlobUrl) URL.revokeObjectURL(localBlobUrl);
    };
  }, [kind, protectedUrl, visible]);

  return (
    <img
      ref={imageRef}
      src={blobUrl || undefined}
      alt={blobUrl ? alt : ""}
      aria-label={alt}
      decoding="async"
      data-atlas-image-state={state}
      data-atlas-asset-url={protectedUrl}
      onError={() => setState("error")}
    />
  );
}

function AtlasBrowser({
  taxonomy,
  language,
  selectedFamily,
  selectedSubfamily,
  search,
  familyFilter,
  limit,
  size,
  tr,
  onSearch,
  onFamily,
  onMore,
  onSize,
  onInspect,
  onSelect,
  onClose,
}) {
  const matches = useMemo(() => {
    const query = search.normalize("NFKC").toLocaleLowerCase().trim();
    const results = [];
    for (const family of taxonomy.families) {
      if (familyFilter !== "all" && family.id !== familyFilter) continue;
      for (const style of family.subfamilies) {
        const haystack = [
          family.id,
          family.label,
          taxonomyLabel(language, family.id, family, true),
          style.id,
          style.label,
          taxonomyLabel(language, family.id, style),
          style.description,
          ...(style.aliases || []),
        ]
          .join(" ")
          .normalize("NFKC")
          .toLocaleLowerCase();
        if (!query || haystack.includes(query)) results.push({ family, style });
      }
    }
    return results;
  }, [taxonomy, language, search, familyFilter]);

  const visible = matches.slice(0, limit);
  return (
    <div
      className="aag-atlas-browser"
      role="dialog"
      aria-modal="true"
      aria-label={tr("visualAtlas", "Visual Atlas")}
      data-testid="aag-visual-atlas-browser"
    >
      <div className="aag-atlas-browser-panel">
        <header>
          <div>
            <strong>{tr("visualAtlas", "Visual Atlas")}</strong>
            <span>
              {tr(
                "atlasCount",
                "493 completed visual styles — choose one to apply it"
              )}
            </span>
          </div>
          <button
            type="button"
            className="aag-atlas-close"
            onClick={onClose}
            aria-label={tr("closeAtlas", "Close Visual Atlas")}
          >
            ×
          </button>
        </header>
        <div className="aag-atlas-toolbar">
          <label>
            <span>{tr("searchStyles", "Search styles")}</span>
            <input
              type="search"
              data-testid="aag-atlas-search"
              value={search}
              placeholder={tr("searchStylesPlaceholder", "Search styles…")}
              onChange={(event) => onSearch(event.target.value)}
              autoFocus
            />
          </label>
          <label>
            <span>{tr("filterFamily", "Filter family")}</span>
            <select
              data-testid="aag-atlas-family-filter"
              value={familyFilter}
              onChange={(event) => onFamily(event.target.value)}
            >
              <option value="all">{tr("allFamilies", "All families")}</option>
              {taxonomy.families.map((family) => (
                <option key={family.id} value={family.id}>
                  {taxonomyLabel(language, family.id, family, true)}
                </option>
              ))}
            </select>
          </label>
          <fieldset className="aag-atlas-size" data-testid="aag-atlas-size-control">
            <legend>{tr("thumbnailSize", "Thumbnail size")}</legend>
            {ATLAS_SIZES.map((value) => (
              <button
                type="button"
                key={value}
                className={size === value ? "active" : ""}
                aria-pressed={size === value}
                data-testid={`aag-atlas-size-${value}`}
                onClick={() => onSize(value)}
              >
                {tr(
                  `thumbnailSize${value}`,
                  value[0].toUpperCase() + value.slice(1)
                )}
              </button>
            ))}
          </fieldset>
        </div>
        <div className="aag-atlas-results" aria-live="polite">
          {matches.length} {tr("stylesFound", "styles found")}
        </div>
        <div
          className="aag-atlas-grid"
          data-testid="aag-atlas-grid"
          data-thumbnail-size={size}
        >
          {visible.map(({ family, style }) => {
            const selected =
              selectedFamily === family.id && selectedSubfamily === style.id;
            return (
              <article
                key={`${family.id}/${style.id}`}
                className={selected ? "selected" : ""}
                data-atlas-style={`${family.id}/${style.id}`}
              >
                <button
                  type="button"
                  className="aag-atlas-image-button"
                  aria-label={`${tr("inspectStyle", "Inspect")} ${taxonomyLabel(language, family.id, style)}`}
                  onClick={() => onInspect(family, style)}
                >
                  <AtlasImage
                    kind="atlas-thumbnail"
                    familyId={family.id}
                    subfamilyId={style.id}
                    assetSha256={style.atlas?.thumbnail_sha256}
                    lazy
                    alt=""
                  />
                </button>
                <span>
                  <strong>{taxonomyLabel(language, family.id, style)}</strong>
                  <small>
                    {taxonomyLabel(language, family.id, family, true)}
                  </small>
                  <button
                    type="button"
                    className="aag-atlas-select-style"
                    aria-pressed={selected}
                    onClick={() => onSelect(family.id, style.id)}
                  >
                    {selected
                      ? tr("styleSelected", "Selected")
                      : tr("selectStyle", "Select style")}
                  </button>
                </span>
              </article>
            );
          })}
        </div>
        {!visible.length && (
          <p className="aag-atlas-empty">
            {tr("noStylesFound", "No styles match this search and family filter.")}
          </p>
        )}
        {visible.length < matches.length && (
          <button
            type="button"
            className="aag-atlas-more"
            data-testid="aag-atlas-load-more"
            onClick={onMore}
          >
            {tr("loadMoreStyles", "Load 48 more styles")}
          </button>
        )}
      </div>
    </div>
  );
}

function AtlasLightbox({
  family,
  style,
  language,
  selected,
  tr,
  onClose,
  onSelect,
}) {
  const dialogRef = useRef(null);
  const closeRef = useRef(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement;
    closeRef.current?.focus();
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
      if (event.key === "Tab" && dialogRef.current) {
        const controls = [
          ...dialogRef.current.querySelectorAll("button:not([disabled])"),
        ];
        if (!controls.length) return;
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="aag-atlas-lightbox"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="aag-atlas-lightbox-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="aag-atlas-preview-title"
        data-testid="aag-atlas-large-preview"
      >
        <button
          ref={closeRef}
          type="button"
          className="aag-atlas-close"
          onClick={onClose}
          aria-label={tr("closePreview", "Close preview")}
        >
          ×
        </button>
        <AtlasImage
          kind="atlas-preview"
          familyId={family.id}
          subfamilyId={style.id}
          assetSha256={style.atlas?.sha256}
          alt={`${taxonomyLabel(language, family.id, style)} ${tr("stylePreview", "style preview")}`}
        />
        <div className="aag-atlas-lightbox-meta">
          <div>
            <strong id="aag-atlas-preview-title">
              {taxonomyLabel(language, family.id, style)}
            </strong>
            <span>
              {tr("category", "Category")}: {taxonomyLabel(language, family.id, family, true)}
            </span>
            <span>
              {tr("subcategory", "Subcategory")}: {taxonomyLabel(language, family.id, style)}
            </span>
          </div>
          <button
            type="button"
            className="aag-atlas-preview-select"
            disabled={selected}
            aria-pressed={selected}
            onClick={onSelect}
          >
            {selected
              ? tr("styleSelected", "Style selected")
              : tr("selectThisStyle", "Select this style")}
          </button>
        </div>
      </div>
    </div>
  );
}

function ComposerGroup({ title, open = false, children }) {
  return (
    <details className="aag-composer-group" open={open}>
      <summary>{title}</summary>
      <div className="aag-composer-group-body">{children}</div>
    </details>
  );
}

function Field({ label, hint = null, help = null, wide = false, children }) {
  return (
    <label className={`aag-composer-field ${wide ? "wide" : ""}`}>
      <span>
        {label} {hint && <em>{hint}</em>}
      </span>
      {children}
      {help && <small>{help}</small>}
    </label>
  );
}

function OptionSelect({
  language,
  scope,
  value,
  options,
  onChange,
  dataTestId = null,
}) {
  return (
    <select
      data-testid={dataTestId}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {options.map(([optionValue, label]) => (
        <option key={optionValue} value={optionValue}>
          {optionLabel(language, scope, optionValue, label)}
        </option>
      ))}
    </select>
  );
}

function findLatestThreadArtifact(history) {
  if (!Array.isArray(history)) return null;
  for (let messageIndex = history.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const outputs = Array.isArray(history[messageIndex]?.outputs)
      ? history[messageIndex].outputs
      : [];
    for (let outputIndex = outputs.length - 1; outputIndex >= 0; outputIndex -= 1) {
      const output = outputs[outputIndex];
      const payload = output?.payload;
      if (
        output?.type !== "imageGenerationCard" ||
        !/^img-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.png$/.test(
          payload?.storageFilename || ""
        ) ||
        !/^[0-9a-f]{64}$/.test(payload?.artifactSha256 || "") ||
        typeof payload?.artifactId !== "string" ||
        !payload.artifactId
      )
        continue;
      return {
        storageFilename: payload.storageFilename,
        artifactSha256: payload.artifactSha256,
        filename:
          typeof payload.filename === "string" && payload.filename
            ? payload.filename
            : "Generated image",
      };
    }
  }
  return null;
}

function fileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read a source image."));
    reader.onload = () => resolve(reader.result);
    reader.readAsDataURL(file);
  });
}

async function sha256Blob(blob) {
  if (!globalThis.crypto?.subtle)
    throw new Error("Secure artifact verification is unavailable in this browser.");
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    await blob.arrayBuffer()
  );
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

function loadRecents() {
  try {
    const value = JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]");
    return Array.isArray(value) ? value.slice(0, 5) : [];
  } catch {
    return [];
  }
}

function loadAtlasSize() {
  try {
    const value = localStorage.getItem(ATLAS_SIZE_KEY);
    return ATLAS_SIZES.includes(value) ? value : "medium";
  } catch {
    return "medium";
  }
}

function storeAtlasSize(value) {
  try {
    localStorage.setItem(ATLAS_SIZE_KEY, value);
  } catch {
    // Presentation preferences must never affect Composer operation.
  }
}

function saveRecent(family, subfamily, setRecents) {
  if (family === "auto") return;
  const entry = { family, subfamily };
  const next = [
    entry,
    ...loadRecents().filter(
      (item) => item.family !== family || item.subfamily !== subfamily
    ),
  ].slice(0, 5);
  localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
  setRecents(next);
}

export default AagImageComposerPanel;
