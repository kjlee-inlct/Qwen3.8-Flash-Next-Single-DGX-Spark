"""No-network tests for the public load harness and numeric profile artifact."""

import io
import json
import pathlib
import threading
import unittest
from unittest.mock import patch

import qualification_load as bench


class Stream(io.BytesIO):
    pass


def stream_bytes(done=True, finish="length"):
    events = [
        {"choices": [{"delta": {"content": "example"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": finish}],
         "usage": {"completion_tokens": 384}},
    ]
    data = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    if done:
        data += "data: [DONE]\n\n"
    return data.encode()


class HarnessTests(unittest.TestCase):
    def run_stream(self, data):
        with patch.object(bench.urllib.request.OpenerDirector, "open", return_value=Stream(data)):
            return bench.run_request("http://127.0.0.1:8888/v1/chat/completions",
                                     "qwen3.8-flash-next", 384, threading.Barrier(1))

    def test_complete_stream(self):
        result = self.run_stream(stream_bytes())
        self.assertTrue(result["ok"])
        self.assertTrue(result["done"])
        self.assertEqual(result["completion_tokens"], 384)

    def test_truncated_stream_is_not_success(self):
        self.assertFalse(self.run_stream(stream_bytes(done=False))["ok"])

    def test_error_finish_is_not_success(self):
        self.assertFalse(self.run_stream(stream_bytes(finish="content_filter"))["ok"])

    def test_stream_error(self):
        with self.assertRaisesRegex(RuntimeError, "error in the stream"):
            self.run_stream(b'data: {"error":{"message":"test"}}\n\n')

    def test_original_workload_parameters(self):
        with patch.object(bench.urllib.request.OpenerDirector, "open", return_value=Stream(stream_bytes())) as send:
            bench.run_request("http://127.0.0.1:8888/v1/chat/completions",
                              "qwen3.8-flash-next", 384, threading.Barrier(1))
            body = json.loads(send.call_args.args[0].data)
        self.assertEqual(body["temperature"], 0.2)
        self.assertEqual(body["max_tokens"], 384)
        self.assertFalse(body["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(body["messages"], [{"role": "user", "content": bench.PROMPT}])

    def test_redirect_refused(self):
        with self.assertRaisesRegex(RuntimeError, "redirected"):
            bench.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_proxy_environment_disabled(self):
        with patch.object(bench.urllib.request, "build_opener", wraps=bench.urllib.request.build_opener) as builder:
            self.run_stream(stream_bytes())
        self.assertEqual(builder.call_args.args[0].proxies, {})

    def test_profile_arithmetic(self):
        profile = json.loads((pathlib.Path(__file__).parent / "results" /
                              "2026-09-07-profile.json").read_text())
        for row in profile["current"] + [profile["burst_16"]]:
            self.assertEqual(row["ok"], row["requests"])
            self.assertEqual(row["total_completion_tokens"], row["requests"] * 384)
            self.assertAlmostEqual(row["aggregate_tokens_s"],
                                   row["total_completion_tokens"] / row["wall_s"], delta=0.02)
            self.assertAlmostEqual(row["per_stream_tokens_s"],
                                   row["aggregate_tokens_s"] / row["requests"], delta=0.01)


if __name__ == "__main__":
    unittest.main()
