// Desktop integrations implement this capability in their privileged host.
// The web UI passes artifact identities and hashes, never server file paths.
export function desktopArtifacts() {
  const bridge = globalThis.window?.aagDesktopArtifacts;
  return bridge?.version === 1 && typeof bridge.resolveArtifact === "function" && typeof bridge.revealArtifact === "function" ? bridge : null;
}

export function humanFileSize(bytes) {
  if (!Number.isSafeInteger(bytes) || bytes < 0) return null;
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let n = bytes / 1024, index = 0;
  while (n >= 1024 && index < units.length - 1) { n /= 1024; index += 1; }
  return `${n.toFixed(n < 10 ? 2 : 1)} ${units[index]} (${bytes.toLocaleString("en-US")} bytes)`;
}

const reference = file => ({jobId:file.jobId, artifactId:file.artifactId,
  storageFilename:file.filename, sha256:file.sha256});

export async function resolveDesktopFile(file, bridge = desktopArtifacts()) {
  if (file?.location?.scope === "HOST_LOCAL" && file.state === "AVAILABLE") return file;
  const remote = {...file, localAccessible:false};
  if (!bridge || file?.state !== "AVAILABLE") return remote;
  try {
    const local = await bridge.resolveArtifact(reference(file));
    // The desktop host must check its current file, not echo generation paths.
    if (local?.exists !== true || local.scope !== "DESKTOP_LOCAL" || local.sha256 !== file.sha256 ||
        local.sizeBytes !== file.sizeBytes || typeof local.token !== "string" || !local.token || local.token.length > 1024 ||
        typeof local.path !== "string" || local.path.length > 4096 || /[\x00-\x1f]/.test(local.path) ||
        !/^(?:\/|[A-Za-z]:[\\/])/.test(local.path)) return remote;
    return {...remote, displayFilename:local.path.split(/[\\/]/).at(-1), location:{scope:"DESKTOP_LOCAL", value:local.path}, localAccessible:true, revealToken:local.token};
  } catch { return remote; }
}

export async function revealCurrentFile(file, bridge = desktopArtifacts(), hostReveal = null) {
  if (file?.location?.scope === "HOST_LOCAL") {
    if (!file.localAccessible || !hostReveal) return {revealed:false, file};
    try {
      // The host resolves afresh; only identity crosses the browser boundary.
      const result = await hostReveal({artifact:file.filename, jobId:file.jobId, artifactId:file.artifactId});
      return {revealed:result?.revealed === true, file:result?.file || file};
    } catch { return {revealed:false, file}; }
  }
  const current = await resolveDesktopFile(file, bridge);
  if (!current.localAccessible) return {revealed:false, file:current};
  try {
    const result = await bridge.revealArtifact({...reference(current), token:current.revealToken});
    return {revealed:result?.revealed === true, file:current};
  } catch { return {revealed:false, file:{...current, localAccessible:false}}; }
}

export function fileGroup(file, text) {
  const recorded = (id, en, he, value) => ({id, label:{en,he}, ...(value == null ? {state:"NOT_RECORDED"} : {state:"RECORDED", value})});
  const available = file?.state === "AVAILABLE";
  const location = available && ["HOST_LOCAL","DESKTOP_LOCAL"].includes(file.location?.scope) ? file.location.value : text.notAvailable;
  return {id:"FILE", rows:[
    recorded("filename", "Filename", "שם הקובץ", file?.displayFilename || file?.filename),
    recorded("current_file_size", "File size", "גודל הקובץ", available ? humanFileSize(file.sizeBytes) : null),
    recorded("current_file_location", "File location", "מיקום הקובץ", location),
    recorded("application_path", "Application path", "נתיב ביישום", file?.applicationPath || (file?.location?.scope === "APPLICATION_STORAGE" ? file.location.value : null)),
    recorded("current_format", "Format", "פורמט", available ? file.format : null),
    recorded("current_dimensions", "Dimensions", "ממדים", available && file.width && file.height ? `${file.width} × ${file.height}` : null),
    recorded("current_sha256", "SHA256", "SHA256", available ? file.sha256 : null),
  ]};
}
