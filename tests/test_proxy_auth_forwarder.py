# -*- coding: utf-8 -*-
import base64
import socket
import threading
import unittest

from core.proxy_auth_forwarder import ProxyAuthForwarder


def _read_headers(sock: socket.socket) -> bytes:
    data = b""
    sock.settimeout(3)
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    return data


class _AuthUpstream:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.requests: list[bytes] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def expected_auth(self) -> str:
        token = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def _loop(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        try:
            raw = _read_headers(client)
            self.requests.append(raw)
            auth = f"Proxy-Authorization: {self.expected_auth}".encode("ascii")
            if auth.lower() not in raw.lower():
                client.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
                return
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        finally:
            try:
                client.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


class ProxyAuthForwarderTests(unittest.TestCase):
    def test_connect_without_client_auth_still_sends_upstream_credentials(self):
        """Firefox 页面内跳转不会带代理账密；本地转发必须补上，否则上游 407 会把进程打崩。"""
        upstream = _AuthUpstream("openai.9", "secret-token")
        forwarder = None
        client = None
        try:
            forwarder = ProxyAuthForwarder.start(
                f"http://openai.9:secret-token@127.0.0.1:{upstream.port}"
            )
            client = socket.create_connection(("127.0.0.1", forwarder.port), timeout=3)
            client.sendall(b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n")
            reply = _read_headers(client)
            self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply[:200])
            self.assertEqual(len(upstream.requests), 1)
            self.assertIn(upstream.expected_auth.encode("ascii"), upstream.requests[0])
            self.assertNotIn(b"407", reply.split(b"\r\n", 1)[0])
        finally:
            if client is not None:
                client.close()
            if forwarder is not None:
                forwarder.stop()
            upstream.close()

    def test_connect_without_forwarder_gets_407(self):
        upstream = _AuthUpstream("openai.9", "secret-token")
        client = None
        try:
            client = socket.create_connection(("127.0.0.1", upstream.port), timeout=3)
            client.sendall(b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n")
            reply = _read_headers(client)
            self.assertTrue(reply.startswith(b"HTTP/1.1 407"), reply[:200])
        finally:
            if client is not None:
                client.close()
            upstream.close()


if __name__ == "__main__":
    unittest.main()
