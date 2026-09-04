# Fresh Install

1. Install Git, Bash, Python 3 and Node.js.
2. Check out AnythingLLM at `07bd65f80b3d9ba3031ed7afb8786627326bd134`.
3. Clone this repository and run:

```bash
./install.sh --profile full --anythingllm-root /path/to/anything-llm --storage /path/to/storage --dry-run
./install.sh --profile full --anythingllm-root /path/to/anything-llm --storage /path/to/storage
./doctor.sh
```

Supply ComfyUI/models and an authorized Atlas pack for image completeness. Missing optional hardware/assets are reported, never silently downloaded. Docker-only users still need the matching source checkout because RC1 patches source files and does not mutate an opaque unknown container.

