# Installer Evidence

`install.sh` validates the immutable upstream revision, runs the doctor, supports six profiles, backs up every target, stages writes, records restore/remove actions and hashes, and traps errors into rollback. `update.sh`, `uninstall.sh`, and `rollback.sh` preserve user data and models.

