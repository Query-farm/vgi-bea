# BEA SQL notebooks

These executed notebooks use the released BEA v0.1.1 worker through Haybarn, with
consistent Query.Farm / VGI branding, SVG figures and an Open in Colab link in each
first cell. All data and discovery entries are explicitly synthetic.

- [Units, population and suppression](01_units_population_and_suppression.ipynb):
  preserve unit exponents, join population and compare available regional rankings.
  Change YEAR from 2024 to 2023 to expose a different suppression pattern.
- [Discovery and complete pairs](02_discovery_and_complete_pairs.ipynb): inspect
  datasets, parameter requirements and dependent line codes; retain qualifying
  notes and distinguish output row caps from upstream acquisition limits.
  Change ROW_CAP from 3 to 5 while the eight-row reference and HTTP bodies stay fixed.

## Run

Open either notebook using its Colab badge and Run all. The brief base-package
bootstrap downloads hash-verified companions and keeps compatible preloaded
plotting/kernel libraries. The visible ATTACH uses a release-tagged uvx launcher;
Python 3.13 and worker dependencies run in their own environment.

Locally, install uv, then run from this directory:

```sh
uv sync --locked
uv run --locked vgi-notebooks
uv run --locked vgi-notebooks 01_units_population_and_suppression.ipynb --parameters '{"YEAR":2023}' --output-dir artifacts/year-2023
uv run --locked vgi-notebooks 02_discovery_and_complete_pairs.ipynb --parameters '{"ROW_CAP":5}' --output-dir artifacts/cap-five
```

The shared [notebook standard](https://github.com/Query-farm/vgi-example-notebooks/blob/main/docs/standard.md)
and runner live in vgi-example-notebooks. The local publication environment includes
its runner extra; Colab installs only the base package. GitHub does not fold code
cells, so setup is short and connection SQL remains visible. Figures include native
SVG MIME output and committed content-addressed SVG links for GitHub.

## Data and coverage

The original eight-row regional fixtures remain in synthetic_api.py. bea_fixture.py
adds invented discovery metadata, exact request auditing and restoration of inherited
BEA configuration. The loopback server accepts only the all-zero public test GUID,
rejects other credentials and never forwards to BEA. Temporary SQL secrets are
explicitly removed. No live data, account or API key is used; the examples do not
claim current API contents or actual economic results.

[coverage.json](coverage.json) records all five named APIs: datasets, parameters,
parameter_values, data and notes. Actual release exports and the complete reviewed
current-source interface are retained separately under coverage/. Current main adds
metadata examples; the notebooks execute the release. Full response size, emitted
rows, suppression and absence are distinct. A capped prefix is not a representative
sample or a completeness guarantee.
