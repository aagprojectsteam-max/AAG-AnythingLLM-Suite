#!/usr/bin/env python3
"""Live Brave acceptance through the production AnythingLLM origin and asset route."""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket


class Cdp:
    def __init__(self, url: str):
        self.socket = websocket.create_connection(url, timeout=15, origin="http://127.0.0.1:9224")
        self.identifier = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.identifier += 1
        identifier = self.identifier
        self.socket.send(json.dumps({"id": identifier, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get("id") != identifier:
                continue
            if "error" in message:
                raise RuntimeError(f"CDP {method}: {message['error']}")
            return message.get("result", {})

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
            "userGesture": True,
        })
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"].get("text", "browser evaluation failed"))
        return result.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.evaluate(expression)
            if value:
                return value
            time.sleep(0.15)
        raise TimeoutError(f"browser condition timed out: {expression}")

    def screenshot(self, path: Path) -> None:
        encoded = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})["data"]
        path.write_bytes(base64.b64decode(encoded))


def fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--url",
        default="https://anythingllm.localhost/workspace/image-generator/t/896a89d0-5579-4b71-b7e5-427abfcbf64d",
    )
    parser.add_argument("--browser", default="/usr/bin/brave-browser")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="aag-atlas-browser-"))
    chrome = subprocess.Popen([
        args.browser,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--ignore-certificate-errors",
        "--remote-allow-origins=*",
        "--remote-debugging-port=9224",
        f"--user-data-dir={profile}",
        "--window-size=1440,1100",
        args.url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    checks: list[dict] = []

    def check(name: str, condition, evidence=None):
        passed = bool(condition)
        checks.append({"name": name, "pass": passed, "evidence": evidence})
        if not passed:
            raise AssertionError(f"acceptance failed: {name}: {evidence}")

    try:
        deadline = time.monotonic() + 20
        targets = None
        while time.monotonic() < deadline:
            try:
                targets = fetch_json("http://127.0.0.1:9224/json/list")
                if targets:
                    break
            except Exception:
                time.sleep(0.2)
        if not targets:
            raise RuntimeError("Chrome debugging target did not start")
        target = next((item for item in targets if "/workspace/image-generator" in item.get("url", "")), None)
        if target is None:
            target = next((item for item in targets if item.get("type") == "page"), targets[0])
        cdp = Cdp(target["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")
        if "/workspace/image-generator" not in str(cdp.evaluate("location.href")):
            cdp.call("Page.navigate", {"url": args.url})
        cdp.wait("document.querySelector('[data-aag-inline-composer=\"v1.3\"]') !== null", timeout=30)
        check("open_composer", True, cdp.evaluate("location.pathname"))
        cdp.evaluate("""(()=>{window.__aagAtlasErrors=[];const old=console.error.bind(console);console.error=(...x)=>{window.__aagAtlasErrors.push(x.map(value=>String(value)).join(' '));old(...x)};window.addEventListener('error',e=>window.__aagAtlasErrors.push(String(e.message||e.error)));window.addEventListener('unhandledrejection',e=>window.__aagAtlasErrors.push(String(e.reason)));return true})()""")

        cdp.evaluate("[...document.querySelectorAll('[role=radio]')].find(x=>x.textContent.trim()==='ADVANCED').click()")
        cdp.wait("document.querySelector('select[data-testid=\"aag-visual-family\"]')?.options.length === 29")
        check("family_catalog_loaded", True, 28)

        cdp.evaluate("(()=>{const x=document.querySelector('select[data-testid=\"aag-visual-family\"]');x.value='cinematic-film-still';x.dispatchEvent(new Event('change',{bubbles:true}))})()")
        sub_count = cdp.wait("document.querySelector('select[data-testid=\"aag-visual-subfamily\"]')?.options.length")
        check("relevant_subfamilies_appear", sub_count > 2, sub_count - 1)

        cdp.evaluate("(()=>{const x=document.querySelector('select[data-testid=\"aag-visual-subfamily\"]');x.value='feature-film-look';x.dispatchEvent(new Event('change',{bubbles:true}))})()")
        selected = cdp.wait("document.querySelector('[data-testid=\"aag-selected-atlas-style\"]')?.innerText")
        cdp.wait("document.querySelector('[data-testid=\"aag-selected-atlas-style\"] img')?.dataset.atlasImageState === 'loaded' && document.querySelector('[data-testid=\"aag-selected-atlas-style\"] img')?.naturalWidth === 192")
        first_image = cdp.evaluate("(()=>{const x=document.querySelector('[data-testid=\"aag-selected-atlas-style\"] img');return {src:x.src,url:x.dataset.atlasAssetUrl,width:x.naturalWidth,height:x.naturalHeight}})()")
        check("feature_film_actual_thumbnail", "Feature-film look" in selected and first_image["src"].startswith("blob:https://anythingllm.localhost/") and "/cinematic-film-still/feature-film-look?v=" in first_image["url"] and first_image["width"] == 192, first_image)
        check("manual_taxonomy_mode", cdp.evaluate("document.querySelector('[data-testid=\"aag-selected-atlas-style\"]')?.dataset.atlasMode") == "manual_taxonomy")

        cdp.evaluate("(()=>{const x=document.querySelector('select[data-testid=\"aag-visual-subfamily\"]');x.value='documentary-film';x.dispatchEvent(new Event('change',{bubbles:true}))})()")
        cdp.wait("document.querySelector('[data-testid=\"aag-selected-atlas-style\"] img')?.dataset.atlasImageState === 'loaded' && document.querySelector('[data-testid=\"aag-selected-atlas-style\"] img')?.dataset.atlasAssetUrl.includes('/cinematic-film-still/documentary-film?v=')")
        second_image = cdp.evaluate("(()=>{const x=document.querySelector('[data-testid=\"aag-selected-atlas-style\"] img');return {src:x.src,url:x.dataset.atlasAssetUrl,width:x.naturalWidth}})()")
        check("preview_changes_with_subfamily", second_image["src"] != first_image["src"] and second_image["url"] != first_image["url"] and second_image["width"] == 192, second_image)

        cdp.evaluate("document.querySelector('[data-testid=\"aag-browse-visual-atlas\"]')?.click()")
        cdp.wait("document.querySelector('[data-testid=\"aag-visual-atlas-browser\"]') !== null")
        cards = cdp.evaluate("document.querySelectorAll('[data-testid=\"aag-atlas-grid\"] [data-atlas-style]').length")
        check("browser_opens", True)
        check("bounded_initial_grid", 2 < cards <= 48, cards)
        card_geometry = cdp.evaluate("(()=>{const b=document.querySelector('[data-atlas-style]');const i=b.querySelector('img');const s=b.querySelector('span');const br=b.getBoundingClientRect(),ir=i.getBoundingClientRect(),sr=s.getBoundingClientRect();return {button:Math.round(br.height),image:Math.round(ir.height),label:Math.round(sr.height),label_visible:sr.bottom<=br.bottom+1&&sr.height>=50,text:s.innerText}})()")
        check("readable_style_names", card_geometry["label_visible"] and bool(card_geometry["text"].strip()), card_geometry)
        cdp.wait("[...document.querySelectorAll('[data-testid=\"aag-atlas-grid\"] img')].filter(x=>x.dataset.atlasImageState==='loaded'&&x.naturalWidth===192).length >= 3")
        resources = cdp.evaluate("performance.getEntriesByType('resource').filter(x=>x.name.includes('/atlas-')).map(x=>({name:x.name,status:x.responseStatus||0,transfer:x.transferSize}))")
        thumbnail_resources = [item for item in resources if "atlas-thumbnail" in item["name"]]
        check("multiple_actual_thumbnails_visible", len(thumbnail_resources) >= 3 and all(item["status"] in (0, 200) for item in thumbnail_resources), thumbnail_resources[:5])
        check("no_full_resolution_bulk_load", not any("atlas-preview" in item["name"] for item in resources), resources[:3])
        check("no_493_image_eager_load", len(thumbnail_resources) < 49, len(thumbnail_resources))
        cdp.screenshot(output.with_name("atlas-browser-grid.png"))

        cdp.evaluate("(()=>{const x=document.querySelector('[data-testid=\"aag-atlas-family-filter\"]');x.value='all';x.dispatchEvent(new Event('change',{bubbles:true}));const q=document.querySelector('[data-testid=\"aag-atlas-search\"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(q,'vintage travel poster');q.dispatchEvent(new Event('input',{bubbles:true}));q.dispatchEvent(new Event('change',{bubbles:true}))})()")
        result_text = cdp.wait("document.querySelector('.aag-atlas-results')?.textContent.includes('1 styles') && document.querySelector('.aag-atlas-results').textContent")
        search_cards = cdp.evaluate("[...document.querySelectorAll('[data-atlas-style]')].map(x=>x.dataset.atlasStyle)")
        check("search_and_family_filter", search_cards == ["retro-vintage/vintage-travel-poster"], {"text": result_text, "cards": search_cards})
        cdp.wait("document.querySelector('[data-atlas-style=\"retro-vintage/vintage-travel-poster\"] img')?.dataset.atlasImageState === 'loaded' && document.querySelector('[data-atlas-style=\"retro-vintage/vintage-travel-poster\"] img')?.naturalWidth === 192")

        cdp.evaluate("document.querySelector('[data-testid=\"aag-atlas-size-small\"]')?.click()")
        check("gallery_size_control", cdp.wait("document.querySelector('[data-testid=\"aag-atlas-grid\"]')?.dataset.thumbnailSize === 'small'") == True)
        cdp.evaluate("document.querySelector('[data-atlas-style=\"retro-vintage/vintage-travel-poster\"] .aag-atlas-image-button')?.click()")
        cdp.wait("document.querySelector('.aag-atlas-lightbox') !== null")
        check("card_opens_preview", True)
        cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
        cdp.wait("document.querySelector('.aag-atlas-lightbox') === null")
        check("escape_closes_preview", True)
        cdp.evaluate("document.querySelector('[data-atlas-style=\"retro-vintage/vintage-travel-poster\"] .aag-atlas-select-style')?.click()")
        cdp.wait("document.querySelector('[data-testid=\"aag-visual-atlas-browser\"]') === null")
        chosen = cdp.wait("document.querySelector('[data-testid=\"aag-selected-atlas-style\"]')?.innerText")
        check("browse_selection_applied", "Vintage travel poster" in chosen, chosen)
        cdp.wait("document.querySelector('[data-testid=\"aag-selected-atlas-style\"] img')?.dataset.atlasImageState === 'loaded' && document.querySelector('[data-testid=\"aag-selected-atlas-style\"] img')?.naturalWidth === 192")
        check("manual_browse_mode", cdp.evaluate("document.querySelector('[data-testid=\"aag-selected-atlas-style\"]')?.dataset.atlasMode") == "manual_browse")
        cdp.screenshot(output.with_name("atlas-selected-style.png"))

        cdp.evaluate("document.querySelector('.aag-atlas-selection-image')?.click()")
        cdp.wait("document.querySelector('.aag-atlas-lightbox img')?.dataset.atlasImageState === 'loaded' && document.querySelector('.aag-atlas-lightbox img')?.naturalWidth === 512")
        preview_image = cdp.evaluate("(()=>{const x=document.querySelector('.aag-atlas-lightbox img');return {src:x.src,url:x.dataset.atlasAssetUrl,width:x.naturalWidth,height:x.naturalHeight}})()")
        check("large_completed_preview", preview_image["src"].startswith("blob:https://anythingllm.localhost/") and "/retro-vintage/vintage-travel-poster?v=" in preview_image["url"] and preview_image["width"] == 512, preview_image)
        cdp.evaluate("document.querySelector('.aag-atlas-lightbox .aag-atlas-close')?.click()")

        cdp.evaluate("document.querySelector('[data-testid=\"aag-clear-atlas-style\"]')?.click()")
        cdp.wait("document.querySelector('[data-testid=\"aag-selected-atlas-style\"]') === null")
        clear_values = cdp.evaluate("[document.querySelector('select[data-testid=\"aag-visual-family\"]').value,document.querySelector('select[data-testid=\"aag-visual-subfamily\"]').value]")
        check("clear_has_no_stale_selection", clear_values == ["auto", "auto"], clear_values)

        payload_base = {
            "mode": "advanced", "operation": "generate", "edit_mode": "not_applicable",
            "aspect_ratio": "auto", "count": 1, "quality": "auto", "final_output_quality": "standard",
            "source_policy": "auto", "source_index": "none", "preservation": "none", "scale": "none",
            "seed": "auto", "output_purpose": "auto", "background": "auto", "visible_text": "auto",
            "batch_relationship": "auto", "reference_purpose": "not_applicable",
            "reference_source": "not_applicable", "reference_artifact_sha256": "none",
            "source_instruction": "", "attachments": [],
        }
        live_prepare = cdp.evaluate("""(async()=>{
          const headers={'X-AAG-Workspace-Path':location.pathname,'X-AAG-Workspace-Slug':'image-generator'};
          const session=await fetch('/api/aag-composer/image-generator/session',{credentials:'same-origin',headers}).then(r=>r.json());
          async function prepare(body){const response=await fetch('/api/aag-composer/image-generator/prepare',{method:'POST',credentials:'same-origin',headers:{...headers,'Content-Type':'application/json','X-AAG-CSRF':session.csrf},body:JSON.stringify(body)});return {status:response.status,data:await response.json()};}
          const base=%s;
          const manual=await prepare({...base,free_text:'Jerusalem street at sunset, very soft pastel colors',visual_family:'fine-art-traditional-media',visual_subfamily:'watercolor',atlas_selection_mode:'manual_browse'});
          const automatic=await prepare({...base,free_text:'make this look like an old travel poster',visual_family:'auto',visual_subfamily:'auto',atlas_selection_mode:'auto'});
          const plain=await prepare({...base,free_text:'a stone house on a quiet Jerusalem street',visual_family:'auto',visual_subfamily:'auto',atlas_selection_mode:'auto'});
          return {manual:{status:manual.status,atlas:manual.data.modelMessage?.includes('fine-art-traditional-media')&&manual.data.modelMessage?.includes('watercolor')},automatic:{status:automatic.status,atlas:automatic.data.modelMessage?.includes('vintage-travel-poster')},plain:{status:plain.status,atlas:plain.data.modelMessage?.includes('knowledge_modules')}};
        })()""" % json.dumps(payload_base, separators=(",", ":")))
        check("manual_selection_reaches_prompt_planning", live_prepare["manual"] == {"status": 200, "atlas": True}, live_prepare["manual"])
        check("auto_mode_routes_natural_language", live_prepare["automatic"] == {"status": 200, "atlas": True}, live_prepare["automatic"])
        check("plain_request_not_polluted", live_prepare["plain"] == {"status": 200, "atlas": False}, live_prepare["plain"])

        final_assets = cdp.evaluate("performance.getEntriesByType('resource').filter(x=>x.name.includes('/atlas-')).map(x=>({name:x.name,status:x.responseStatus||0}))")
        check("all_atlas_http_responses_successful", all(item["status"] in (0, 200) for item in final_assets), final_assets)
        image_failures = cdp.evaluate("[...document.querySelectorAll('[data-atlas-image-state]')].filter(x=>x.dataset.atlasImageState==='error'||(x.src&&x.complete&&x.naturalWidth===0)).length")
        check("no_broken_atlas_images", image_failures == 0, image_failures)
        runtime_errors = cdp.evaluate("window.__aagAtlasErrors || []")
        check("no_atlas_console_or_runtime_errors", not runtime_errors, runtime_errors)

        report = {
            "schema": "aag.visual-atlas.browser-acceptance.v1",
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "url": args.url,
            "browser": subprocess.check_output([args.browser, "--version"], text=True).strip(),
            "asset_transport": "authenticated same-origin fetch -> validated image response -> browser blob URL",
            "checks": checks,
            "passed": sum(item["pass"] for item in checks),
            "failed": sum(not item["pass"] for item in checks),
            "result": "PASS" if all(item["pass"] for item in checks) else "FAIL",
        }
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
