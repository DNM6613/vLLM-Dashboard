"""Unified error envelope regression (backend/middleware.py).

Locks the ``{"error": {code, message, details, request_id}}`` response body
shape, the 400-503 status-to-code mapping, the fallback for unmapped codes,
and the presence / uniqueness of the ``X-Request-ID`` header.
"""
import asyncio
import json
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.middleware import (
    _HTTP_STATUS_CODES,
    _resolve_request_id,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)


def _request(path="/api/v1/x", method="GET", request_id="req-123"):
    state = SimpleNamespace(request_id=request_id)
    return SimpleNamespace(
        url=SimpleNamespace(path=path), method=method, state=state,
        scope={"type": "http"},
    )


def _body(resp: JSONResponse) -> dict:
    return json.loads(resp.body)


class TestErrorEnvelope(unittest.TestCase):
    def test_error_body_structure_is_uniform(self):
        resp = asyncio.run(
            http_exception_handler(_request(), HTTPException(status_code=404, detail="nope")))
        self.assertEqual(resp.status_code, 404)
        err = _body(resp)["error"]
        self.assertEqual(set(err.keys()), {"code", "message", "details", "request_id"})
        self.assertEqual(err["code"], "not_found")
        self.assertEqual(err["message"], "nope")
        self.assertIsNone(err["details"])
        self.assertEqual(err["request_id"], "req-123")

    def test_x_request_id_header_present_on_error(self):
        resp = asyncio.run(
            http_exception_handler(_request(), HTTPException(status_code=409, detail="dup")))
        self.assertEqual(resp.headers["x-request-id"], "req-123")

    def test_status_code_mapping_400_to_503(self):
        expected = {
            400: "bad_request", 401: "unauthorized", 403: "forbidden",
            404: "not_found", 405: "method_not_allowed", 409: "conflict",
            422: "validation_error", 500: "internal_error", 502: "bad_gateway",
            503: "service_unavailable",
        }
        self.assertEqual(_HTTP_STATUS_CODES, expected)
        for status, code in expected.items():
            resp = asyncio.run(
                http_exception_handler(_request(), HTTPException(status_code=status, detail="x")))
            self.assertEqual(resp.status_code, status)
            self.assertEqual(_body(resp)["error"]["code"], code, status)

    def test_unmapped_status_falls_back_to_http_prefix(self):
        resp = asyncio.run(
            http_exception_handler(_request(), HTTPException(status_code=418, detail="teapot")))
        self.assertEqual(_body(resp)["error"]["code"], "http_418")

    def test_structured_detail_is_preserved(self):
        detail = {"missing": ["model_id"]}
        resp = asyncio.run(
            http_exception_handler(_request(), HTTPException(status_code=400, detail=detail)))
        self.assertEqual(_body(resp)["error"]["details"], detail)

    def test_validation_error_envelope(self):
        exc = RequestValidationError([])
        resp = asyncio.run(validation_exception_handler(_request(), exc))
        self.assertEqual(resp.status_code, 422)
        err = _body(resp)["error"]
        self.assertEqual(err["code"], "validation_error")
        self.assertIn("errors", err["details"])

    def test_unhandled_exception_maps_to_500_without_leaking_details(self):
        resp = asyncio.run(
            unhandled_exception_handler(_request(), RuntimeError("secret-password-here")))
        self.assertEqual(resp.status_code, 500)
        body = json.dumps(_body(resp))
        self.assertEqual(_body(resp)["error"]["code"], "internal_error")
        self.assertNotIn("secret-password-here", body)

    def test_missing_request_id_omits_header_but_keeps_body_key(self):
        state = SimpleNamespace()  # no request_id attribute
        req = SimpleNamespace(url=SimpleNamespace(path="/x"), method="GET", state=state,
                              scope={"type": "http"})
        resp = asyncio.run(
            http_exception_handler(req, HTTPException(status_code=400, detail="x")))
        self.assertNotIn("x-request-id", resp.headers)
        self.assertIsNone(_body(resp)["error"]["request_id"])


class TestRequestIdResolution(unittest.TestCase):
    def test_valid_client_request_id_is_echoed(self):
        self.assertEqual(_resolve_request_id(
            SimpleNamespace(headers={"X-Request-ID": "client-id-1"})), "client-id-1")

    def test_generated_ids_are_unique_12_char_hex(self):
        first = _resolve_request_id(SimpleNamespace(headers={}))
        second = _resolve_request_id(SimpleNamespace(headers={}))
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 12)
        int(first, 16)  # must be hex

    def test_invalid_client_request_id_is_replaced(self):
        got = _resolve_request_id(
            SimpleNamespace(headers={"X-Request-ID": "bad id with spaces/..%"}))
        self.assertNotEqual(got, "bad id with spaces/..%")
        self.assertEqual(len(got), 12)


if __name__ == "__main__":
    unittest.main()
