# vgi-bea

Query Bureau of Economic Analysis datasets from DuckDB through a Python VGI worker.
Supports national accounts, regional accounts, industry accounts, international
accounts, and other datasets returned by BEA's discovery API.

## Install and query

Python 3.13 or newer is required. Install a wheel from this repository's GitHub
Releases, or build the locked source checkout:

```sh
make build
```

Configure DuckDB 2.0.0 with Query Farm's signed VGI extension:

```sql
SET allow_extension_repositories = 'allowed';
CREATE EXTENSION REPOSITORY IF NOT EXISTS query_farm
    WITH PREFIX 'https://duckdb-extensions.query.farm';
INSTALL vgi FROM query_farm;
LOAD vgi FROM query_farm;
ATTACH 'bea' (TYPE vgi, LOCATION '/absolute/path/to/.venv/bin/vgi-bea-worker');
CREATE SECRET bea_key (TYPE bea_api, API_KEY 'your-36-character-BEA-key');

SELECT dataset_name, description FROM bea.main.datasets() ORDER BY dataset_name;
SELECT parameter_name, is_required, description
FROM bea.main.parameters('NIPA') ORDER BY parameter_name;
SELECT key, description FROM bea.main.parameter_values('NIPA', 'TableName') ORDER BY key;

SELECT time_period, line_number, line_description, value, unit, unit_multiplier
FROM bea.main.data('NIPA', '{"TableName":"T10101","Frequency":"A","Year":"2024"}')
ORDER BY time_period, line_number;
```

Get a free key from [BEA](https://apps.bea.gov/api/signup/). Use your secret manager
to supply the key; the SQL above shows the secret's shape. An applicable `bea_api`
secret takes precedence over `BEA_API_KEY` in the worker process environment.
Multiple applicable secrets are rejected. An invalid explicit key never silently
falls back to an environment key. Environment files are not loaded automatically.

## Functions

All functions are in `bea.main`. SQL NULL arguments are rejected. `max_rows`
defaults to 100,000 and accepts 1–1,000,000; it explicitly caps emitted rows.

| Function | Purpose |
| --- | --- |
| `datasets(max_rows := 100000)` | Dataset names and descriptions from GetDatasetList |
| `parameters(dataset, max_rows := 100000)` | Required parameters, defaults, multiple-value flags, and source metadata |
| `parameter_values(dataset, parameter, filters_json := '{}', max_rows := 100000)` | Allowed parameter values; nonempty filters select GetParameterValuesFiltered |
| `data(dataset, parameters_json, max_rows := 100000)` | Economic observations, units, common dimensions, all source attributes, and notes |
| `notes(dataset, parameters_json, max_rows := 100000)` | Source footnotes, including notes on an empty data result |

`parameters_json` and `filters_json` are objects with string or integer values.
Use comma-separated strings for multiple values. Duplicate names, including case
variants, and overrides of authentication, method, dataset, or result format are
rejected before a request. Not every BEA dataset implements filtered parameter
values; unsupported combinations produce an API error.

```sql
SELECT key, description
FROM bea.main.parameter_values('Regional', 'LineCode',
    filters_json := '{"TableName":"SAINC1"}') ORDER BY key;

SELECT geo_fips, geo_name, time_period, value, unit
FROM bea.main.data('Regional',
    '{"TableName":"SAINC1","LineCode":"1","GeoFIPS":"STATE","Year":"2024"}')
ORDER BY geo_fips, time_period;

-- Combine two data calls: each state's share of personal income vs. population.
WITH income AS (
    SELECT geo_fips, geo_name, round(100 * value / sum(value) OVER (), 2) AS income_share_pct
    FROM bea.main.data('Regional',
        '{"TableName":"SAINC1","LineCode":"1","GeoFips":"STATE","Year":"2023"}')
    WHERE geo_fips BETWEEN '01000' AND '56000'),
population AS (
    SELECT geo_fips, round(100 * value / sum(value) OVER (), 2) AS population_share_pct
    FROM bea.main.data('Regional',
        '{"TableName":"SAINC1","LineCode":"2","GeoFips":"STATE","Year":"2023"}')
    WHERE geo_fips BETWEEN '01000' AND '56000')
SELECT i.geo_fips, i.geo_name, i.income_share_pct, p.population_share_pct
FROM income AS i JOIN population AS p ON p.geo_fips = i.geo_fips
ORDER BY i.income_share_pct DESC NULLS LAST, i.geo_fips;
```

`notes` takes the same `parameters_json` as `data`, so the two join on `note_ref`
to put each observation beside the footnotes it cites (see the `data` examples).

### Data semantics

- `value` is a DOUBLE in the published units. It is **not rescaled** by
  `unit_multiplier`, the source's base-10 exponent. Avoid aggregating unlike units.
- `value_text` preserves the published numeric text, separators and suppression
  markers. Use this column for exact decimal conversion. Known missing or
  suppressed markers such as `(D)`, `(NA)`, `(L)` and `...` yield a NULL `value`.
  Unrecognized numeric text fails the query rather than silently becoming NULL.
- Periods remain strings, such as `2024` and `2024Q1`; they are not release dates.
  Geography identifiers remain strings to preserve leading zeroes.
- Common dimensions have dedicated columns. `attributes_json` retains the entire
  data row, including industry, country and other dataset-specific dimensions.
  `notes_json` retains source notes. `metadata_json` retains result-level metadata,
  including dimensions and production timestamps when BEA supplies them.
- Missing optional fields become NULL. Empty upstream arrays produce empty typed
  relations. Both object and array forms of BEA `Results` are supported.
- BEA's `Request` node echoes credentials; it is never returned in result columns.

BEA has no general pagination API. Each invocation reads one bounded response and
emits one Arrow batch. `max_rows` and a SQL `LIMIT` do not reduce the upstream
response size. Narrow the requested years, geography and tables to fit the
configured response bound. Queries read the current published dataset; this
worker does not invent historical vintages or replace ALFRED's revision history.

## HTTP authentication and deployment

The same executable supports stdio, Unix sockets and HTTP. Configure the SDK's
bearer authentication for an HTTP service:

```sh
export VGI_BEARER_TOKENS="$(openssl rand -hex 32)=analyst"
vgi-bea-worker --http --host 127.0.0.1 --port 8000
```

Use a secret manager for persistent tokens. Tokens contain 32–4096 characters;
configure multiple principals as `token1=principal1,token2=principal2`. An explicitly
empty or malformed configuration fails startup without printing its contents.
Omitting the variable leaves HTTP unauthenticated for local development. Each
configured principal can access the entire catalog. `/health` remains public.
Terminate TLS at your service ingress and avoid exposing an unauthenticated port.

```sql
INSTALL httpfs FROM core;
LOAD httpfs;
ATTACH 'bea' (TYPE vgi, LOCATION 'https://your-worker.example',
              bearer_token 'your-worker-bearer-token');
CREATE SECRET bea_key (TYPE bea_api, API_KEY 'your-BEA-key');
```

The worker bearer token and the BEA API key serve separate purposes. Bearer tokens
are never sent to BEA. API keys are resolved for each invocation and kept out of
continuation state. Errors omit upstream bodies, URLs and exception chains that
could expose query-string credentials. Worker stdout is reserved for Arrow RPC.

A nonroot Docker image is defined in `Dockerfile`; build with `docker build -t
vgi-bea .`. It starts HTTP on port 8000. Pass secrets through your deployment
platform. Use `docker run -i vgi-bea stdio` for containerized stdio.

## Operations

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `BEA_API_KEY` | unset | Fallback upstream API key |
| `BEA_TIMEOUT_SECONDS` | 30 | Per-request socket timeout, 0.1–120 seconds |
| `BEA_MAX_ATTEMPTS` | 4 | Total attempts per request, 1–6 |
| `BEA_RETRY_BUDGET_SECONDS` | 120 | Retry/read budget, 1–600 seconds |
| `BEA_MIN_INTERVAL_SECONDS` | 0.65 | Minimum spacing between request starts in one process |
| `BEA_MAX_RESPONSE_BYTES` | 16777216 | Maximum response size; 1 KiB–64 MiB |
| `BEA_API_BASE_URL` | `https://apps.bea.gov/api/data` | Operator-controlled HTTPS endpoint |
| `BEA_ALLOW_INSECURE_HTTP` | unset | Set to `1` only for loopback HTTP fixtures |

Network failures, HTTP 429 and HTTP 5xx are retried with bounded backoff and
jitter. `Retry-After` seconds and HTTP dates are honored; a wait beyond the budget
fails the query instead of retrying early. Redirects are rejected. Other HTTP
errors and BEA API errors fail immediately. The read budget is checked between
chunks; a blocking socket operation can extend wall time by its timeout.

Request spacing is process-local, not a global account quota. Coordinate
concurrency across replicas sharing a key. BEA documents request, data-volume
and error quotas. There is no automatic persistent data cache. Configured API
base URLs are trusted operator settings; do not let untrusted users change them.

## Verification and releases

```sh
make check          # Ruff, strict mypy, pydoclint, pytest, all SQL transports, metadata, stock DuckDB
make test-duckdb    # Offline stock DuckDB stdio and authenticated HTTP contracts
make smoke-live     # Optional official API check; requires BEA_API_KEY
make package        # Wheel and source distribution
```

Fixtures use synthetic data and a deliberately invalid public test key. Tests
cover request construction, units, suppression, empty results, multiple result
nodes, SQL secrets, bearer authentication, safe failures and bounded retries.
Original starter tests remain under `examples/starter_samples`; their original
assertions run alongside the product tests.

CI verifies Python 3.13 and 3.14, Haybarn SQL over stdio/launch/HTTP, stock DuckDB,
the installed wheel and Docker startup. Version tags gate GitHub releases on CI,
then attach a wheel, source archive, signatures and build provenance. The
worker is not published to PyPI. See `VERIFICATION.md` for measured results and
remaining live-service limitations.

Starter: [Query-farm/vgi-starter-python](https://github.com/Query-farm/vgi-starter-python),
revision `9cca9105474d6e0052b8e343c8ea21f3d1a7503c`, template 1.3.0.
Runtime: `vgi-python>=0.51.1,<0.52`; the checked lock resolves vgi-python 0.51.1
and vgi-rpc 0.53.1. SQL engines: stock DuckDB 2.0.0 and Haybarn 2.0.0rc11.

Protocol and dependency updates go through `make template-diff`; keep the SDK
and SQL extension compatible. Report issues through this repository's GitHub
issue tracker. Software license: BSD Zero Clause (`0BSD`), see `LICENSE`.
BEA source data and notes remain attributable to BEA; the software license does
not replace source-data terms.

API contract reference: [BEA API User Guide, April 2026](https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf).

## Notebooks

[Run the executed regional-data walkthrough](notebooks/README.md) to explore unit multipliers, population denominators and suppressed values using an explicit synthetic API fixture.
