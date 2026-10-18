"""Tests for the model chat SSE proxy (model_chat + POST /api/v1/models/chat)."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.config.remote_client import config_manager
from backend.config.server_config import ServerConfig
from backend.controller.state_machine import state_machine
from backend.main import app
from backend.service import model_chat as mc
from backend.service import model_service as ms


class _FakeChatResponse:
    """Stands in for httpx's streaming response context manager."""

    def __init__(self, status_code=200, lines=(), error_body=b""):
        self.status_code = status_code
        self._lines = list(lines)
        self._error_body = error_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return self._error_body


def _consume(agen):
    async def collect():
        return [x async for x in agen]
    return asyncio.run(collect())


class _ChatBase(unittest.TestCase):
    def _add_model(self):
        state_machine.add_model(
            model_id="chat-test-m", name="chat-test-m",
            path="/home/uforce/models/chat-test-m",
            config={"name": "chat-test-m", "path": "/home/uforce/models/chat-test-m"})

    def tearDown(self):
        for mid in list(state_machine.models):
            if mid.startswith("chat-test"):
                state_machine.remove_model(mid)
        with state_machine._lock:
            state_machine._dirty = False
            if state_machine._debounce_timer is not None:
                state_machine._debounce_timer.cancel()
                state_machine._debounce_timer = None


class TestChatPrecheck(_ChatBase):
    def _client_and_target(self, model_list):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": model_list}
        client = MagicMock()
        client.get = AsyncMock(return_value=response)
        target = MagicMock(base_url="http://10.131.1.7:8000", headers={"Authorization": "Bearer k"})
        return client, target

    def test_unknown_model_404(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(mc.chat_precheck("chat-test-none"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_unreachable_503(self):
        self._add_model()
        client = MagicMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        target = MagicMock(base_url="http://10.131.1.7:8000", headers={})
        with patch.object(ms, "get_http_client", return_value=client), \
             patch.object(ms, "resolve_api_target", return_value=target), \
             patch.object(config_manager, "get_config", return_value=ServerConfig()), \
             self.assertRaises(HTTPException) as ctx:
            asyncio.run(mc.chat_precheck("chat-test-m"))
        self.assertEqual(ctx.exception.status_code, 503)

    def test_empty_model_list_400(self):
        self._add_model()
        client, target = self._client_and_target([])
        with patch.object(ms, "get_http_client", return_value=client), \
             patch.object(ms, "resolve_api_target", return_value=target), \
             patch.object(config_manager, "get_config", return_value=ServerConfig()), \
             self.assertRaises(HTTPException) as ctx:
            asyncio.run(mc.chat_precheck("chat-test-m"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_model_not_loaded_400(self):
        self._add_model()
        client, target = self._client_and_target([{"id": "other-model"}])
        with patch.object(ms, "get_http_client", return_value=client), \
             patch.object(ms, "resolve_api_target", return_value=target), \
             patch.object(config_manager, "get_config", return_value=ServerConfig()), \
             self.assertRaises(HTTPException) as ctx:
            asyncio.run(mc.chat_precheck("chat-test-m"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_ok_returns_client_target_served_id(self):
        self._add_model()
        client, target = self._client_and_target([{"id": "chat-test-m"}])
        with patch.object(ms, "get_http_client", return_value=client), \
             patch.object(ms, "resolve_api_target", return_value=target), \
             patch.object(config_manager, "get_config", return_value=ServerConfig()):
            got_client, got_target, served = asyncio.run(mc.chat_precheck("chat-test-m"))
        self.assertIs(got_client, client)
        self.assertIs(got_target, target)
        self.assertEqual(served, "chat-test-m")


class TestStreamChat(_ChatBase):
    def _target(self):
        return MagicMock(base_url="http://10.131.1.7:8000", headers={})

    def test_passthrough_frames_verbatim(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"Hi"}}]}',
            'data: {"choices":[{"delta":{"content":" there"}}]}',
            'data: {"choices":[],"usage":{"completion_tokens":2}}',
            'data: [DONE]',
        ]
        client = MagicMock()
        client.stream = MagicMock(return_value=_FakeChatResponse(lines=lines))
        frames = _consume(mc.stream_chat(client, self._target(), "served",
                                         [{"role": "user", "content": "hi"}]))
        self.assertEqual(frames, [line + "\n\n" for line in lines])

    def test_non_200_yields_single_error_frame(self):
        resp = _FakeChatResponse(status_code=500, error_body=b'{"error":{"message":"bad prompt"}}')
        client = MagicMock()
        client.stream = MagicMock(return_value=resp)
        frames = _consume(mc.stream_chat(client, self._target(), "served",
                                         [{"role": "user", "content": "hi"}]))
        self.assertEqual(len(frames), 1)
        self.assertIn("500", frames[0])
        parsed = json.loads(frames[0][len("data: "):])
        self.assertIn("bad prompt", parsed["error"]["message"])

    def test_connect_error_yields_error_frame(self):
        client = MagicMock()
        client.stream = MagicMock(side_effect=httpx.ConnectError("no route"))
        frames = _consume(mc.stream_chat(client, self._target(), "served",
                                         [{"role": "user", "content": "hi"}]))
        self.assertEqual(len(frames), 1)
        parsed = json.loads(frames[0][len("data: "):])
        self.assertIn("no route", parsed["error"]["message"])


class TestChatEndpoint(_ChatBase):
    def setUp(self):
        self.client = TestClient(app)

    def test_stream_endpoint_passthrough(self):
        self._add_model()
        target = MagicMock(base_url="http://10.131.1.7:8000", headers={})
        fake_client = MagicMock()

        async def fake_stream(*_args, **_kwargs):
            yield "data: hello\n\n"
            yield "data: [DONE]\n\n"

        with patch.object(ms, "chat_precheck", new=AsyncMock(return_value=(fake_client, target, "served"))), \
             patch.object(ms, "stream_chat", new=fake_stream):
            resp = self.client.post("/api/v1/models/chat", json={
                "model_id": "chat-test-m",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 64,
            })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("data: hello", resp.text)
        self.assertIn("data: [DONE]", resp.text)

    def test_missing_messages_400(self):
        resp = self.client.post("/api/v1/models/chat", json={"model_id": "m"})
        self.assertEqual(resp.status_code, 400)

    def test_bad_role_400(self):
        resp = self.client.post("/api/v1/models/chat", json={
            "model_id": "m",
            "messages": [{"role": "tool", "content": "x"}],
        })
        self.assertEqual(resp.status_code, 400)

    def test_too_many_messages_400(self):
        resp = self.client.post("/api/v1/models/chat", json={
            "model_id": "m",
            "messages": [{"role": "user", "content": "x"}] * 21,
        })
        self.assertEqual(resp.status_code, 400)

    def test_bad_max_tokens_400(self):
        resp = self.client.post("/api/v1/models/chat", json={
            "model_id": "m",
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 0,
        })
        self.assertEqual(resp.status_code, 400)

    def test_precheck_failure_propagates_503(self):
        resp = self.client.post("/api/v1/models/chat", json={
            "model_id": "chat-test-none",
            "messages": [{"role": "user", "content": "x"}],
        })
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
