# Model and Asset Setup

Set `AAG_MODEL_ROOT`, `COMFYUI_ROOT`, `LLAMACPP_ROOT` and `AAG_ATLAS_ROOT` in the user config. Run `tools/model-check.py` or `doctor.sh`. Ordinary image generation requires the FLUX.2 Klein checkpoint, Qwen encoder and FLUX.2 VAE named in `config/models.yaml`. Identity additionally needs PuLID/Juggernaut and evaluator assets. Local LLM needs a user-selected GGUF plus its matching optional mmproj/MTP sidecars.

Sources, licenses and hashes not proven by governed evidence remain unresolved. Obtain assets only from their official project/publisher, accept their terms and record local hashes. The installer never auto-downloads large files.

