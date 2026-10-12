"""Verified local WSS transport with one JSON-RPC response dispatcher."""

import asyncio
import itertools
import json
import ssl
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

CORTEX_URL = "wss://localhost:6868"
DEFAULT_CA = Path(__file__).parent / "certificates" / "rootCA.pem"


class CortexError(Exception):
    """Safe diagnostic text: never includes credentials or raw RPC payloads."""


class CortexDisconnected(CortexError):
    pass


class CortexRPCError(CortexError):
    def __init__(self, method, error):
        code = error.get('code')
        self.code = code if isinstance(code, int) else None
        self.rpc_message = str(error.get('message', ''))
        super().__init__(f"Cortex {method} failed (code {self.code}).")


def tls_context(ca_file=None):
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(ca_file or DEFAULT_CA))
    return context  # CERT_REQUIRED and check_hostname remain enabled


class CortexClient:
    _ids = itertools.count(1)

    def __init__(self, url=CORTEX_URL, ca_file=None, ssl_context=None):
        self.url = url
        self.ca_file = ca_file
        self.ssl_context = ssl_context
        self.websocket = None
        self.reader = None
        self.pending = {}
        self.events = asyncio.Queue(maxsize=256)
        self.limit = asyncio.Semaphore(32)

    async def connect(self):
        self.websocket = await connect(
            self.url, ssl=self.ssl_context or tls_context(self.ca_file),
            proxy=None, open_timeout=10, close_timeout=2,
            max_queue=16, max_size=1024 * 1024,
        )
        self.reader = asyncio.create_task(self._receive())

    def _event(self, message):
        if self.events.full():
            self.events.get_nowait()
        self.events.put_nowait(message)

    async def _receive(self):
        try:
            async for raw in self.websocket:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise CortexError("Invalid Cortex message.")
                entry = self.pending.get(message.get('id'))
                if entry:
                    future, method = entry
                    if not future.done():
                        if 'error' in message:
                            future.set_exception(CortexRPCError(method, message['error']))
                        elif 'result' in message:
                            future.set_result(message['result'])
                        else:
                            future.set_exception(CortexError("Invalid Cortex RPC response."))
                elif 'id' not in message:
                    self._event(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Don't log raw transport errors, responses or credential-bearing requests.
            pass
        finally:
            for future, _ in self.pending.values():
                if not future.done():
                    future.set_exception(CortexDisconnected("Cortex WebSocket disconnected."))
            self._event({'_disconnected': True})

    async def request(self, method, params=None, timeout=10):
        async with self.limit:
            if not self.websocket or not self.reader or self.reader.done():
                raise CortexDisconnected("Cortex WebSocket disconnected.")
            request_id = next(self._ids)
            future = asyncio.get_running_loop().create_future()
            self.pending[request_id] = (future, method)
            try:
                await self.websocket.send(json.dumps({
                    'jsonrpc': '2.0', 'id': request_id, 'method': method,
                    'params': params or {},
                }))
                return await asyncio.wait_for(future, timeout)
            except ConnectionClosed as exc:
                raise CortexDisconnected("Cortex WebSocket disconnected.") from exc
            except (asyncio.TimeoutError, TimeoutError) as exc:
                raise CortexError(f"Cortex {method} timed out.") from exc
            finally:
                self.pending.pop(request_id, None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()  # retrieve exceptions even if send failed first

    async def next_event(self, timeout=10):
        message = await asyncio.wait_for(self.events.get(), timeout)
        if message.get('_disconnected'):
            raise CortexDisconnected("Cortex WebSocket disconnected.")
        return message

    async def close(self):
        try:
            if self.websocket:
                await self.websocket.close()
        finally:
            if self.reader:
                self.reader.cancel()
                await asyncio.gather(self.reader, return_exceptions=True)
            self.websocket = self.reader = None
