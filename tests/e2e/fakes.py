# -*- coding: utf-8 -*-
"""
    Meta and the LLM of the end-to-end test, in one HTTP server with nothing but the standard library: it runs in a
    bare python image, and the test reads back what each one was asked.

        POST /v26.0/{phone_number_id}/messages   Meta's Cloud API: a text, a «read» or a «typing»
        POST /v1/chat/completions                the LLM: the classifier (a verdict, as JSON) or the responder (text)
        GET  /_seen                              what each one was asked, for the test
"""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = "Hola Ana, sí: abrimos mañana a las 9."
VERDICT = {"permitido": True, "motivo": "ninguno", "etiqueta": "pregunta", "confianza": 0.9, "entidades": []}
MESSAGES = re.compile(r"^/v\d{2}\.0/(\d+)/messages$")

seen = {"meta": [], "llm": []}
lock = threading.Lock()


class Fakes(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _answer(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/_seen":
            with lock:
                return self._answer(200, seen)
        return self._answer(404, {})

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        authorization = self.headers.get("Authorization", "")
        found = MESSAGES.match(self.path)
        if found:
            with lock:
                seen["meta"].append({"phoneId": found.group(1), "authorization": authorization, "body": body})
            if body.get("status") == "read":
                return self._answer(200, {"success": True})
            return self._answer(200, {"messaging_product": "whatsapp", "messages": [
                {"id": f"wamid.E2E{len(seen['meta'])}"}]})
        if self.path == "/v1/chat/completions":
            classifying = "response_format" in body
            with lock:
                seen["llm"].append({"kind": "classifier" if classifying else "responder",
                                    "authorization": authorization})
            content = json.dumps(VERDICT) if classifying else REPLY
            return self._answer(200, {"choices": [{"message": {"role": "assistant", "content": content},
                                                   "finish_reason": "stop"}],
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 10}})
        return self._answer(404, {"error": {"code": 100}})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Fakes).serve_forever()
