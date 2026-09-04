# Update

`./update.sh` reads the installed version/profile, restores its verified stock baseline, invokes the new transactional installer, regenerates hashes and runs the installed doctor. On failure it reinstalls the previous package state. Update across an unlisted AnythingLLM commit is refused; it requires a new compatibility entry and acceptance evidence.

