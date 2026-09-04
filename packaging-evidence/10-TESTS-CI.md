# Tests and CI

- Package doctor: PASS.
- Secret scans: PASS/PASS.
- Chess suite: PASS when run outside restricted socket sandbox (output reached 100%).
- Live AAG release doctor: PASS, including 125 Python tests.
- Node package tests: 159/163 pass. The four failures are the intentional Atlas pixel/product-bundle gates; Composer integration is 26/26 PASS and compatibility is 125/125 PASS when pointed at the external live Atlas.
- CI performs syntax, JSON and sanitization gates without models or private secrets.
