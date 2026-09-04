# Update

`update.sh` delegates to the transactional installer after confirming an existing manifest. It preserves `.env`, backs up targets, validates staged content, writes hashes and rolls back on failure. Never update from a dirty or untrusted package.

