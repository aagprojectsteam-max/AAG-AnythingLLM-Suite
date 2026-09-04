# Hardware Detection

`tools/hardware-detect.sh` reports OS, architecture, RAM, free disk, Intel GPU, Level Zero/SYCL indicators and NVIDIA detection. Core/PDF/Chess are CPU-capable. The packaged accelerated local-LLM path is Intel Arc/Intel GPU with the official llama.cpp SYCL build. CPU llama.cpp can be supplied by the user. NVIDIA is reported but no AAG NVIDIA local-LLM acceptance claim is made. Image acceleration is delegated to the user's compatible ComfyUI.

