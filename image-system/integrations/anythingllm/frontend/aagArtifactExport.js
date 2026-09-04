import { saveAs } from "file-saver";

export const AAG_EXPORT_ENDPOINT = "/api/aag/artifact-export/pdf";

export function suggestedPdfFilename(now = new Date()) {
  const stamp = now
    .toISOString()
    .replace(/[-:]/g, "")
    .replace("T", "-")
    .slice(0, 15);
  return `ANYTHING-${stamp}.pdf`;
}

async function requestPdf(storageFilenames, mode) {
  const response = await fetch(AAG_EXPORT_ENDPOINT, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format: "pdf", mode, artifacts: storageFilenames }),
  });
  if (!response.ok) {
    let message = "PDF export failed.";
    try { message = (await response.json())?.message || message; } catch {}
    throw new Error(message);
  }
  const pages = Number(response.headers.get("X-AAG-PDF-Pages"));
  if (pages !== storageFilenames.length)
    throw new Error("PDF export returned an unexpected page count.");
  const blob = await response.blob();
  if (blob.type !== "application/pdf" || blob.size < 100)
    throw new Error("PDF export returned an invalid file.");
  return blob;
}

export async function exportArtifactsAsPdf(storageFilenames, mode) {
  const expected = mode === "single" ? 1 : storageFilenames.length;
  if (
    !Array.isArray(storageFilenames) ||
    storageFilenames.length !== expected ||
    storageFilenames.length < 1 ||
    storageFilenames.length > 10 ||
    new Set(storageFilenames).size !== storageFilenames.length
  ) throw new Error("The requested artifact set is invalid.");

  const suggestedName = suggestedPdfFilename();
  let fileHandle = null;
  if (typeof window.showSaveFilePicker === "function") {
    try {
      // Invoke the native picker synchronously from the click gesture before
      // requesting any server work, preserving browser user activation.
      fileHandle = await window.showSaveFilePicker({
        suggestedName,
        types: [{ description: "PDF document", accept: { "application/pdf": [".pdf"] } }],
        excludeAcceptAllOption: true,
      });
    } catch (error) {
      if (error?.name === "AbortError") return { cancelled: true, method: "save-as" };
      fileHandle = null;
    }
  }

  const blob = await requestPdf(storageFilenames, mode);
  if (fileHandle) {
    const writable = await fileHandle.createWritable();
    try {
      await writable.write(blob);
      await writable.close();
    } catch (error) {
      try { await writable.abort(); } catch {}
      throw error;
    }
    return { cancelled: false, method: "save-as", filename: fileHandle.name || suggestedName };
  }
  saveAs(blob, suggestedName);
  return { cancelled: false, method: "browser-download", filename: suggestedName };
}

