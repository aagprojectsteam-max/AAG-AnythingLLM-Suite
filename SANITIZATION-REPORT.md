# Sanitization Report

Status: PASS for the committed local package. Credential-format, literal-assignment, forbidden-file, staged-name and repository-size gates completed. Scanner-regex definitions themselves were manually adjudicated as code, not credentials.

Excluded by construction: AnythingLLM databases/conversations/documents/uploads; all `.env` files; API/proxy/GitHub tokens; certificates and private keys; browser/auth profiles; SQLite memory; runtime state, logs and evidence payloads; generated images/PDFs; backups; model/checkpoint/VAE/mmproj/MTP/embedding weights; third-party executables; compiled AnythingLLM public tree; Atlas reference/thumbnail pixels.

The final gate runs filename/extension exclusion, targeted credential-pattern scanning, entropy-like assignment scanning, Git object/staged-list review and repository-size checks. Findings must be zero or explicitly adjudicated before push.
