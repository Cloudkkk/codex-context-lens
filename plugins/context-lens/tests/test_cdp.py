import json
import socket
import struct
import unittest
from collections import deque
from context_lens.cdp import CDP


class TransportTests(unittest.TestCase):
    def test_fragmented_websocket_message_and_ping(self):
        client_sock, peer = socket.socketpair()
        client = CDP.__new__(CDP)
        client.sock, client.buffer, client.fragments, client.events = client_sock, bytearray(), bytearray(), deque()
        peer.sendall(b'\x89\x01p' + b'\x01\x06{"id":' + b'\x80\x021}')
        self.assertEqual(client.receive(1), {"id": 1})
        pong = peer.recv(100)
        self.assertEqual(pong[0] & 15, 10)
        self.assertTrue(pong[1] & 128)
        client.close(); peer.close()

    def test_loopback_connection_boundary(self):
        for url in ["ws://example.com/", "wss://127.0.0.1/", "ws://192.168.1.2/"]:
            with self.assertRaises(ValueError):
                CDP(url)


if __name__ == "__main__":
    unittest.main()
