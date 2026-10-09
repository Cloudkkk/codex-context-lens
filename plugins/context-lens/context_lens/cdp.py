"""Small standard-library CDP transport for this plugin's local UI overlay.

Only loopback WebSockets are accepted. This is part of the delivered plugin,
not a general browser automation tool.
"""
import base64
import hashlib
import json
import os
import select
import socket
import struct
import time
from collections import deque
from urllib.parse import urlsplit


class CDP:
    def __init__(self, url):
        p = urlsplit(url)
        if p.scheme != "ws" or p.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Only local app debug connections are supported")
        self.sock = socket.create_connection((p.hostname, p.port or 80), timeout=3)
        self.sock.settimeout(3)
        self.buffer, self.events, self.counter = bytearray(), deque(), 0
        self.fragments = bytearray()
        key = base64.b64encode(os.urandom(16)).decode()
        path = p.path + (("?" + p.query) if p.query else "")
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {p.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(8192)
            if not chunk:
                raise ConnectionError("Debug connection closed during handshake")
            self.buffer.extend(chunk)
            if len(self.buffer) > 65536:
                raise ConnectionError("Invalid WebSocket handshake")
        head, rest = bytes(self.buffer).split(b"\r\n\r\n", 1)
        self.buffer = bytearray(rest)
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        headers = {line.decode().split(":", 1)[0].lower(): line.decode().split(":", 1)[1].strip() for line in head.split(b"\r\n")[1:] if b":" in line}
        if b" 101 " not in head.split(b"\r\n")[0] or headers.get("sec-websocket-accept", "") != expected:
            self.close()
            raise ConnectionError("App rejected the debug WebSocket")

    def close(self):
        self.sock.close()

    def send(self, data, opcode=1):
        mask = os.urandom(4)
        size = len(data)
        prefix = bytes([0x80 | opcode])
        prefix += bytes([0x80 | size]) if size < 126 else (bytes([0xFE]) + struct.pack("!H", size) if size < 65536 else bytes([0xFF]) + struct.pack("!Q", size))
        self.sock.sendall(prefix + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def receive(self, timeout=1):
        deadline = time.monotonic() + timeout
        while True:
            if len(self.buffer) >= 2:
                first, second = self.buffer[:2]
                size, pos = second & 127, 2
                extra = 2 if size == 126 else (8 if size == 127 else 0)
                if len(self.buffer) >= pos + extra:
                    if extra:
                        size = int.from_bytes(self.buffer[pos:pos + extra], "big")
                        pos += extra
                    if size > 32_000_000:
                        raise ConnectionError("Debug message exceeds size limit")
                    mask_size = 4 if second & 128 else 0
                    if len(self.buffer) >= pos + mask_size + size:
                        mask = self.buffer[pos:pos + mask_size]
                        payload = bytes(self.buffer[pos + mask_size:pos + mask_size + size])
                        del self.buffer[:pos + mask_size + size]
                        if mask:
                            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                        opcode = first & 15
                        if opcode == 8:
                            raise ConnectionError("App renderer closed")
                        if opcode == 9:
                            self.send(payload, 10)
                            continue
                        if opcode in (0, 1):
                            self.fragments.extend(payload)
                            if first & 128:
                                value = json.loads(self.fragments)
                                self.fragments.clear()
                                return value
                        continue
            wait = deadline - time.monotonic()
            if wait <= 0 or not select.select([self.sock], [], [], wait)[0]:
                return None
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("Debug connection ended")
            self.buffer.extend(chunk)

    def call(self, method, params=None):
        self.counter += 1
        request_id = self.counter
        self.send(json.dumps({"id": request_id, "method": method, "params": params or {}}, ensure_ascii=False).encode())
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            message = self.receive(max(.01, deadline - time.monotonic()))
            if message is None:
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message.get("result", {})
            if "method" in message:
                self.events.append(message)
        raise TimeoutError("App renderer did not respond")

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        if "exceptionDetails" in result:
            raise RuntimeError(str(result["exceptionDetails"]))
        return result.get("result", {}).get("value")
