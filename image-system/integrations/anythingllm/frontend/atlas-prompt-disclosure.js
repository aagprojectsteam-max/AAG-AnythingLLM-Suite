// Minimal disclosure for the existing Atlas. No generation or selection hooks.
(() => {
  // Keep React's original Select button and click handler in place.
  function iconAction(button, label, icon) {
    button.dataset.atlasAction = icon;
    button.dataset.tooltip = label;
    button.setAttribute("aria-label", label);
    button.title = label;
    if (!button.dataset.atlasKeyguard) {
      button.dataset.atlasKeyguard = "true";
      // Keep Enter/Space on the focused Atlas button, away from chat shortcuts.
      for (const type of ["keydown", "keyup"]) button.addEventListener(type, event => {
        if (event.key === "Enter" || event.key === " ") event.stopPropagation();
      });
    }
  }
  const tooltip = document.createElement("div");
  tooltip.id = "aag-atlas-action-tooltip"; tooltip.role = "tooltip";
  tooltip.hidden = true; document.body.append(tooltip);
  let tooltipOwner;
  function showTooltip(button) {
    if (!button) return;
    tooltipOwner = button; tooltip.textContent = button.dataset.tooltip;
    tooltip.hidden = false; button.setAttribute("aria-describedby", tooltip.id);
    const r = button.getBoundingClientRect();
    const left = Math.max(8, Math.min(innerWidth - tooltip.offsetWidth - 8, r.left + r.width / 2 - tooltip.offsetWidth / 2));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${Math.max(8, r.top - tooltip.offsetHeight - 7)}px`;
  }
  function hideTooltip() {
    tooltipOwner?.removeAttribute("aria-describedby"); tooltipOwner = null; tooltip.hidden = true;
  }
  document.addEventListener("pointerover", e => showTooltip(e.target.closest?.("[data-atlas-action]")));
  document.addEventListener("focusin", e => showTooltip(e.target.closest?.("[data-atlas-action]")));
  document.addEventListener("pointerout", e => { if (e.target.closest?.("[data-atlas-action]")) hideTooltip(); });
  document.addEventListener("focusout", hideTooltip);
  document.addEventListener("keydown", e => { if (e.key === "Escape") hideTooltip(); });
  document.addEventListener("scroll", () => {
    const focused = document.activeElement?.closest?.("[data-atlas-action]");
    if (focused) requestAnimationFrame(() => showTooltip(focused));
    else hideTooltip();
  }, true);
  let catalogPromise;
  async function catalog() {
    if (!catalogPromise) catalogPromise = (async () => {
      const headers = {"X-AAG-Workspace-Path": location.pathname, "X-AAG-Workspace-Slug": "image-generator"};
      const token = localStorage.getItem("anythingllm_authToken");
      if (token) headers.Authorization = `Bearer ${token}`;
      const response = await fetch("/api/aag-composer/image-generator/taxonomy", {headers, cache: "no-store"});
      if (!response.ok) throw Error("Prompt metadata is unavailable.");
      const data = await response.json();
      return new Map(data.families.flatMap(f => f.subfamilies.map(s => [f.id + "/" + s.id, s.atlas])));
    })().catch(error => { catalogPromise = null; throw error; });
    return catalogPromise;
  }
  const digest = async text => [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)))].map(x => x.toString(16).padStart(2,"0")).join("");
  function add(host, id, image) {
    if (host.querySelector(":scope > .aag-atlas-prompt")) return;
    const box = document.createElement("div"); box.className = "aag-atlas-prompt"; box.classList.add("aag-atlas-prompt-disclosure");
    const toggle = document.createElement("button"); toggle.type = "button"; toggle.textContent = "Show Prompt";
    toggle.className = "aag-atlas-select-style"; toggle.dataset.testid = "aag-atlas-show-prompt"; toggle.setAttribute("aria-expanded", "false");
    const body = document.createElement("div"); body.className = "aag-atlas-prompt-body"; body.hidden = true;
    toggle.setAttribute("aria-label", "Show Prompt");
    if (host.matches("[data-atlas-style]")) iconAction(toggle, "Show Prompt", "prompt");
    box.append(toggle, body); host.append(box);
    let open = false;
    toggle.addEventListener("click", async event => {
      event.stopPropagation(); open = !open; body.hidden = !open; toggle.textContent = open ? "Hide Prompt" : "Show Prompt"; toggle.setAttribute("aria-expanded", String(open));
      host.dataset.promptOpen = String(open);
      toggle.setAttribute("aria-label", open ? "Hide Prompt" : "Show Prompt");
      if (host.matches("[data-atlas-style]")) {
        iconAction(toggle, open ? "Hide Prompt" : "Show Prompt", "prompt");
        if (tooltipOwner === toggle) showTooltip(toggle);
      }
      if (!open || body.dataset.ready) return;
      body.textContent = "Loading prompt…";
      try {
        const proof = (await catalog()).get(id);
        const asset = new URL(image.dataset.atlasAssetUrl, location.origin);
        const expected = asset.searchParams.get("v") || "";
        // Previews carry the original image hash; thumbnails carry their derivative hash.
        const imageHash = asset.pathname.includes("atlas-preview") ? proof?.sha256 : proof?.thumbnail_sha256;
        if (!proof || !expected.startsWith(imageHash?.slice(0,16) + "-") || typeof proof.prompt !== "string" || await digest(proof.prompt) !== proof.prompt_sha256)
          throw Error("Prompt does not match this image. Reload the Atlas.");
        body.textContent = "";
        const pre = document.createElement("pre"); pre.dataset.testid = "aag-atlas-prompt-text"; pre.textContent = proof.prompt;
        pre.tabIndex = 0; pre.setAttribute("role", "region"); pre.setAttribute("aria-label", "Image-bound prompt");
        const copy = document.createElement("button"); copy.type = "button"; copy.textContent = "Copy Prompt"; copy.className = "aag-atlas-select-style"; copy.dataset.testid = "aag-atlas-copy-prompt";
        const status = document.createElement("span"); status.setAttribute("role","status");
        copy.addEventListener("click", async e => {e.stopPropagation(); try {await navigator.clipboard.writeText(proof.prompt); status.textContent = "Copied";} catch {status.textContent = "Select the prompt to copy it.";}});
        body.append(pre, copy, status); body.dataset.ready = "true";
      } catch (error) {body.textContent = error.message;}
    });
  }
  function decorate() {
    document.querySelectorAll("[data-atlas-style]").forEach(card => {
      const select = card.querySelector(":scope > span > .aag-atlas-select-style");
      if (select) iconAction(select, "Select style", "select");
      const img = card.querySelector("img[data-atlas-asset-url]"); if (img) add(card, card.dataset.atlasStyle, img);
    });
    document.querySelectorAll('[data-testid="aag-atlas-large-preview"]').forEach(card => {
      const img = card.querySelector("img[data-atlas-asset-url]");
      const match = img?.dataset.atlasAssetUrl.match(/\/atlas-preview\/([a-z0-9-]+\/[a-z0-9-]+)\?/);
      if (match) add(card, match[1], img);
    });
  }
  const observer = new MutationObserver(decorate);
  observer.observe(document.documentElement, {childList:true, subtree:true}); decorate();
})();
