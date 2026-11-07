"""A tiny scripted OpenAI-compatible server that stands in for a local model (Ollama/LM Studio)
during relay testing. Not a model: it asks for count_goods, then answers with placeholders.

    uv run python tools/fake_local_llm.py 11500
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")  # like OLLAMA_ORIGINS=*
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._send(200, {"data": [{"id": "fake-local"}]})
        self._send(404, {})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        has_tool_result = any(m.get("role") == "tool" for m in req["messages"])
        if not has_tool_result:
            msg = {"content": None, "tool_calls": [{"id": "c1", "type": "function",
                                                    "function": {"name": "count_goods", "arguments": "{}"}}]}
        else:
            msg = {"content": json.dumps({"summary": "Локальная модель: в декларации {{r1.goods_items}} товарных позиции.",
                                          "facts": [{"text": "Позиций: {{r1.goods_items}}", "kind": "xml_fact",
                                                     "evidence": ["r1"]}]}, ensure_ascii=False)}
        self._send(200, {"choices": [{"message": msg}]})

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 11500), H).serve_forever()
