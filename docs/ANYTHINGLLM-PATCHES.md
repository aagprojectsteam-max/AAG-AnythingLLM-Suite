# AnythingLLM Patches

`PATCH-MANIFEST.yaml` maps every observed server mount to its upstream path. Additive endpoint/command files are preferable extensions. Five replacement files remain upgrade-sensitive: chat index/handler, model-map index, the two Agent implementations, tool reranker and server index. Installation therefore requires the exact recorded upstream revision and backs up every target before replacement.

The production frontend was built from the recorded revision with Composer, Progress, ImageGenerationCard, HistoricalOutputs, AagImageCollection, PromptInput and ChatContainer overlays. The compiled upstream public tree is not redistributed. The precise overlay source capture is incomplete, so rebuilding that frontend is a documented release blocker rather than being misrepresented as reproducible.

