import { memo, useMemo, useState } from "react";
import { FilePdf, CircleNotch, WarningCircle } from "@phosphor-icons/react";
import ImageGenerationCard from "@/components/WorkspaceChat/ChatContainer/ChatHistory/ImageGenerationCard";
import { exportArtifactsAsPdf } from "@/utils/aagArtifactExport";

function AagImageCollection({ outputs = [] }) {
  const ordered = useMemo(
    () => [...outputs].sort((left, right) => Number(left?.payload?.logicalIndex) - Number(right?.payload?.logicalIndex)),
    [outputs]
  );
  const first = ordered[0]?.payload || {};
  const requested = Number(first.requestedCount || ordered.length);
  const completed = Number(first.completedCount || ordered.length);
  const complete = first.collectionComplete === true && ordered.length === requested;
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");

  const saveAll = async () => {
    if (!complete || exporting) return;
    setError("");
    setExporting(true);
    try {
      await exportArtifactsAsPdf(ordered.map((output) => output.payload.storageFilename), "collection");
    } catch (exportError) {
      setError(exportError?.message || "PDF export failed.");
    } finally {
      setExporting(false);
    }
  };

  return (
    <section className="rounded-xl border border-zinc-700 light:border-slate-200 bg-zinc-900/40 light:bg-slate-50 p-3">
      <div className="mb-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="m-0 text-sm font-semibold text-white light:text-slate-800">Image collection</h3>
          <span className="text-xs text-zinc-400 light:text-slate-500">{completed}/{requested} verified</span>
        </div>
        {first.collectionBrief && <p className="mt-1 mb-0 text-xs text-zinc-400 light:text-slate-600">{first.collectionBrief}</p>}
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        {ordered.map((output) => (
          <div key={output.payload.artifactId} className="min-w-0">
            <div className="text-xs text-zinc-400 light:text-slate-500 mb-1">Image {output.payload.logicalIndex} of {requested}</div>
            <ImageGenerationCard props={{ content: output.payload }} />
          </div>
        ))}
      </div>
      <div className="mt-3 pt-3 border-t border-zinc-700 light:border-slate-200">
        <button
          type="button"
          onClick={saveAll}
          disabled={!complete || exporting}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border-none bg-theme-button-primary hover:bg-theme-button-primary-hover text-white text-sm disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {exporting ? <CircleNotch size={16} className="animate-spin" /> : <FilePdf size={16} weight="bold" />}
          Save all as PDF
        </button>
        {!complete && <p className="mt-2 mb-0 text-xs text-amber-500">Full PDF is available only when all {requested} intended images are verified. Completed images remain individually downloadable and exportable.</p>}
        {error && <p role="alert" className="mt-2 mb-0 text-xs text-red-500 flex items-center gap-1"><WarningCircle size={14} />{error}</p>}
      </div>
    </section>
  );
}

export default memo(AagImageCollection);
