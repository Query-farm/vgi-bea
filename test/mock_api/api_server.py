"""Synthetic BEA API fixture based on the official response envelopes."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

API_KEY = "00000000-0000-0000-0000-000000000000"  # Public, invalid fixture credential.
DATA = [
    {
        "TimePeriod": "2024",
        "DataValue": "1,234.50",
        "CL_UNIT": "USD",
        "UNIT_MULT": "6",
        "GeoFips": "01000",
        "GeoName": "Fixture state",
        "LineNumber": "1",
        "TableName": "T10101",
        "NoteRef": "1",
    },
    {
        "TimePeriod": "2024",
        "DataValue": "(D)",
        "CL_UNIT": "USD",
        "UNIT_MULT": "6",
        "GeoFips": "02000",
        "GeoName": "Suppressed state",
        "LineNumber": "2",
    },
    {
        "TimePeriod": "2023",
        "DataValue": "-42.25",
        "CL_UNIT": "USD",
        "UNIT_MULT": "6",
        "GeoFips": "01000",
        "LineNumber": "1",
    },
]


class ApiServer(ThreadingHTTPServer):
    """A local server with inspectable requests and controlled failure injection."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests: list[dict[str, str]] = []
        self.mode = ""
        self.attempts = 0


class Handler(BaseHTTPRequestHandler):
    """Serve documented BEA envelopes without logging credential-bearing URLs."""

    server: ApiServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def send_json(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        query = {k.lower(): v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
        self.server.requests.append(query)
        self.server.attempts += 1
        key = query.get("userid", "")
        if key != API_KEY:
            self.send_json(
                200, {"BEAAPI": {"Results": {"Error": {"APIErrorCode": "1", "APIErrorDescription": "Bad key " + key}}}}
            )
            return
        mode = self.server.mode or query.get("datasetname", "")
        if mode == "REDIRECT":
            self.send_json(302, {}, {"Location": "https://example.invalid/" + key})
            return
        if mode == "RETRY" and self.server.attempts == 1:
            self.send_json(429, {"error": key}, {"Retry-After": "0"})
            return
        if mode == "ERROR":
            self.send_json(200, {"BEAAPI": {"Results": {"Error": {"APIErrorCode": "4", "APIErrorDescription": key}}}})
            return
        if mode == "MALFORMED":
            self.send_json(200, {"BEAAPI": {"Results": {"Data": "invalid"}}})
            return
        method = query.get("method")
        result: dict[str, Any]
        if method == "GetDatasetList":
            result = {
                "Dataset": [
                    {"DatasetName": name, "DatasetDescription": "Synthetic " + name}
                    for name in ("NIPA", "Regional", "GDPbyIndustry")
                ]
            }
        elif method == "GetParameterList":
            result = {
                "Parameter": [
                    {
                        "ParameterName": name,
                        "ParameterDataType": "String",
                        "ParameterDescription": "Fixture " + name,
                        "ParameterIsRequired": "1",
                        "MultipleAcceptedFlag": "1",
                        "AllValue": "ALL",
                    }
                    for name in ("TableName", "Year", "Frequency")
                ]
            }
        elif method in {"GetParameterValues", "GetParameterValuesFiltered"}:
            parameter = query.get("parametername", query.get("targetparameter", ""))
            result = (
                {"ParamValue": [{"Key": "1", "Desc": "Personal income"}, {"Key": "2", "Desc": "Population"}]}
                if parameter == "LineCode"
                else {"ParamValue": [{"TableName": "T10101", "Description": "Fixture GDP table"}]}
            )
        elif method == "GetData":
            result = {
                "Data": DATA if mode != "EMPTY" else [],
                "Notes": [{"NoteRef": "1", "NoteText": "Synthetic data. (D) means suppressed."}],
                "UnitOfMeasure": "Millions of dollars",
                "UTCProductionTime": "2026-01-01T12:00:00",
                "Dimensions": [{"Name": "DataValue", "IsValue": "1"}],
            }
            if mode == "BADVALUE":
                result["Data"] = [{"DataValue": "broken"}]
            if mode == "MULTI":
                self.send_json(200, {"BEAAPI": {"Results": [result, result]}})
                return
        else:
            self.send_json(400, {"error": "Unsupported fixture method"})
            return
        self.send_json(
            200,
            {
                "BEAAPI": {
                    "Request": {"RequestParam": [{"ParameterName": "USERID", "ParameterValue": key}]},
                    "Results": result,
                }
            },
        )


if __name__ == "__main__":
    server = ApiServer()
    print(f"PORT:{server.server_port}", flush=True)
    server.serve_forever()
