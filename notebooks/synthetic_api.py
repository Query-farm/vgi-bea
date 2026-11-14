"""A tiny BEA-shaped HTTP fixture with invented geography and observations."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PUBLIC_TEST_KEY = "00000000-0000-0000-0000-000000000000"
GEOGRAPHIES = {"00001": "Aster", "00002": "Briar", "00003": "Cedar", "00004": "Dune"}
# Income is in millions of USD; population is in persons. These are invented.
OBSERVATIONS = {
    "2023": {
        "00001": ("14,000.0", "240,000"),
        "00002": ("8,200.0", "100,000"),
        "00003": ("(D)", "490,000"),
        "00004": ("12,000.0", "295,000"),
    },
    "2024": {
        "00001": ("15,000.0", "250,000"),
        "00002": ("9,000.0", "100,000"),
        "00003": ("20,000.0", "500,000"),
        "00004": ("(D)", "300,000"),
    },
}


class Handler(BaseHTTPRequestHandler):
    """Answer only the explicit notebook fixture query on loopback."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep request URLs out of logs."""

    def do_GET(self) -> None:  # noqa: N802
        """Return synthetic rows in the documented BEA response envelope."""
        url = urlsplit(self.path)
        query = {key.lower(): values[0] for key, values in parse_qs(url.query).items()}
        expected = {
            "userid": PUBLIC_TEST_KEY,
            "method": "GetData",
            "datasetname": "Regional",
            "tablename": "SAINC1",
            "linecode": "1,2",
            "geofips": "STATE",
        }
        year = query.get("year", "")
        if url.path != "/api/data" or year not in OBSERVATIONS or any(query.get(k) != v for k, v in expected.items()):
            self.send_error(400, "Unsupported synthetic fixture request")
            return
        data = []
        for geography, (income, population) in OBSERVATIONS[year].items():
            for line, value, unit, exponent, label in [
                ("1", income, "USD", "6", "Personal income"),
                ("2", population, "Persons", "0", "Population"),
            ]:
                data.append(
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
        body = {
            "BEAAPI": {
                "Results": {
                    "Data": data,
                    "Notes": [
                        {
                            "NoteRef": "S",
                            "NoteText": (
                                "Entire response is synthetic. "
                                "(D) marks a deliberately suppressed income; it is not zero."
                            ),
                        }
                    ],
                }
            }
        }
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_fixture() -> ThreadingHTTPServer:
    """Start a disposable server at a freshly allocated loopback port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
