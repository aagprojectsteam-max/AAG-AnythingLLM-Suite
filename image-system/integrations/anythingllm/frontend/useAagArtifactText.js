import { useCallback, useEffect, useState } from "react";
import { displayArtifactText } from "@/utils/aagArtifactText";
import { loadUiLanguage, uiText } from "@/components/WorkspaceChat/ChatContainer/PromptInput/AagImageComposerPanel/localization";

export default function useAagArtifactText() {
  const [language, setLanguage] = useState(loadUiLanguage);
  useEffect(() => {
    const sync = () => setLanguage(loadUiLanguage());
    window.addEventListener("aag-composer-language", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("aag-composer-language", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return useCallback((content, outputs, role) => displayArtifactText(
    content, outputs, role, (key, english) => uiText(language, key, english)
  ), [language]);
}
