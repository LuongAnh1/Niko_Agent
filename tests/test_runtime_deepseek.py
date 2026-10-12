import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

import niko.runtime as runtime


class FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class DeepSeekRuntimeTests(unittest.TestCase):
    def test_call_deepseek_posts_chat_completion_request(self):
        response = FakeHttpResponse({"choices": [{"message": {"content": "Dạ em đây anh."}}]})

        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "test-key",
                "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
                "DEEPSEEK_MODEL": "deepseek-chat",
                "DEEPSEEK_TIMEOUT_SECONDS": "7",
                "NIKO_RUNTIME_LOG_ENABLED": "0",
            },
            clear=False,
        ), patch("niko.runtime.urlopen", return_value=response) as urlopen:
            answer = runtime.call_deepseek("hello")

        self.assertEqual(answer, "Dạ em đây anh.")
        request = urlopen.call_args.args[0]
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hello"}])

    def test_call_deepseek_requires_api_key(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "NIKO_RUNTIME_LOG_ENABLED": "0"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                runtime.call_deepseek("hello")

    def test_call_deepseek_reports_http_error(self):
        error = HTTPError(
            "https://api.deepseek.com/chat/completions",
            401,
            "Unauthorized",
            hdrs=None,
            fp=BytesIO(b'{"error":"bad key"}'),
        )

        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "bad-key", "NIKO_RUNTIME_LOG_ENABLED": "0"},
            clear=False,
        ), patch("niko.runtime.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                runtime.call_deepseek("hello")

    def test_call_deepseek_rejects_invalid_schema(self):
        response = FakeHttpResponse({"not_choices": []})

        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "test-key", "NIKO_RUNTIME_LOG_ENABLED": "0"},
            clear=False,
        ), patch("niko.runtime.urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "schema"):
                runtime.call_deepseek("hello")

    def test_deepseek_chat_url_accepts_full_chat_completion_url(self):
        self.assertEqual(
            runtime._deepseek_chat_url("https://example.test/v1/chat/completions"),
            "https://example.test/v1/chat/completions",
        )


if __name__ == "__main__":
    unittest.main()
