# Verification

Measured locally on 2026-11-13, macOS ARM64. No live BEA credential was available.
Deterministic data is synthetic and tests never depend on the official service.

| Check | Result |
| --- | --- |
| Published starter baseline `make check` | Passed before adaptation |
| `make check` | Passed |
| Ruff formatting and lint, strict mypy, pydoclint | Passed |
| pytest | 100 passed |
| Haybarn 2.0.0rc11, stdio | 130 assertions passed; no skipped test files |
| Haybarn 2.0.0rc11, launch | 130 assertions passed; no skipped test files |
| Haybarn 2.0.0rc11, HTTP | 94 assertions passed; no skipped test files |
| Stock DuckDB 2.0.0, stdio | 16 contract checks passed |
| Stock DuckDB 2.0.0, HTTP bearer authentication | 19 checks passed, including missing/wrong bearer rejection and separate BEA credential requirement |
| VGI metadata lint, strict `fail_on = "info"` | 100/100, zero errors, warnings or informational findings, L2 |
| Optional metadata documentation and agent review | 100/100, L3 semantic, using the local API fixture |
| Wheel and source archive metadata (`twine check`) | Passed |
| Isolated installed wheel, Python 3.13.16 | 16 stdio and 19 authenticated HTTP checks passed |
| Isolated locked source archive, Python 3.13.16 | 16 stdio and 19 authenticated HTTP checks passed |

The main local suite uses Python 3.14.4. Both packaged formats were installed in
temporary directories, then launched from outside the development checkout.
`python3 ci/verify-artifacts.py --python 3.13` repeats those artifact checks.
Verification JSON, checksums and command logs are generated under ignored `build/`.
CI repeats supported-platform checks and gates tagged releases.

Starter source: `https://github.com/Query-farm/vgi-starter-python`, template 1.3.0,
commit `9cca9105474d6e0052b8e343c8ea21f3d1a7503c`.
Resolved runtime: vgi-python 0.51.1, vgi-rpc 0.53.1.
Stock DuckDB uses the signed Query Farm VGI extension `e75482339d` on `osx_arm64`.
Haybarn is tested separately; it is not labeled stock DuckDB.

## Scope and limits

The tests cover all five functions, source suppression/blank values, precision
preservation, units and dimensions, empty and multiple result nodes, secret
precedence, credential-free continuation state, malformed responses, redirects,
size limits, request budgets, Retry-After, and HTTP bearer authentication.
Original starter assertions remain in the example suite.

`make smoke-live` is a separate opt-in check requiring a real `BEA_API_KEY`.
It has not been run locally. Fixture authentication tests verify worker behavior;
they do not establish that a particular user's BEA registration is valid.
BEA has no general pagination or transactional snapshot guarantee. Large queries
must be constrained to fit the configured response limit. Rate spacing applies
within one process; operators must coordinate shared-key concurrency across replicas.
