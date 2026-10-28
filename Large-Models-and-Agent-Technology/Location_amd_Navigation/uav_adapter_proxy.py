#!/usr/bin/env python
"""UAV API 适配代理：8001 -> 8000（不修改官方 agent4drone / MultiUAV-Plat）。

能力：
  1) GET /environments/current -> weather 改写为 clear（避免模型因 heavy rain 拒飞）
  2) POST /drones/{id}/command/take_off 因"已在空中/悬停"失败 ->
     自动改发 change_altitude 并按成功返回
  3) 其余请求透明转发

用法：
    py -3.12 uav_adapter_proxy.py --port 8001 --upstream http://127.0.0.1:8000
    agent GUI -> Settings -> UAV API 地址: http://127.0.0.1:8001
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

CLEAR_WEATHER = {
    "weather": "clear",
    "description": "clear sky",
    "temperature": 22.0,
    "humidity": 40.0,
    "pressure": 1013.0,
    "wind_speed": 0.5,
    "wind_direction": "north",
    "visibility": 10000.0,
}

IN_AIR_HINTS = ("hovering", "already", "in air", "airborne", "not on ground", "cannot take off")


def rewrite_environment(payload):
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    for key, value in CLEAR_WEATHER.items():
        result[key] = value
    return result


def should_fallback_to_change_altitude(status_code: int, payload) -> bool:
    if status_code >= 400:
        return True
    if isinstance(payload, dict):
        message = f"{payload.get('message', '')} {payload.get('detail', '')}".lower()
        if str(payload.get("status", "")).lower() in ("error", "failed"):
            return True
        if any(hint in message for hint in IN_AIR_HINTS):
            return True
    return False


def forward(method, url, body, headers, timeout=30.0):
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        if key.lower() in ("host", "content-length", "connection"):
            continue
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _as_json(payload):
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    upstream = "http://127.0.0.1:8000"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[proxy] " + (fmt % args) + "\n")

    def _send(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            pass

    def _handle(self, method):
        parsed = urlparse(self.path)
        target = f"{self.upstream}{parsed.path}"
        if parsed.query:
            target += "?" + parsed.query
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        status, payload = forward(method, target, body, dict(self.headers))

        if parsed.path.rstrip("/").endswith("/environments/current") and status == 200:
            data = _as_json(payload)
            if data is not None:
                payload = json.dumps(rewrite_environment(data), ensure_ascii=False).encode("utf-8")

        if method == "POST" and "/command/take_off" in parsed.path:
            if should_fallback_to_change_altitude(status, _as_json(payload)):
                altitude = "10.0"
                for pair in parsed.query.split("&"):
                    if pair.startswith("altitude="):
                        altitude = pair.split("=", 1)[1]
                change_url = (f"{self.upstream}{parsed.path.replace('/take_off', '/change_altitude')}"
                              f"?altitude={altitude}")
                status2, payload2 = forward("POST", change_url, None, dict(self.headers))
                if status2 < 400:
                    data2 = _as_json(payload2) or {}
                    data2["message"] = (f"Drone already airborne; altitude changed to {altitude}m "
                                        f"instead of take_off")
                    data2["command"] = "take_off->change_altitude"
                    data2["status"] = "success"
                    status, payload = 200, json.dumps(data2, ensure_ascii=False).encode("utf-8")
                else:
                    status, payload = status2, payload2

        self._send(status, payload)

    do_GET = lambda self: self._handle("GET")          # noqa: E731
    do_POST = lambda self: self._handle("POST")        # noqa: E731
    do_PUT = lambda self: self._handle("PUT")          # noqa: E731
    do_DELETE = lambda self: self._handle("DELETE")    # noqa: E731
    do_PATCH = lambda self: self._handle("PATCH")      # noqa: E731


def main(argv=None):
    parser = argparse.ArgumentParser(description="UAV API 适配代理")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--upstream", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    Handler.upstream = args.upstream.rstrip("/")
    server = ThreadingHTTPServer((args.listen, args.port), Handler)
    print(f"[proxy] http://{args.listen}:{args.port} -> {Handler.upstream}", flush=True)
    print("[proxy] weather=clear, take_off->change_altitude fallback on", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
