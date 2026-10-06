import AagArtifactDetails from "@/components/WorkspaceChat/ChatContainer/ChatHistory/AagArtifactDetails";
import AagArtifactPrompt from "@/components/WorkspaceChat/ChatContainer/ChatHistory/AagArtifactPrompt";
import { memo, useEffect, useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import { saveAs } from "file-saver";
import {
  DownloadSimple,
  Copy,
  Check,
  CircleNotch,
  ImageBroken,
  DotsThree,
  PencilSimple,
  FilePdf,
  WarningCircle,
} from "@phosphor-icons/react";
import StorageFiles from "@/models/files";
import { openImageLightbox } from "@/components/ImageLightbox";
import { PASTE_ATTACHMENT_EVENT } from "@/components/WorkspaceChat/ChatContainer/DnDWrapper";
import { PROMPT_INPUT_EVENT } from "@/components/WorkspaceChat/ChatContainer/PromptInput";
import { exportArtifactsAsPdf } from "@/utils/aagArtifactExport";
import { ARTIFACT_PRESENTED_EVENT } from "@/utils/aagLiveArtifacts";
import { artifactFilename } from "@/utils/aagArtifactText";

/**
 * Renders a generated image returned by the `/img` command. The serve endpoint
 * is auth-protected, so we fetch the image as a blob and render an object URL.
 * @param {{content: {storageFilename: string, filename?: string, prompt?: string}}} props
 */
function ImageGenerationCard({ props }) {
  const { t } = useTranslation();
  const { storageFilename, filename, prompt } = props.content || {};
  const [resolvedFile, setResolvedFile] = useState(null);
  const actualFilename = artifactFilename(props.content, resolvedFile);
  const [blob, setBlob] = useState(null);
  const [objectUrl, setObjectUrl] = useState(null);
  const [status, setStatus] = useState("loading");
  const [copied, setCopied] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [pdfStatus, setPdfStatus] = useState("idle");
  const [pdfError, setPdfError] = useState("");
  const menuRef = useRef(null);

  useEffect(() => {
    let revokeUrl = null;
    async function loadImage() {
      const result = await StorageFiles.image(storageFilename);
      if (!result) return setStatus("failed");
      revokeUrl = URL.createObjectURL(result);
      setBlob(result);
      setObjectUrl(revokeUrl);
      setStatus("ready");
    }
    if (storageFilename) {
      loadImage();
    } else {
      setStatus("failed");
    }
    return () => {
      if (revokeUrl) URL.revokeObjectURL(revokeUrl);
    };
  }, [storageFilename]);

  useEffect(() => {
    if (!menuOpen) return;
    function handleClickOutside(e) {
      if (menuRef.current && !menuRef.current.contains(e.target))
        setMenuOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [menuOpen]);

  const handleDownload = () => {
    if (!blob) return;
    saveAs(blob, actualFilename || "image.png");
    setMenuOpen(false);
  };

  const handleCopy = async () => {
    if (!blob) return;
    try {
      await navigator.clipboard.write([
        new ClipboardItem({ [blob.type]: blob }),
      ]);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  };

  const handlePdf = async () => {
    if (!storageFilename || pdfStatus === "working") return;
    setPdfError("");
    setPdfStatus("working");
    try {
      await exportArtifactsAsPdf([storageFilename], "single");
      setPdfStatus("idle");
    } catch (error) {
      setPdfError(error?.message || "PDF export failed.");
      setPdfStatus("failed");
    }
  };

  const handleEdit = async () => {
    setMenuOpen(false);
    if (!blob) return;

    const file = new File([blob], actualFilename || "image.png", {
      type: blob.type,
    });

    window.dispatchEvent(
      new CustomEvent(PASTE_ATTACHMENT_EVENT, {
        detail: {
          files: [file],
          storageFilename,
        },
      })
    );

    window.dispatchEvent(
      new CustomEvent(PROMPT_INPUT_EVENT, {
        detail: { messageContent: "/img ", writeMode: "replace" },
      })
    );
  };

  return (
    <div className="my-2" data-testid="aag-image-card" style={{minWidth:0,maxWidth:"100%"}}>
      <div style={{width:"min(500px,100%)",minWidth:0}}>
        <div data-testid="aag-artifact-filename" data-artifact-id={props.content?.artifactId || ""}
          title={actualFilename || undefined} dir="ltr"
          className="mb-2 text-sm text-zinc-300 light:text-slate-700"
          style={{maxWidth:"100%",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",userSelect:"text"}}>
          {actualFilename || "Generated image"}
        </div>
        {status !== "ready" && (
          <div className="relative rounded-xl overflow-hidden bg-zinc-800 aspect-square w-[280px] max-w-full">
            {status === "loading" && (
              <div className="absolute inset-0 flex items-center justify-center">
                <CircleNotch
                  size={28}
                  weight="bold"
                  className="animate-spin text-zinc-400 light:text-slate-500"
                />
              </div>
            )}

            {status === "failed" && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-y-2 text-zinc-400 light:text-slate-500">
                <ImageBroken size={28} weight="bold" />
                <span className="text-xs">
                  {t("imageGeneration.card.failed-to-load")}
                </span>
              </div>
            )}
          </div>
        )}

        {status === "ready" && (
          <div className="relative group rounded-xl overflow-hidden bg-zinc-800" style={{width:"fit-content",maxWidth:"100%"}}>
            <img
              src={objectUrl}
              onLoad={() => {
                if (props.content?.artifactId && actualFilename) window.dispatchEvent(
                  new CustomEvent(ARTIFACT_PRESENTED_EVENT, {detail: {
                    artifactId: props.content.artifactId, jobId: props.content.jobId, filename: actualFilename,
                  }}));
              }}
              onError={() => setStatus("failed")}
              alt={prompt || t("imageGeneration.card.alt-text")}
              role="button"
              tabIndex={0}
              onClick={() =>
                openImageLightbox([
                  { contentString: objectUrl, name: filename || prompt },
                ])
              }
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  openImageLightbox([
                    { contentString: objectUrl, name: filename || prompt },
                  ]);
                }
              }}
              className="max-h-[280px] object-contain cursor-pointer"
              style={{display:"block",maxWidth:"100%",height:"auto"}}
            />

            {/* Copy — bottom left */}
            <div className="absolute bottom-2 left-2 opacity-0 group-hover:opacity-100 transition-opacity">
              <button
                onClick={handleCopy}
                className="border-none p-2 rounded-lg bg-black/60 hover:bg-black/80 light:bg-slate-200/60 light:hover:bg-slate-200 text-white light:text-slate-700"
              >
                {copied ? (
                  <Check size={16} weight="bold" />
                ) : (
                  <Copy size={16} weight="bold" />
                )}
              </button>
            </div>

            {/* 3-dot menu — top right */}
            <div
              ref={menuRef}
              className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity"
            >
              <button
                onClick={() => setMenuOpen((prev) => !prev)}
                className="border-none p-2 rounded-lg bg-black/60 hover:bg-black/80 light:bg-slate-200/60 light:hover:bg-slate-200 text-white light:text-slate-700"
              >
                <DotsThree size={16} weight="bold" />
              </button>

              {menuOpen && (
                <div className="absolute right-0 mt-1 w-36 rounded-lg bg-zinc-900 light:bg-white border border-zinc-700 light:border-slate-200 shadow-lg z-10 overflow-hidden">
                  <button
                    onClick={handleEdit}
                    className="w-full flex items-center gap-x-2 px-3 py-2 text-sm text-white light:text-slate-700 hover:bg-zinc-800 light:hover:bg-slate-100 border-none"
                  >
                    <PencilSimple size={14} weight="bold" />
                    {t("imageGeneration.card.edit")}
                  </button>
                  <button
                    onClick={handleDownload}
                    className="w-full flex items-center gap-x-2 px-3 py-2 text-sm text-white light:text-slate-700 hover:bg-zinc-800 light:hover:bg-slate-100 border-none"
                  >
                    <DownloadSimple size={14} weight="bold" />
                    {t("imageGeneration.card.download")}
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
        {(
          <div data-testid="aag-artifact-actions" dir="ltr" className="flex items-center gap-2 mt-2" style={{direction:"ltr",flexWrap:"wrap",maxWidth:"100%"}}>
            <AagArtifactDetails artifact={props.content} onFileResolved={setResolvedFile}>
            <button
              type="button"
              onClick={handleDownload}
              disabled={status !== "ready"}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-zinc-700 light:border-slate-300 bg-zinc-900 light:bg-white text-xs text-white light:text-slate-700 hover:bg-zinc-800 light:hover:bg-slate-100"
            >
              <DownloadSimple size={14} weight="bold" /> Download
            </button>
            <button
              type="button"
              onClick={handlePdf}
              disabled={status !== "ready" || pdfStatus === "working"}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-zinc-700 light:border-slate-300 bg-zinc-900 light:bg-white text-xs text-white light:text-slate-700 hover:bg-zinc-800 light:hover:bg-slate-100 disabled:opacity-50"
            >
              {pdfStatus === "working" ? <CircleNotch size={14} className="animate-spin" /> : <FilePdf size={14} weight="bold" />} PDF
            </button>
            <AagArtifactPrompt artifact={props.content} />
            </AagArtifactDetails>
            {pdfError && <span role="alert" className="inline-flex items-center gap-1 text-xs text-red-500"><WarningCircle size={14} />{pdfError}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(ImageGenerationCard);
