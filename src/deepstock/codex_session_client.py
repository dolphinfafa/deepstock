"""Bounded JSON-RPC over an owner-only local Codex Unix control socket."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import struct


class CodexClient:
    def __init__(self, path: Path):
        metadata = path.stat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ValueError('Codex control socket must belong to the current user')
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(15)
        self.buffer = b''
        self.counter = 0
        try:
            self.socket.connect(str(path))
            key = base64.b64encode(os.urandom(16)).decode()
            request = (f'GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                       f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
            self.socket.sendall(request.encode())
            while b'\r\n\r\n' not in self.buffer:
                if len(self.buffer) > 16384:
                    raise ValueError('Oversized WebSocket handshake')
                self._read()
            header, self.buffer = self.buffer.split(b'\r\n\r\n', 1)
            lines = header.decode('ascii').split('\r\n')
            fields = {name.lower(): value for name, value in
                      (line.split(':', 1) for line in lines[1:] if ':' in line)}
            expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            if ' 101 ' not in lines[0] or fields.get('sec-websocket-accept', '').strip() != expected:
                raise ValueError('Codex WebSocket handshake failed')
            self.request('initialize', {'clientInfo': {'name': 'deepstock_session_mail', 'version': '0.1.0'},
                                        'capabilities': {'experimentalApi': True}})
            self.send({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise

    def _read(self):
        value = self.socket.recv(65536)
        if not value:
            raise ConnectionError('Codex control connection closed')
        self.buffer += value

    def _take(self, size):
        while len(self.buffer) < size:
            self._read()
        value, self.buffer = self.buffer[:size], self.buffer[size:]
        return value

    def _frame(self, body, opcode=1):
        mask = os.urandom(4)
        size = len(body)
        prefix = bytes([128 | opcode, 128 | min(size, 126)]) if size < 65536 else bytes([128 | opcode, 255])
        if size >= 65536:
            prefix += struct.pack('!Q', size)
        elif size >= 126:
            prefix += struct.pack('!H', size)
        self.socket.sendall(prefix + mask + bytes(x ^ mask[i % 4] for i, x in enumerate(body)))

    def send(self, value):
        self._frame(json.dumps(value, ensure_ascii=False).encode())

    def receive(self):
        chunks = bytearray()
        while True:
            first, second = self._take(2)
            size = second & 127
            if size == 126:
                size = struct.unpack('!H', self._take(2))[0]
            elif size == 127:
                size = struct.unpack('!Q', self._take(8))[0]
            if size > 4 * 1024 * 1024 or len(chunks) + size > 4 * 1024 * 1024:
                raise ValueError('Oversized Codex response')
            mask = self._take(4) if second & 128 else None
            body = self._take(size)
            if mask:
                body = bytes(x ^ mask[i % 4] for i, x in enumerate(body))
            opcode = first & 15
            if opcode == 8:
                raise ConnectionError('Codex WebSocket closed')
            if opcode == 9:
                self._frame(body, 10)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1):
                raise ValueError('Unsupported Codex WebSocket frame')
            chunks.extend(body)
            if first & 128:
                return json.loads(chunks)

    def request(self, method, params):
        self.counter += 1
        number = self.counter
        self.send({'jsonrpc': '2.0', 'id': number, 'method': method, 'params': params})
        for _ in range(1000):
            response = self.receive()
            if response.get('id') != number:
                continue
            if 'error' in response:
                # Avoid logging upstream messages, session text or credentials.
                raise RuntimeError(f"Codex RPC {method} failed: {response['error'].get('code')}")
            return response['result']
        raise RuntimeError('Codex response exceeded notification limit')

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
