"""LLM 适配代理的单元 + 端到端测试。"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STUB_PORT = 8019
PROXY_PORT = 8018


def load_proxy():
    spec = importlib.util.spec_from_file_location("llm_proxy_t", ROOT / "llm_adapter_proxy.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(module)
    return module


class RuleInjectionTest(unittest.TestCase):
    def setUp(self):
        self.proxy = load_proxy()

    def test_append_to_system(self):
        messages = [{"role": "system", "content": "base"}, {"role": "user", "content": "hi"}]
        out, changed = self.proxy.inject_rules(messages, "RULES", "[[mk]]")
        self.assertTrue(changed)
        self.assertTrue(out[0]["content"].startswith("base"))
        self.assertIn("RULES", out[0]["content"])
        self.assertEqual(len(out), 2)

    def test_idempotent(self):
        messages = [{"role": "system", "content": "base [[mk]] RULES"}]
        out, changed = self.proxy.inject_rules(messages, "[[mk]] RULES", "[[mk]]")
        self.assertFalse(changed)

    def test_insert_when_no_system(self):
        out, changed = self.proxy.inject_rules([{"role": "user", "content": "hi"}], "RULES", "[[mk]]")
        self.assertTrue(changed)
        self.assertEqual(out[0]["role"], "system")
        self.assertEqual(len(out), 2)


class RewriteTest(unittest.TestCase):
    def setUp(self):
        self.proxy = load_proxy()

    def _payload(self, content):
        return {"choices": [{"message": {"role": "assistant", "content": content}}]}

    def test_hallucinated_done_is_failed(self):
        messages = [{"role": "user", "content": "take off and photo"}]
        payload, note = self.proxy.rewrite_completion(self._payload("all done [TASK DONE]"), messages)
        self.assertEqual(note, "hallucinated_completion")
        content = payload["choices"][0]["message"]["content"]
        self.assertIn("[TASK FAILED]", content)

    def test_done_allowed_after_tool_result(self):
        messages = [
            {"role": "user", "content": "take off"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "ok"},
        ]
        payload, note = self.proxy.rewrite_completion(self._payload("done [TASK DONE]"), messages)
        self.assertIsNone(note)
        self.assertIn("[TASK DONE]", payload["choices"][0]["message"]["content"])

    def test_failed_wins_over_done(self):
        messages = [{"role": "user", "content": "x"}, {"role": "tool", "content": "ok"}]
        payload, note = self.proxy.rewrite_completion(
            self._payload("blocked [TASK FAILED] ... [TASK DONE]"), messages)
        self.assertEqual(note, "removed_conflicting_task_done")
        content = payload["choices"][0]["message"]["content"]
        self.assertNotIn("TASK DONE", content.upper())
        self.assertIn("[TASK FAILED]", content)

    def test_tool_message_counting(self):
        messages = [
            {"role": "user", "content": "a"},
            {"role": "tool", "content": "1"},
            {"role": "tool", "content": "2"},
            {"role": "user", "content": "b"},
        ]
        self.assertEqual(self.proxy.tool_results_since_last_user(messages), 0)
        self.assertEqual(self.proxy.tool_results_since_last_user(messages[:2]), 1)


class _Stub(BaseHTTPRequestHandler):
    received = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode())
        _Stub.received.append(body)
        # 回一个"没有工具调用却报完成"的响应，供代理纠正
        out = {"choices": [{"message": {"role": "assistant",
                                        "content": "All done. [TASK DONE]"}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class EndToEndTest(unittest.TestCase):
    def test_rules_injected_and_response_rewritten(self):
        proxy = load_proxy()
        proxy.Handler.upstream = f"http://127.0.0.1:{STUB_PORT}"
        proxy.Handler.rules_text = "[[mk]] RULE-A"
        proxy.Handler.guard_done = True
        stub = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), _Stub)
        server = ThreadingHTTPServer(("127.0.0.1", PROXY_PORT), proxy.Handler)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            body = json.dumps({
                "model": "Qwen3-8B",
                "messages": [{"role": "system", "content": "base"},
                             {"role": "user", "content": "do it"}],
                "stream": False,
            }).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{PROXY_PORT}/v1/chat/completions",
                                         data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
            # 规则注入到上游请求
            sent = _Stub.received[-1]
            system_content = sent["messages"][0]["content"]
            self.assertIn("RULE-A", system_content)
            # 响应被纠正
            content = result["choices"][0]["message"]["content"]
            self.assertIn("[TASK FAILED]", content)
        finally:
            stub.shutdown()
            server.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
