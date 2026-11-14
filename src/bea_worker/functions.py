"""Typed BEA discovery, economic accounts and source notes."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
from vgi.invocation import BindResponse
from vgi.metadata import FunctionExample
from vgi.table_function import BindParams, OutputCollector, ProcessParams, TableFunctionGenerator, init_single_worker
from vgi_rpc import ArrowSerializableDataclass

from .api import SECRET_TYPE, call, credential, parameters_json, records
from .meta import column, function_meta
from .transport import ApiError

DATASETS = pa.schema(
    [
        column("dataset_name", pa.string(), "BEA dataset identifier used in subsequent calls.", nullable=False),
        column("description", pa.string(), "BEA's description of this dataset."),
    ]
)
PARAMETERS = pa.schema(
    [
        column("dataset_name", pa.string(), "Requested BEA dataset.", nullable=False),
        column("parameter_name", pa.string(), "Name to use in parameters_json.", nullable=False),
        column("data_type", pa.string(), "Upstream parameter type."),
        column("description", pa.string(), "Meaning of the parameter."),
        column("is_required", pa.bool_(), "Whether BEA requires this parameter."),
        column("default_value", pa.string(), "BEA's default value, when declared."),
        column("multiple_accepted", pa.bool_(), "Whether comma-separated values are accepted."),
        column("all_value", pa.string(), "Special value requesting all entries, when supported."),
        column("attributes_json", pa.string(), "Complete source parameter metadata as JSON."),
    ]
)
VALUES = pa.schema(
    [
        column("dataset_name", pa.string(), "Requested BEA dataset.", nullable=False),
        column("parameter_name", pa.string(), "Requested parameter.", nullable=False),
        column("key", pa.string(), "Parameter value to pass to data.", nullable=False),
        column("description", pa.string(), "Source description of the value."),
        column("attributes_json", pa.string(), "All source attributes, preserving dataset-specific metadata."),
    ]
)
DATA = pa.schema(
    [
        column("dataset_name", pa.string(), "Requested BEA dataset.", nullable=False),
        column("time_period", pa.string(), "Source period, such as 2024, 2024Q1 or 2024M01; not a publication date."),
        column("value", pa.float64(), "Numeric value in published units; suppression and missing markers become NULL."),
        column(
            "value_text", pa.string(), "Unmodified published value text, preserving precision and suppression codes."
        ),
        column("unit", pa.string(), "Published CL_UNIT or UnitOfMeasure, if supplied."),
        column("unit_multiplier", pa.int64(), "Base-10 exponent UNIT_MULT; value is not automatically rescaled."),
        column("table_name", pa.string(), "TableName or TableID, if supplied."),
        column("line_number", pa.string(), "LineNumber or LineCode, if supplied."),
        column("line_description", pa.string(), "LineDescription or Description, if supplied."),
        column("geo_fips", pa.string(), "Geography code kept as text to preserve leading zeroes."),
        column("geo_name", pa.string(), "Geography name, if supplied."),
        column("note_ref", pa.string(), "Source note references for this row."),
        column("attributes_json", pa.string(), "Complete data row, including industry, country and other dimensions."),
        column("notes_json", pa.string(), "Source result notes qualifying these observations."),
        column(
            "metadata_json", pa.string(), "Result-level units, dimensions and production metadata; excludes Request."
        ),
    ]
)
NOTES = pa.schema(
    [
        column("dataset_name", pa.string(), "Requested BEA dataset.", nullable=False),
        column("note_ref", pa.string(), "Note identifier referenced by observations or result metadata."),
        column("note_text", pa.string(), "Source note text, including suppression definitions."),
    ]
)


# Composed examples: each combines two or more of this worker's functions in one
# query. Defined once and attached to the function that drives the query; the
# worker also publishes them in the schema-level vgi.example_queries list.
_NIPA_GDP_2024 = '{"TableName":"T10101","Frequency":"A","Year":"2024"}'
_SAINC1_STATE_2023 = '{{"TableName":"SAINC1","LineCode":"{line}","GeoFips":"STATE","Year":"2023"}}'
DATA_WITH_NOTES_SQL = (
    "SELECT TRY_CAST(d.line_number AS INTEGER) AS line_no, d.line_description, d.value, d.unit, n.note_text "
    f"FROM bea.main.data('NIPA', '{_NIPA_GDP_2024}') AS d "
    f"LEFT JOIN bea.main.notes('NIPA', '{_NIPA_GDP_2024}') AS n "
    "ON list_contains(string_split(d.note_ref, ','), n.note_ref) "
    "ORDER BY line_no, n.note_ref, n.note_text"
)
INCOME_VS_POPULATION_SHARE_SQL = (
    "WITH income AS (SELECT geo_fips, geo_name, round(100 * value / sum(value) OVER (), 2) AS income_share_pct "
    f"FROM bea.main.data('Regional', '{_SAINC1_STATE_2023.format(line=1)}') "
    "WHERE geo_fips BETWEEN '01000' AND '56000'), "
    "population AS (SELECT geo_fips, round(100 * value / sum(value) OVER (), 2) AS population_share_pct "
    f"FROM bea.main.data('Regional', '{_SAINC1_STATE_2023.format(line=2)}') "
    "WHERE geo_fips BETWEEN '01000' AND '56000') "
    "SELECT i.geo_fips, i.geo_name, i.income_share_pct, p.population_share_pct, "
    "round(i.income_share_pct - p.population_share_pct, 2) AS share_gap_pct "
    "FROM income AS i JOIN population AS p ON p.geo_fips = i.geo_fips "
    "ORDER BY i.income_share_pct DESC NULLS LAST, i.geo_fips"
)

COMPOSED_DATA_EXAMPLES = [
    FunctionExample(
        sql=DATA_WITH_NOTES_SQL,
        description=(
            "Attach source footnote text to each 2024 real GDP growth line by joining data rows to notes "
            "on the note references they cite"
        ),
    ),
    FunctionExample(
        sql=INCOME_VS_POPULATION_SHARE_SQL,
        description=(
            "Compare each state's 2023 share of the 50-state-plus-DC personal income total (SAINC1 line 1) "
            "with its share of population (line 2) and the gap between them, largest income share first"
        ),
    ),
]


@dataclass(frozen=True, kw_only=True)
class BaseArgs:
    """A result cap, independent of BEA's bounded full-response request."""

    max_rows: Annotated[
        int,
        Arg(
            "max_rows",
            default=100000,
            ge=1,
            le=1000000,
            doc="Explicit cap on emitted rows; does not reduce BEA's response size.",
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class DatasetArgs(BaseArgs):
    """A discovered dataset identifier."""

    dataset: Annotated[
        str,
        Arg(
            0,
            pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$",
            doc="Dataset identifier from datasets, such as NIPA or Regional.",
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class ValueArgs(DatasetArgs):
    """A parameter and optional dependent filters."""

    parameter: Annotated[
        str, Arg(1, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$", doc="Parameter name from parameters, such as TableName.")
    ]
    filters_json: Annotated[
        str,
        Arg(
            "filters_json",
            default="{}",
            doc=(
                "JSON object of other dataset parameters. Nonempty filters use "
                "GetParameterValuesFiltered, supported by selected datasets."
            ),
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class DataArgs(DatasetArgs):
    """Dataset-specific parameters encoded as a JSON object."""

    parameters_json: Annotated[
        str,
        Arg(
            1,
            doc=(
                "JSON object of dataset parameters, e.g. TableName, Frequency and Year. Use "
                "comma-separated strings for multiple values."
            ),
        ),
    ]


@dataclass(kw_only=True)
class State(ArrowSerializableDataclass):
    """Portable completion marker; no credentials or payloads are serialized."""

    done: bool = False


def text(row: dict[str, Any], *keys: str) -> str | None:
    """Read optional scalar metadata without stringifying complex objects."""
    for key in keys:
        value = row.get(key)
        if value is not None:
            if type(value) not in (str, int, float):
                raise ApiError(f"BEA response has invalid {key}")
            return str(value)
    return None


def required(row: dict[str, Any], *keys: str) -> str:
    """Require a nonempty identifying attribute."""
    value = text(row, *keys)
    if not value:
        raise ApiError("BEA response is missing a required identifying attribute")
    return value


def flag(row: dict[str, Any], key: str) -> bool | None:
    """Convert a source zero/one flag without silently accepting malformed values."""
    value = text(row, key)
    if value is None:
        return None
    if value not in {"0", "1"}:
        raise ApiError(f"BEA response has invalid {key}")
    return value == "1"


def number(value: str) -> float | None:
    """Parse published numbers and known BEA suppression/missing markers."""
    clean = value.strip()
    if clean in {"", "(D)", "(NA)", "(L)", "(NM)", "--", "---", "...", "*", "(S)"}:
        return None
    try:
        if re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", clean) is None:
            raise ValueError
        result = float(clean.replace(",", ""))
        if not math.isfinite(result):
            raise ValueError
        return result
    except ValueError:
        raise ApiError("BEA response contains an unrecognized numeric value") from None


def data_rows(nodes: list[dict[str, Any]], args: DataArgs) -> list[dict[str, Any]]:
    """Normalize common dimensions while retaining every source row and footnote."""
    result: list[dict[str, Any]] = []
    for node in nodes:
        notes = node.get("Notes", [])
        if not isinstance(notes, list):
            raise ApiError("BEA response has invalid Notes")
        metadata = {k: v for k, v in node.items() if k not in {"Data", "Notes"}}
        for row in records(node, "Data"):
            raw_value = text(row, "DataValue")
            if raw_value is None:
                raise ApiError("BEA response is missing DataValue")
            multiplier = text(row, "UNIT_MULT")
            if multiplier is not None and re.fullmatch(r"-?\d{1,3}", multiplier) is None:
                raise ApiError("BEA response has invalid UNIT_MULT")
            result.append(
                {
                    "dataset_name": args.dataset,
                    "time_period": text(row, "TimePeriod", "Year"),
                    "value": number(raw_value),
                    "value_text": raw_value,
                    "unit": text(row, "CL_UNIT", "UnitOfMeasure") or text(node, "UnitOfMeasure"),
                    "unit_multiplier": int(multiplier) if multiplier is not None else None,
                    "table_name": text(row, "TableName", "TableID"),
                    "line_number": text(row, "LineNumber", "LineCode"),
                    "line_description": text(row, "LineDescription", "Description"),
                    "geo_fips": text(row, "GeoFips", "GeoFIPS"),
                    "geo_name": text(row, "GeoName"),
                    "note_ref": text(row, "NoteRef") or text(node, "NoteRef"),
                    "attributes_json": json.dumps(row, ensure_ascii=False, allow_nan=False),
                    "notes_json": json.dumps(notes, ensure_ascii=False, allow_nan=False),
                    "metadata_json": json.dumps(metadata, ensure_ascii=False, allow_nan=False),
                }
            )
    return result


@init_single_worker
class BeaFunction[T: BaseArgs](TableFunctionGenerator[T, State]):
    """One bounded API request per invocation, with no network work during binding."""

    FIXED_SCHEMA: ClassVar[pa.Schema]

    @classmethod
    def on_bind(cls, params: BindParams[T]) -> BindResponse:
        """Validate dependent parameters and request secret resolution."""
        args = params.args
        if isinstance(args, DataArgs) and not parameters_json(args.parameters_json):
            raise ApiError("parameters_json must contain dataset parameters; discover them with parameters")
        if isinstance(args, ValueArgs):
            filters = parameters_json(args.filters_json)
            if args.parameter.lower() in {key.lower() for key in filters}:
                raise ApiError("filters_json must not include the target parameter")
        params.secrets.get(SECRET_TYPE)
        return BindResponse(output_schema=cls.FIXED_SCHEMA)

    @classmethod
    def initial_state(cls, params: ProcessParams[T]) -> State:
        """Create the portable completion marker."""
        return State()

    @classmethod
    def fetch(cls, args: T, key: str) -> list[dict[str, Any]]:
        """Implement a concrete BEA method."""
        raise NotImplementedError

    @classmethod
    def process(cls, params: ProcessParams[T], state: State, out: OutputCollector) -> None:
        """Convert a bounded response to typed Arrow without persisting credentials."""
        if state.done:
            out.finish()
            return
        rows = cls.fetch(params.args, credential(params))[: params.args.max_rows]
        try:
            batch = pa.RecordBatch.from_pylist(rows, schema=params.output_schema)
        except (pa.ArrowException, ValueError, OverflowError):
            raise ApiError("BEA response cannot be represented by the declared schema") from None
        state.done = True
        out.emit(batch)


class Datasets(BeaFunction[BaseArgs]):
    """Discover the datasets currently offered by BEA."""

    FIXED_SCHEMA = DATASETS
    Meta = function_meta(
        "datasets",
        "Discover BEA datasets",
        "List current dataset identifiers and descriptions before choosing an economic account.",
        DATASETS,
        "SELECT dataset_name, description FROM bea.main.datasets() ORDER BY dataset_name",
    )

    @classmethod
    def fetch(cls, args: BaseArgs, key: str) -> list[dict[str, Any]]:
        """Call GetDatasetList."""
        return [
            {"dataset_name": required(row, "DatasetName"), "description": text(row, "DatasetDescription")}
            for node in call("GetDatasetList", {}, key)
            for row in records(node, "Dataset")
        ]


class Parameters(BeaFunction[DatasetArgs]):
    """Discover a dataset's required and optional parameters."""

    FIXED_SCHEMA = PARAMETERS
    Meta = function_meta(
        "parameters",
        "Discover dataset parameters",
        "Inspect required parameters, defaults and multiple-value support for a dataset.",
        PARAMETERS,
        ("SELECT parameter_name, is_required, description FROM bea.main.parameters('NIPA') ORDER BY parameter_name"),
    )

    @classmethod
    def fetch(cls, args: DatasetArgs, key: str) -> list[dict[str, Any]]:
        """Call GetParameterList and decode source flags."""
        return [
            {
                "dataset_name": args.dataset,
                "parameter_name": required(row, "ParameterName"),
                "data_type": text(row, "ParameterDataType"),
                "description": text(row, "ParameterDescription"),
                "is_required": flag(row, "ParameterIsRequired"),
                "default_value": text(row, "ParameterDefaultValue"),
                "multiple_accepted": flag(row, "MultipleAcceptedFlag"),
                "all_value": text(row, "AllValue"),
                "attributes_json": json.dumps(row, ensure_ascii=False),
            }
            for node in call("GetParameterList", {"DatasetName": args.dataset}, key)
            for row in records(node, "Parameter")
        ]


class ParameterValues(BeaFunction[ValueArgs]):
    """Discover available parameter values, optionally filtered by other dimensions."""

    FIXED_SCHEMA = VALUES
    Meta = function_meta(
        "parameter_values",
        "Discover parameter values",
        (
            "Retrieve allowed values. Nonempty filters_json selects GetParameterValuesFiltered; "
            "BEA supports this for selected datasets only."
        ),
        VALUES,
        (
            "SELECT key, description FROM bea.main.parameter_values('Regional', 'LineCode', "
            'filters_json := \'{"TableName":"SAINC1"}\') ORDER BY key'
        ),
    )

    @classmethod
    def fetch(cls, args: ValueArgs, key: str) -> list[dict[str, Any]]:
        """Select the correct discovery method and preserve dataset-specific attributes."""
        filters = parameters_json(args.filters_json)
        method = "GetParameterValuesFiltered" if filters else "GetParameterValues"
        params = {
            **filters,
            "DatasetName": args.dataset,
            "TargetParameter" if filters else "ParameterName": args.parameter,
        }
        return [
            {
                "dataset_name": args.dataset,
                "parameter_name": args.parameter,
                "key": required(row, "Key", args.parameter),
                "description": text(row, "Desc", "Description"),
                "attributes_json": json.dumps(row, ensure_ascii=False),
            }
            for node in call(method, params, key)
            for row in records(node, "ParamValue")
        ]


class Data(BeaFunction[DataArgs]):
    """Query any BEA dataset with an explicit parameter object."""

    FIXED_SCHEMA = DATA
    Meta = function_meta(
        "data",
        "Retrieve economic accounts",
        (
            "Retrieve economic observations for a dataset. Common dimensions are typed; "
            "attributes_json preserves every dataset-specific dimension. Units and multipliers "
            "are retained without rescaling."
        ),
        DATA,
        (
            "SELECT time_period, line_number, value, unit FROM bea.main.data('NIPA', "
            '\'{"TableName":"T10101","Frequency":"A","Year":"2024"}\') ORDER BY time_period, '
            "line_number"
        ),
    )

    Meta.examples = [*Meta.examples, *COMPOSED_DATA_EXAMPLES]

    @classmethod
    def fetch(cls, args: DataArgs, key: str) -> list[dict[str, Any]]:
        """Call GetData and normalize its result nodes."""
        return data_rows(
            call("GetData", {**parameters_json(args.parameters_json), "DatasetName": args.dataset}, key), args
        )


class Notes(BeaFunction[DataArgs]):
    """Retrieve source notes for the same parameters used by data."""

    FIXED_SCHEMA = NOTES
    Meta = function_meta(
        "notes",
        "Read source notes",
        (
            "Retrieve source footnotes separately, including result-level notes when no "
            "observations are returned. Uses the same parameters_json as data."
        ),
        NOTES,
        (
            "SELECT note_ref, note_text FROM bea.main.notes('NIPA', "
            '\'{"TableName":"T10101","Frequency":"A","Year":"2024"}\') ORDER BY note_ref, note_text'
        ),
    )

    @classmethod
    def fetch(cls, args: DataArgs, key: str) -> list[dict[str, Any]]:
        """Call GetData and retain each result node's notes."""
        return [
            {"dataset_name": args.dataset, "note_ref": text(row, "NoteRef"), "note_text": text(row, "NoteText")}
            for node in call("GetData", {**parameters_json(args.parameters_json), "DatasetName": args.dataset}, key)
            for row in records({"Notes": node.get("Notes", [])}, "Notes")
        ]


FUNCTIONS: list[type[Any]] = [Datasets, Parameters, ParameterValues, Data, Notes]
