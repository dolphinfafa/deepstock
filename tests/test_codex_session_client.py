"""Local fake control socket; no real Codex turns are started."""
import base64
import hashlib
import json
import os
import socket
import struct
import threading

import pytest

from deepstock.codex_session_client import CodexClient


def test_regular_file_rejected_before_connect(tmp_path):
    if os.name == 'nt':
        pytest.skip('Unix owner-only control socket is server-only')
    path = tmp_path / 'not-a-socket'
    path.write_text('test')
    with pytest.raises(ValueError, match='must belong'):
        CodexClient(path)


@pytest.mark.skipif(os.name == 'nt', reason='Unix control socket is server-only')
def test_rpc_masking_handshake_notifications_ping_and_fragmented_response(tmp_path):
    path = tmp_path / 'ws'
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path)); server.listen(1); server.settimeout(5)
    errors, observed = [], []

    def serve():
        try:
            conn, _ = server.accept()
            with conn:
                conn.settimeout(5)
                request = bytearray()
                while b'\r\n\r\n' not in request:
                    request.extend(conn.recv(8192))
                key = next(line.split(b': ', 1)[1] for line in request.split(b'\r\n') if line.startswith(b'Sec-WebSocket-Key'))
                digest = base64.b64encode(hashlib.sha1(key + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                conn.sendall(b'HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: ' + digest + b'\r\n\r\n')
                def take(n):
                    result = b''
                    while len(result) < n:
                        result += conn.recv(n - len(result))
                    return result
                def receive():
                    first, second = take(2)
                    assert second & 128
                    size = second & 127
                    if size == 126: size = struct.unpack('!H', take(2))[0]
                    elif size == 127: size = struct.unpack('!Q', take(8))[0]
                    mask, value = take(4), take(size)
                    return first & 15, bytes(v ^ mask[i % 4] for i, v in enumerate(value))
                def frame(value, first=129):
                    body = value if isinstance(value, bytes) else json.dumps(value).encode()
                    conn.sendall(bytes([first, len(body)]) + body)
                _, first = receive()
                first = json.loads(first); observed.append(first)
                frame({'id': first['id'], 'result': {}})
                _, notice = receive(); observed.append(json.loads(notice))
                _, request = receive(); request = json.loads(request); observed.append(request)
                frame({'method': 'thread/status/changed', 'params': {}})
                value = json.dumps({'id': request['id'], 'result': {'status': 'idle'}}).encode()
                frame(value[:20], 1)
                frame(b'ping', 137)
                opcode, pong = receive()
                assert opcode == 10 and pong == b'ping'
                frame(value[20:], 128)
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=serve, daemon=True); worker.start()
    try:
        with CodexClient(path) as client:
            assert client.request('thread/read', {'threadId': 'synthetic'}) == {'status': 'idle'}
        worker.join(timeout=6)
        assert not worker.is_alive() and not errors
        assert [r['method'] for r in observed] == ['initialize', 'initialized', 'thread/read']
    finally:
        server.close()
