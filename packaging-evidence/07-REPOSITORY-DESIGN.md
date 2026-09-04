# Repository Design

One sanitized source repository separates image, chess, local-LLM, Ubuntu and AnythingLLM overlay authorities. Runtime/state and third-party assets remain external. The packaged tree is approximately 10MB instead of copying 49GB of production/history/models.

