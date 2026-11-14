# Executable SQL examples

| Notebook | Question |
| --- | --- |
| [Compare regional income on compatible scales](01_units_population_and_suppression.ipynb) | How do unit multipliers, population denominators and suppression affect a regional comparison? |

The real BEA worker parses an explicitly synthetic response from a disposable
loopback HTTP server. SQL applies the source unit exponent, pairs income with
population, calculates income per person and preserves suppressed income as
unavailable. Two charts compare total income with the population-adjusted ratio.

All values, geography names and leading-zero identifiers are invented. They are
not BEA observations or real geography codes. The response envelope adapts the
repository's existing test API fixture; the example demonstrates data handling,
not real regional economic performance. No live API mode is executed.

## Run from this directory

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then copy or
download the full `notebooks/` directory:

```sh
cd notebooks
uv sync --frozen
uv run --frozen python execute.py
```

The folder runs independently: no worker checkout, root `.venv`, build step or
real BEA key is required. Internet access is needed for packages, the community
VGI extension and the pinned worker Git revision. The notebook environment locks
published Haybarn 2.0.0rc11 and its visualization dependencies. The visible SQL
launcher is literal:

```text
uvx --python 3.13 --with vgi-python==0.51.1 --from git+https://github.com/Query-farm/vgi-bea@09bf4ef62c002c1cd4b1eea704cdff1c4821e392 vgi-bea-worker
```

The companion starts a fresh loopback server and sets the worker's supported
`BEA_API_BASE_URL` and `BEA_ALLOW_INSECURE_HTTP=1` options. Its public all-zero test
GUID is supplied through an explicit SQL secret and is valid only for the local
fixture. The fixture serves only the selected synthetic query and rejects
unsupported requests. It does not fetch or fall back to live data. Cleanup stops
the server and closes the SQL connection.

To upgrade the worker, change the visible Git/SDK pins and rerun the default and
experiment before replacing outputs. The root project's lock remains separate.

For an interactive notebook editor, select `notebooks/.venv/bin/python`, or
register the environment:

```sh
uv run --frozen python -m ipykernel install --user --name vgi-bea-notebooks --display-name "Python (vgi-bea notebooks)"
```

The runner uses a temporary kernel specification, starts from this directory and
saves the default only after all cells succeed.

## Year experiment

```sh
uv run --frozen python execute.py --parameters '{"YEAR": 2023}' --output-dir validation/year-2023
```

The default is `YEAR = 2024`; the only other supported source year is 2023.
Compare rankings and availability: the fixture deliberately suppresses a
different region's income in each year. The geography coverage must be matched
before interpreting a change in aggregate totals. The annual periods are not
publication dates or historical vintages. Reset `YEAR = 2024` for the default.

Overrides require a separate output folder. Copies include the server, helper,
assets, runner and locked environment files. Generated logs and extracted charts
are ignored; successful default outputs remain embedded in the primary notebook.
The full invented source data is in [synthetic_api.py](synthetic_api.py).
[STANDARD.md](STANDARD.md) describes the presentation conventions.
