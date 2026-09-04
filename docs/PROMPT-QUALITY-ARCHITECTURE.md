# Prompt-quality architecture

The selected AnythingLLM workspace language model is the sole semantic interpreter and
creative prompt author. FLUX is the image generator. AAG is the governed validation and
routing boundary between them.

The trusted browser invocation supplies the authoritative user request and the workspace
model supplies one rich, professional, self-contained prompt. The canonical
`aag.prompt-quality.v1` gate checks bounded completeness, important explicit quantities,
requested visual mode, identity language where applicable, same-language lexical drift,
and structural prompt dimensions. Accepted prompt text is delivered unchanged to the
fixed workflow.

Under-specification is measured and recorded as prompt-quality provenance but does not
trigger a rejection/retry loop. The workspace model owns that quality outcome and its
prompt is routed unchanged. Unsafe text or a deterministically provable semantic conflict
still fails before parent/child job creation, scheduler acquisition, lease propagation, or
ComfyUI submission. AAG does not retry, append prose, construct a prompt, invoke another
language model, or maintain a hidden correction loop.

There are no creative templates, scene-specific prose writers, adjective packs, prompt
expansion tables, provider allow-lists, or provider/model special cases in this path.
