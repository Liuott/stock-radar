#!/usr/bin/env python3
"""A股雷达 local web server.

No web framework: the standard library keeps the tool easy to start on a Windows
machine that only has Python, requests and numpy. It binds to localhost by
default and has no authentication, so do not expose it to the public internet.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import mimetypes
import socket
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.config import APP_NAME, APP_VERSION, DISCLAIMER, WEB_DIR
from app.providers.eastmoney import EastmoneyError
from app.services.market import MarketService

SERVICE: MarketService | None = None
MIME_OVERRIDES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}


def get_service() -> MarketService:
    global SERVICE
    if SERVICE is None:
        SERVICE = MarketService()
    return SERVICE


class Handler(BaseHTTPRequestHandler):
    server_version = f"StockLens/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- helpers
    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError):
            # Browsers close keep-alive sockets when a tab refreshes; it is not
            # an application error and should not print a traceback.
            pass

    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                # The browser navigated away or cancelled a poll; not an app error.
                return

    def send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def send_error_json(self, message: str, status: int = 500) -> None:
        self.send_json({"error": message, "asOf": None}, status)

    def _authorized(self) -> bool:
        password = getattr(self.server, "auth_password", None)
        if not password:
            return True
        username = getattr(self.server, "auth_user", "stocklens")
        expected = "Basic " + base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        presented = self.headers.get("Authorization", "")
        return hmac.compare_digest(presented, expected)

    def send_unauthorized(self) -> None:
        body = json.dumps(
            {"error": "需要登录。用户名默认 stocklens，密码见启动参数。"},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="StockLens", charset="UTF-8"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib API
        # Polling endpoints would flood the console; log only errors.
        try:
            status = str(args[1]) if len(args) > 1 else ""
        except Exception:  # noqa: BLE001
            status = ""
        if status.startswith(("4", "5")):
            sys.stderr.write(f"[http] {self.address_string()} {fmt % args}\n")

    # --------------------------------------------------------------- routes
    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        if not self._authorized():
            self.send_unauthorized()
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path == "/api/health":
                self.send_json(
                    {
                        "ok": True,
                        "app": APP_NAME,
                        "version": APP_VERSION,
                        "disclaimer": DISCLAIMER,
                    }
                )
                return
            if path == "/api/overview":
                force = query.get("refresh", ["0"])[0] == "1"
                self.send_json(get_service().get_overview(force=force))
                return
            if path == "/api/picks":
                profile = query.get("profile", ["balanced"])[0]
                force = query.get("refresh", ["0"])[0] == "1"
                self.send_json(get_service().get_picks(profile, force=force))
                return
            if path == "/api/watchlist":
                self.send_json(get_service().get_watchlist())
                return
            if path == "/api/live":
                self.send_json(get_service().get_live())
                return
            if path == "/api/leaders":
                force = query.get("refresh", ["0"])[0] == "1"
                self.send_json(get_service().get_leaders(force=force))
                return
            if path.startswith("/api/stock/"):
                code = path.rsplit("/", 1)[-1]
                profile = query.get("profile", ["balanced"])[0]
                self.send_json(get_service().get_stock(code, profile))
                return
            if path == "/api/search":
                text = query.get("q", [""])[0]
                self.send_json({"items": get_service().search(text)})
                return
            if path == "/api/method":
                self.send_json(get_service().method_card())
                return
            if path.startswith("/api/"):
                self.send_error_json("API 不存在", 404)
                return
            self.serve_static(path)
        except EastmoneyError as exc:
            self.send_error_json(f"行情源暂时不可用：{exc}", 502)
        except ValueError as exc:
            self.send_error_json(str(exc), 400)
        except BrokenPipeError:
            return
        except (ConnectionAbortedError, ConnectionResetError):
            return
        except Exception as exc:  # noqa: BLE001 - keep the local UI alive
            traceback.print_exc()
            self.send_error_json(f"服务内部错误：{exc}", 500)

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        if not self._authorized():
            self.send_unauthorized()
            return
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/watchlist":
                payload = self.read_json_body()
                self.send_json(get_service().add_watch(payload))
                return
            self.send_error_json("API 不存在", 404)
        except ValueError as exc:
            self.send_error_json(str(exc), 400)
        except EastmoneyError as exc:
            self.send_error_json(f"行情源暂时不可用：{exc}", 502)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.send_error_json(f"服务内部错误：{exc}", 500)

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib API
        if not self._authorized():
            self.send_unauthorized()
            return
        path = unquote(urlparse(self.path).path)
        try:
            if path.startswith("/api/watchlist/"):
                code = path.rsplit("/", 1)[-1]
                self.send_json(get_service().remove_watch(code))
                return
            self.send_error_json("API 不存在", 404)
        except ValueError as exc:
            self.send_error_json(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.send_error_json(f"服务内部错误：{exc}", 500)

    # -------------------------------------------------------------- static
    def serve_static(self, path: str) -> None:
        relative = path.lstrip("/") or "index.html"
        if relative.endswith("/"):
            relative += "index.html"
        candidate = (WEB_DIR / relative).resolve()
        try:
            candidate.relative_to(WEB_DIR.resolve())
        except ValueError:
            self.send_error_json("非法路径", 403)
            return
        if not candidate.exists() or not candidate.is_file():
            if "." not in Path(relative).name:
                candidate = WEB_DIR / "index.html"
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
        content_type = MIME_OVERRIDES.get(candidate.suffix.lower())
        if not content_type:
            content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        cache = "no-cache" if candidate.suffix.lower() in {".html", ".css", ".js"} else "public, max-age=3600"
        if "vendor" in candidate.parts:
            cache = "public, max-age=86400"
        try:
            body = candidate.read_bytes()
        except OSError:
            self.send_error_json("静态文件读取失败", 500)
            return
        self._send(200, body, content_type, cache)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    auth_user = "stocklens"
    auth_password: str | None = None


def lan_ip() -> str:
    """Best-effort LAN address for the printout; no packet is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def warm_cache() -> None:
    try:
        service = get_service()
        service.get_overview()
        service.get_picks("balanced")
        print("[warm] 行情与候选缓存已预热")
    except Exception as exc:  # noqa: BLE001
        print(f"[warm] 预热失败（首屏会自行加载）：{exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} local server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--user", default="stocklens", help="HTTP Basic 用户名")
    parser.add_argument("--password", default=None, help="HTTP Basic 密码；非本机监听建议必填")
    parser.add_argument(
        "--allow-no-auth",
        action="store_true",
        help="明确允许在非本机监听时不加密码（仅限可信反向代理/内网）",
    )
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--warm", action="store_true", help="启动后在后台预热行情缓存")
    args = parser.parse_args()

    loopback = args.host in {"127.0.0.1", "localhost", "::1"}
    if not loopback and not args.password and not args.allow_no_auth:
        parser.error(
            "绑定到非本机地址时必须设置 --password，或显式加 --allow-no-auth。"
            "没有鉴权的行情服务不应暴露到局域网/公网。"
        )

    server = Server((args.host, args.port), Handler)
    server.auth_user = args.user
    server.auth_password = args.password
    local_url = f"http://127.0.0.1:{args.port}"
    print("=" * 62)
    print(f"  {APP_NAME}  ·  StockLens  v{APP_VERSION}")
    print(f"  本机访问：{local_url}")
    if not loopback:
        print(f"  局域网/手机：http://{lan_ip()}:{args.port}")
        print("  注意：需要放行 Windows 防火墙端口，且只在可信网络使用。")
    print(f"  访问保护：{'HTTP Basic（用户 ' + args.user + '）' if args.password else '未启用（仅本机）'}")
    print("  数据源：东方财富公开行情（非 Level-2，可能延迟）")
    print("  仅用于个人研究，不构成投资建议；不接券商、不自动下单。")
    print("  Ctrl+C 停止服务")
    print("=" * 62)
    if args.warm:
        threading.Thread(target=warm_cache, daemon=True).start()
    if args.open:
        threading.Timer(1.2, lambda: webbrowser.open(local_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
