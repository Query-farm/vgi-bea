"""Invented BEA discovery and observations, with exact credential-free request audits."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from synthetic_api import GEOGRAPHIES, OBSERVATIONS, PUBLIC_TEST_KEY

NOTE = "Entire response is synthetic. (D) marks a deliberately suppressed income; it is not zero."
DATASETS = [
    {"DatasetName": "NIPA", "DatasetDescription": "Synthetic national-account discovery entry"},
    {"DatasetName": "Regional", "DatasetDescription": "Synthetic regional income and population"},
]
PARAMETERS = [
    {
        "ParameterName": name,
        "ParameterDataType": "string",
        "ParameterDescription": description,
        "ParameterIsRequired": "1",
        "MultipleAcceptedFlag": multi,
    }
    for name, description, multi in [
        ("TableName", "Table identifier", "0"),
        ("LineCode", "Measure identifier within the selected table", "1"),
        ("GeoFIPS", "Geography selector", "1"),
        ("Year", "Observation period, not release vintage", "1"),
    ]
]


def observations(year):
    """Retain the original notebook's invented response exactly."""
    rows = []
    for geography, (income, population) in OBSERVATIONS[year].items():
        for line, value, unit, exponent, label in [
            ("1", income, "USD", "6", "Personal income"),
            ("2", population, "Persons", "0", "Population"),
        ]:
            rows.append(
                {
                    "TimePeriod": year,
                    "GeoFips": geography,
                    "GeoName": GEOGRAPHIES[geography],
                    "LineCode": line,
                    "LineDescription": label,
                    "DataValue": value,
                    "CL_UNIT": unit,
                    "UNIT_MULT": exponent,
                    "TableName": "SAINC1",
                    "NoteRef": "S",
                }
            )
    return rows


class SyntheticBEA:
    """Serve explicit local routes, reject real credentials, restore inherited worker configuration."""

    def __init__(self):
        self.requests = []
        self._saved = {k: v for k, v in os.environ.items() if k.startswith("BEA_")}
        for key in self._saved:
            os.environ.pop(key)
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):  # noqa: N802
                url = urlsplit(self.path)
                values = parse_qs(url.query)
                if any(len(v) != 1 for v in values.values()):
                    self.send_error(400, "Duplicate request key")
                    return
                q = {k.lower(): v[0] for k, v in values.items()}
                common = {"userid": PUBLIC_TEST_KEY, "resultformat": "JSON"}
                method = q.get("method")
                result = None
                required = {**common, "method": method}
                if url.path != "/api/data" or self.headers.get("Authorization") is not None:
                    self.send_error(400, "Only the synthetic route is available")
                    return
                if method == "GetDatasetList":
                    result = {"Dataset": DATASETS}
                elif method == "GetParameterList":
                    required["datasetname"] = "Regional"
                    result = {"Parameter": PARAMETERS}
                elif method == "GetParameterValues":
                    required.update(datasetname="Regional", parametername="TableName")
                    result = {"ParamValue": [{"Key": "SAINC1", "Desc": "Synthetic personal income and population"}]}
                elif method == "GetParameterValuesFiltered":
                    required.update(datasetname="Regional", targetparameter="LineCode", tablename="SAINC1")
                    result = {
                        "ParamValue": [{"Key": "1", "Desc": "Personal income"}, {"Key": "2", "Desc": "Population"}]
                    }
                elif method == "GetData" and q.get("year") in OBSERVATIONS:
                    required.update(
                        datasetname="Regional", tablename="SAINC1", linecode="1,2", geofips="STATE", year=q["year"]
                    )
                    result = {"Data": observations(q["year"]), "Notes": [{"NoteRef": "S", "NoteText": NOTE}]}
                if result is None or q != required:
                    self.send_error(400, "Unsupported synthetic request; live keys are never accepted")
                    return
                fixture.requests.append(
                    {
                        "method": method,
                        "parameters": {k: v for k, v in q.items() if k not in common},
                        "response_rows": len(next(iter(result.values()))),
                        "public_test_key_only": True,
                    }
                )
                body = json.dumps({"BEAAPI": {"Results": result}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        os.environ.update(
            BEA_API_BASE_URL=f"http://127.0.0.1:{self._server.server_port}/api/data",
            BEA_ALLOW_INSECURE_HTTP="1",
            BEA_MIN_INTERVAL_SECONDS="0",
        )

    def close(self):
        """Stop the fixture and restore configuration without printing any previous values."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        for key in list(os.environ):
            if key.startswith("BEA_"):
                os.environ.pop(key)
        os.environ.update(self._saved)
