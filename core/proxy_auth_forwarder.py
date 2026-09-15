# -*- coding: utf-8 -*-
"""把带账密的 HTTP 代理收成本机无账密入口。

Playwright Firefox / Camoufox 第一次 goto 会带 Proxy-Authorization，页面自己触发的
后续 document 导航经常丢掉账密，上游返回 407 后进程直接退出。浏览器只连
127.0.0.1，由这里补鉴权。
"""
from __future__ import annotations

import base64
import logging
import socket
import threading
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

_HEADER_LIMIT = 65536
_IO_TIMEOUT = 30.0


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _recv_headers(sock: socket.socket, limit: int = _HEADER_LIMIT) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > limit:
            break
    return data


def _inject_proxy_authorization(raw: bytes, header_value: str) -> bytes:
    head, sep, rest = raw.partition(b"\r\n\r\n")
    if not sep:
        return raw
    if b"\nproxy-authorization:" in b"\n" + head.lower():
        return raw
    return head + b"\r\nProxy-Authorization: " + header_value.encode("ascii") + b"\r\n\r\n" + rest


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class ProxyAuthForwarder:
    def __init__(self, listen_sock: socket.socket, upstream_host: str, upstream_port: int, auth_header: str):
        self._sock = listen_sock
        self._upstream_host = upstream_host
        self._upstream_port = upstream_port
        self._auth_header = auth_header
        self._stop = threading.Event()
        self.port = listen_sock.getsockname()[1]
        self.local_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._accept_loop, name="camoufox-proxy-fwd", daemon=True)
        self._thread.start()

    @classmethod
    def start(cls, proxy_url: str) -> "ProxyAuthForwarder":
        parsed = urlparse(str(proxy_url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"只支持 HTTP 代理转发: {proxy_url!r}")
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        if not username:
            raise ValueError("HTTP 代理没有用户名，不需要本地转发")
        listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen.bind(("127.0.0.1", 0))
        listen.listen(128)
        listen.settimeout(0.3)
        return cls(
            listen,
            parsed.hostname,
            int(parsed.port or (443 if parsed.scheme == "https" else 80)),
            _basic_auth_header(username, password),
        )

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _handle_client(self, client: socket.socket) -> None:
        upstream = None
        try:
            client.settimeout(_IO_TIMEOUT)
            raw = _recv_headers(client)
            if not raw:
                return
            payload = _inject_proxy_authorization(raw, self._auth_header)
            head, sep, leftover = payload.partition(b"\r\n\r\n")
            upstream = socket.create_connection((self._upstream_host, self._upstream_port), timeout=_IO_TIMEOUT)
            upstream.settimeout(_IO_TIMEOUT)
            upstream.sendall(head + sep)
            first = _recv_headers(upstream)
            if first:
                client.sendall(first)
            if leftover:
                upstream.sendall(leftover)
            client.settimeout(None)
            upstream.settimeout(None)
            threading.Thread(target=_pipe, args=(client, upstream), daemon=True).start()
            _pipe(upstream, client)
        except Exception as exc:
            logger.debug("[Camoufox] 本地代理转发失败：%s: %s", type(exc).__name__, exc)
        finally:
            for sock in (client, upstream):
                if sock is None:
                    continue
                try:
                    sock.close()
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


def maybe_start_http_auth_forwarder(proxy_url: str | None) -> ProxyAuthForwarder | None:
    parsed = urlparse(str(proxy_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.username:
        return None
    return ProxyAuthForwarder.start(proxy_url or "")
