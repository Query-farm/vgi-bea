"""BEA credentials, request validation and response envelopes."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import pyarrow as pa
from vgi.catalog import SecretTypeSpec
from vgi.table_function import ProcessParams

from .transport import ApiError, Config, request_json

SECRET_TYPE = "bea_api"
API_SECRET_TYPES = [
    SecretTypeSpec(
        name=SECRET_TYPE,
        description="BEA API user key; obtain one from https://apps.bea.gov/api/signup/.",
        schema=pa.schema(
            [pa.field("api_key", pa.string(), metadata={"redact": "true", "vgi_doc": "Your BEA API user key."})],
            metadata={"vgi_doc_url": "https://apps.bea.gov/api/signup/"},
        ),
    )
]
RESERVED = {"userid", "method", "datasetname", "resultformat", "parametername", "targetparameter"}


def validate_key(key: str | None) -> str:
    """Validate a BEA GUID without disclosing it."""
    if not key:
        raise ApiError("BEA API key required: create a bea_api secret or set BEA_API_KEY")
    if re.fullmatch(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", key) is None:
        raise ApiError("BEA API key must be a 36-character GUID")
    return key


def credential(params: ProcessParams[Any]) -> str:
    """Resolve a DuckDB secret before the worker environment."""
    secrets = list(params.secrets.of_type(SECRET_TYPE))
    if len(secrets) > 1:
        raise ApiError("Multiple bea_api secrets matched; keep one applicable secret")
    key = None
    if secrets:
        scalar = secrets[0].get("api_key")
        key = scalar.as_py() if scalar is not None else None
    return validate_key(key if key is not None else os.environ.get("BEA_API_KEY"))


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate parameter names, including case variants."""
    result: dict[str, Any] = {}
    seen: set[str] = set()
    for key, value in pairs:
        if key.lower() in seen:
            raise ValueError("duplicate parameter")
        result[key] = value
        seen.add(key.lower())
    return result


def parameters_json(value: str) -> dict[str, str]:
    """Decode bounded dataset parameters without allowing credential overrides."""
    try:
        if len(value) > 20000:
            raise ValueError
        parsed = json.loads(value, object_pairs_hook=_unique)
        if not isinstance(parsed, dict) or len(parsed) > 50:
            raise ValueError
        for key, item in parsed.items():
            if (
                re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key) is None
                or key.lower() in RESERVED
                or type(item) not in (str, int)
                or not str(item)
                or len(str(item)) > 4000
                or any(ord(c) < 32 for c in str(item))
            ):
                raise ValueError
        return {key: str(item) for key, item in parsed.items()}
    except (ValueError, TypeError, RecursionError):
        raise ApiError(
            "Parameters must be a JSON object of unique dataset parameter names and string/integer values; "
            "credentials, method and dataset overrides are forbidden"
        ) from None


def call(method: str, params: dict[str, str], key: str) -> list[dict[str, Any]]:
    """Read only result nodes; never expose BEA's credential-echoing Request node."""
    query = {**params, "UserID": validate_key(key), "Method": method, "ResultFormat": "JSON"}
    body = request_json("", list(query.items()), Config.from_env())
    envelope = body.get("BEAAPI")
    if not isinstance(envelope, dict) or "Error" in envelope:
        raise ApiError("BEA returned an invalid envelope or API error")
    result = envelope.get("Results")
    nodes = result if isinstance(result, list) else [result]
    if not nodes:
        raise ApiError("BEA response is missing Results")
    validated: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise ApiError("BEA response is missing Results")
        if "Error" in node:
            error = node["Error"]
            code = error.get("APIErrorCode", "") if isinstance(error, dict) else ""
            safe_code = str(code) if re.fullmatch(r"[0-9]{1,6}", str(code)) else "unknown"
            raise ApiError(f"BEA API error {safe_code}; check dataset parameters and API key")
        validated.append(node)
    return validated


def records(node: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Require an explicitly present array of objects, including empty arrays."""
    rows = node.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ApiError(f"BEA response has invalid {key} records")
    return rows
