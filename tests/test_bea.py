"""BEA contract tests exercise real worker processes against a local API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from vgi.arguments import Arguments
from vgi.client import Client
from vgi.invocation import BindResponse

from bea_worker.api import parameters_json, validate_key
from bea_worker.auth import validate_http_auth
from bea_worker.functions import DataArgs, State, data_rows, number
from bea_worker.transport import ApiError
from test.mock_api.api_server import API_KEY, ApiServer


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[ApiServer]:
    server = ApiServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("BEA_API_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("BEA_ALLOW_INSECURE_HTTP", "1")
    monkeypatch.setenv("BEA_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("BEA_API_KEY", API_KEY)
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def bea_client(api: ApiServer) -> Iterator[Client]:
    with Client([sys.executable, "-m", "bea_worker.worker"], pool=None) as client:
        yield client


def table(client: Client, name: str, *positional: Any, secret_key: str | None = None, **named: Any) -> pa.Table:
    bound: list[BindResponse] = []
    batches = list(
        client.table_function(
            function_name=name,
            schema_path=["main"],
            secrets={"fixture": {"type": "bea_api", "api_key": secret_key}},
            bind_result_callback=bound.append,
            arguments=Arguments(
                positional=tuple(pa.scalar(v) for v in positional), named={k: pa.scalar(v) for k, v in named.items()}
            ),
        )
    )
    return pa.Table.from_batches(batches, schema=bound[-1].output_schema)


def test_discovery(bea_client: Client, api: ApiServer) -> None:
    assert table(bea_client, "datasets")["dataset_name"].to_pylist() == ["NIPA", "Regional", "GDPbyIndustry"]
    params = table(bea_client, "parameters", "NIPA")
    assert params["is_required"].to_pylist() == [True, True, True]
    assert params["multiple_accepted"].to_pylist() == [True, True, True]
    assert table(bea_client, "parameter_values", "NIPA", "TableName")["key"].to_pylist() == ["T10101"]
    values = table(bea_client, "parameter_values", "Regional", "LineCode", filters_json='{"TableName":"SAINC1"}')
    assert values["key"].to_pylist() == ["1", "2"]
    assert api.requests[-1]["method"] == "GetParameterValuesFiltered"
    assert api.requests[-1]["targetparameter"] == "LineCode"
    assert api.requests[-1]["tablename"] == "SAINC1"


def test_data_preserves_units_suppression_dimensions_and_notes(bea_client: Client, api: ApiServer) -> None:
    result = table(bea_client, "data", "Regional", '{"TableName":"SAINC1","Year":"2024"}')
    assert result["value"].to_pylist() == [1234.5, None, -42.25]
    assert result["value_text"].to_pylist() == ["1,234.50", "(D)", "-42.25"]
    assert result["unit_multiplier"].to_pylist() == [6, 6, 6]
    assert result["geo_fips"][0].as_py() == "01000"
    assert json.loads(result["attributes_json"][0].as_py())["NoteRef"] == "1"
    assert json.loads(result["notes_json"][0].as_py())[0]["NoteText"].startswith("Synthetic")
    assert "UTCProductionTime" in json.loads(result["metadata_json"][0].as_py())
    assert API_KEY not in str(result.to_pylist())
    assert api.requests[-1]["year"] == "2024"


def test_empty_cap_multi_result_and_notes(bea_client: Client) -> None:
    assert table(bea_client, "data", "EMPTY", '{"Year":"2024"}').num_rows == 0
    assert table(bea_client, "data", "NIPA", '{"Year":"2024"}', max_rows=1).num_rows == 1
    assert table(bea_client, "data", "MULTI", '{"Year":"2024"}').num_rows == 6
    assert table(bea_client, "notes", "EMPTY", '{"Year":"2024"}')["note_ref"].to_pylist() == ["1"]


def test_secret_precedes_environment(api: ApiServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEA_API_KEY", "ffffffff-ffff-ffff-ffff-ffffffffffff")
    with Client([sys.executable, "-m", "bea_worker.worker"], pool=None) as client:
        assert table(client, "datasets", secret_key=API_KEY).num_rows == 3
    assert api.requests[-1]["userid"] == API_KEY


def test_missing_credential_fails(api: ApiServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEA_API_KEY")
    with (
        Client([sys.executable, "-m", "bea_worker.worker"], pool=None) as client,
        pytest.raises(Exception, match="BEA_API_KEY"),
    ):
        table(client, "datasets")
    assert not api.requests


@pytest.mark.parametrize("dataset", ["ERROR", "MALFORMED", "BADVALUE", "REDIRECT"])
def test_invalid_upstream_fails_without_credentials(bea_client: Client, dataset: str) -> None:
    with pytest.raises(Exception) as error:
        table(bea_client, "data", dataset, '{"Year":"2024"}')
    assert API_KEY not in str(error.value)
    assert "example.invalid" not in str(error.value)


def test_rate_limit_retry(bea_client: Client, api: ApiServer) -> None:
    api.mode = "RETRY"
    assert table(bea_client, "datasets").num_rows == 3
    assert api.attempts == 2


@pytest.mark.parametrize(
    "value",
    [
        '{"UserID":"secret"}',
        '{"method":"GetData"}',
        '{"Year":2024,"year":2023}',
        "[]",
        '{"Year":true}',
        '{"Year":[]}',
        '{"Year":null}',
        '{"Year":""}',
        "invalid",
    ],
)
def test_invalid_parameters_rejected(value: str) -> None:
    with pytest.raises(ApiError):
        parameters_json(value)


@pytest.mark.parametrize("value", [None, "", "secret", "x" * 36])
def test_invalid_keys_are_redacted(value: str | None) -> None:
    with pytest.raises(ApiError) as error:
        validate_key(value)
    if value and value not in {"secret"}:
        assert value not in str(error.value)


@pytest.mark.parametrize(
    "value,expected",
    [("0", 0.0), ("-42.25", -42.25), ("1,234.5", 1234.5), ("(D)", None), ("(NA)", None), ("(L)", None), ("...", None)],
)
def test_numeric_contract(value: str, expected: float | None) -> None:
    assert number(value) == expected


@pytest.mark.parametrize("value", ["NaN", "inf", "1,2", "corrupt", "(12)"])
def test_corrupt_number_fails(value: str) -> None:
    with pytest.raises(ApiError):
        number(value)


def test_invalid_bind_does_not_call_api(bea_client: Client, api: ApiServer) -> None:
    with pytest.raises(Exception, match="Parameters must"):
        table(bea_client, "data", "NIPA", '{"UserID":"override"}')
    with pytest.raises(Exception, match="target parameter"):
        table(bea_client, "parameter_values", "NIPA", "Year", filters_json='{"year":"2024"}')
    assert not api.requests


def test_blank_data_value_is_missing_but_absent_value_is_corrupt() -> None:
    args = DataArgs(dataset="MNE", parameters_json='{"Year":"2024"}', max_rows=100)
    assert data_rows([{"Data": [{"DataValue": ""}]}], args)[0]["value"] is None
    with pytest.raises(ApiError, match="missing DataValue"):
        data_rows([{"Data": [{}]}], args)


def test_portable_state_contains_only_completion_marker() -> None:
    state = State(done=True)
    encoded = state.serialize_to_bytes()
    assert State.deserialize_from_bytes(encoded) == state
    assert API_KEY.encode() not in encoded


WORKER_TOKEN = "fixture-only-worker-token-with-32-characters"


@pytest.fixture()
def bea_http(api: ApiServer, tmp_path: Path) -> Iterator[str]:
    port_file = tmp_path / "port"
    environment = os.environ.copy()
    environment.update(VGI_BEARER_TOKENS=f"{WORKER_TOKEN}=analyst", VGI_PROXY_PROOF_MODE="off")
    environment.pop("VGI_JWT_ISSUER", None)
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "bea_worker.worker", "--http", "--port", "0", "--port-file", str(port_file)],
            stdout=log,
            stderr=log,
            env=environment,
        )
        try:
            deadline = time.monotonic() + 20
            while not port_file.exists():
                assert process.poll() is None, "HTTP worker exited before publishing its port"
                if time.monotonic() >= deadline:
                    pytest.fail("HTTP worker did not start within 20 seconds")
                time.sleep(0.05)
            location = "http://127.0.0.1:" + port_file.read_text().strip()
            while True:
                try:
                    with urllib.request.urlopen(location + "/health", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail("HTTP worker did not become ready")
                    time.sleep(0.05)
            yield location
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.seek(0)
            messages = log.read()
            assert WORKER_TOKEN not in messages
            assert API_KEY not in messages


@pytest.mark.parametrize("token", [None, "incorrect-token", API_KEY])
def test_http_rpc_rejects_missing_or_wrong_bearer(bea_http: str, api: ApiServer, token: str | None) -> None:
    headers = {"Authorization": "Bearer " + token} if token else {}
    request = urllib.request.Request(bea_http + "/__describe__", headers=headers)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 401
    exc.value.close()
    with (
        pytest.raises(Exception, match="authentication rejected"),
        Client(transport="http", base_url=bea_http, bearer_token=token, pool=None) as client,
    ):
        table(client, "datasets")
    assert api.requests == []


def test_http_bearer_authenticated_pages_and_secrets(bea_http: str, api: ApiServer) -> None:
    with Client(transport="http", base_url=bea_http, bearer_token=WORKER_TOKEN, pool=None) as client:
        result = table(client, "data", "NIPA", '{"Year":"2024"}', secret_key=API_KEY)
    assert result.num_rows == 3
    assert len(api.requests) == 1
    assert all(query["userid"] == API_KEY for query in api.requests)
    assert all(WORKER_TOKEN not in str(query) for query in api.requests)


def test_http_health_remains_available_without_bearer(bea_http: str) -> None:
    with urllib.request.urlopen(bea_http + "/health", timeout=5) as response:
        assert response.status == 200


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        ",",
        WORKER_TOKEN,
        "=analyst",
        f"{WORKER_TOKEN}=",
        "short=analyst",
        f"{WORKER_TOKEN}=analyst,",
        f"{WORKER_TOKEN}=analyst,{WORKER_TOKEN}=other",
    ],
)
def test_bad_bearer_configuration_fails_without_exposing_token(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("VGI_BEARER_TOKENS", value)
    with pytest.raises(ValueError, match="Invalid VGI_BEARER_TOKENS") as exc:
        validate_http_auth()
    assert WORKER_TOKEN not in str(exc.value)


def test_bearer_configuration_accepts_multiple_principals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VGI_BEARER_TOKENS", f"{WORKER_TOKEN}=analyst,{WORKER_TOKEN}-second=service")
    validate_http_auth()
    monkeypatch.delenv("VGI_BEARER_TOKENS")
    validate_http_auth()


def test_bad_bearer_configuration_stops_http_startup(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["VGI_BEARER_TOKENS"] = WORKER_TOKEN
    port_file = tmp_path / "port"
    result = subprocess.run(
        [sys.executable, "-m", "bea_worker.worker", "--http", "--port-file", str(port_file)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Invalid VGI_BEARER_TOKENS" in result.stderr
    assert WORKER_TOKEN not in result.stderr + result.stdout
    assert not port_file.exists()
