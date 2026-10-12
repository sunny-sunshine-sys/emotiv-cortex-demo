"""Local verified WSS mock only; never contacts EMOTIV hardware or Launcher."""

import asyncio
import json
import ssl
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.main import create_eeg_source
from backend.recorder import EEGRecorder
from backend.simulator import SimulatedEEGSource

from websockets.asyncio.server import serve

from backend.cortex_client import CortexClient, CortexError, CortexRPCError, tls_context
from backend.cortex_source import CortexEEGSource, LICENSE_MESSAGE, channel_indices, convert_eeg


class MockCortex:
    def __init__(self, access=True, headset=True, licensed=True, rpc_license=False,
                 connected=True, disconnect=False, warning=False, inactive=False):
        self.access, self.headset, self.licensed = access, headset, licensed
        self.rpc_license, self.connected = rpc_license, connected
        self.disconnect, self.warning, self.inactive = disconnect, warning, inactive
        self.requests = []
        self.cols = ['COUNTER', 'INTERPOLATED', 'F3', 'O2', 'Pz', 'RAW_CQ', 'MARKERS']
        self.reversed = []

    async def handle(self, ws):
        async for raw in ws:
            request = json.loads(raw)
            self.requests.append(request)
            method, params = request['method'], request['params']
            response = {'jsonrpc': '2.0', 'id': request['id']}
            if method in ('first', 'second'):
                self.reversed.append(request)
                if len(self.reversed) == 2:
                    for r in reversed(self.reversed):
                        await ws.send(json.dumps({'jsonrpc': '2.0', 'id': r['id'], 'result': r['method']}))
                continue
            if method == 'silent':
                continue
            if method == 'getCortexInfo':
                result = {'version': 'mock'}
            elif method == 'requestAccess':
                result = {'accessGranted': self.access}
            elif method == 'controlDevice':
                if params['command'] == 'connect':
                    self.connected = True
                result = {}
            elif method == 'queryHeadsets':
                result = [{'id': 'mock-headset', 'status': 'connected' if self.connected else 'discovered', 'samplingRate': 256}] if self.headset else []
            elif method == 'authorize':
                result = {'cortexToken': 'mock-token'}
            elif method == 'createSession':
                result = {'id': 'mock-session', 'status': 'opened' if self.inactive else 'activated'}
            elif method == 'subscribe':
                if self.rpc_license:
                    response['error'] = {'code': -32016, 'message': 'Raw EEG license unavailable'}
                    await ws.send(json.dumps(response))
                    continue
                result = {'success': [{'streamName': 'eeg', 'cols': self.cols}] if self.licensed else [],
                          'failure': [] if self.licensed else [{'streamName': 'eeg', 'message': 'Raw EEG license required'}]}
                if self.licensed:
                    # Streaming can start before the subscription response arrives.
                    await ws.send(json.dumps({'sid': 'mock-session', 'time': 42.0, 'eeg': [0, 0, 11.5, -22, 3, 99, []]}))
            elif method in ('unsubscribe', 'updateSession'):
                result = {}
            else:
                response['error'] = {'code': -32601, 'message': 'Unknown method'}
                await ws.send(json.dumps(response))
                continue
            response['result'] = result
            await ws.send(json.dumps(response))
            if method == 'subscribe' and self.licensed:
                await ws.send(json.dumps({'sid': 'mock-session', 'time': 42.01, 'eeg': [1, 0, 12.5, -21, 4, 99, []]}))
                if self.warning:
                    await ws.send(json.dumps({'warning': {'code': 103, 'message': 'headset disconnected'}}))
                if self.disconnect:
                    await ws.close()


class CortexTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.cert = Path(cls.temp.name) / 'cert.pem'
        cls.key = Path(cls.temp.name) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(cls.key), '-out', str(cls.cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    async def setup_mock(self, **options):
        self.mock = MockCortex(**options)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        self.server = await serve(self.mock.handle, '127.0.0.1', 0, ssl=context)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f'wss://localhost:{port}'
        self.addAsyncCleanup(self.close_server)
        return CortexClient(url=self.url, ca_file=self.cert)

    async def close_server(self):
        self.server.close()
        await self.server.wait_closed()

    def source(self):
        return CortexEEGSource('mock-client', 'mock-secret',
                               client_factory=lambda: CortexClient(url=self.url, ca_file=self.cert),
                               retry_delay=60, discovery_delay=0)

    async def observe_state(self, source, expected):
        generator = source.stream()
        pending = asyncio.create_task(anext(generator))
        try:
            async def wait_state():
                while source.status['state'] != expected:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_state(), 2)
            self.assertFalse(source.status['ready'])
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await generator.aclose()

    async def test_json_rpc_unique_ids_out_of_order_errors_timeout(self):
        client = await self.setup_mock()
        await client.connect()
        self.addAsyncCleanup(client.close)
        self.assertEqual(await asyncio.gather(client.request('first'), client.request('second')), ['first', 'second'])
        with self.assertRaises(CortexRPCError):
            await client.request('unknown')
        with self.assertRaises(CortexError):
            await client.request('silent', timeout=0.03)
        self.assertFalse(client.pending)
        requests = self.mock.requests
        self.assertEqual(len({r['id'] for r in requests}), len(requests))
        self.assertTrue(all(r['jsonrpc'] == '2.0' for r in requests))

    async def test_workflow_mapping_active_session_and_cleanup(self):
        await self.setup_mock(connected=False)
        source = self.source()
        generator = source.stream()
        first = await asyncio.wait_for(anext(generator), 2)
        second = await asyncio.wait_for(anext(generator), 2)
        self.assertEqual(first, {'timestamp': 42.0, 'eeg': {'F3': 11.5, 'O2': -22, 'Pz': 3}})
        self.assertGreater(second['timestamp'], first['timestamp'])
        self.assertTrue(source.status['ready'])
        self.assertEqual(source.status['channels'], ['F3', 'O2', 'Pz'])
        await generator.aclose()
        methods = [r['method'] for r in self.mock.requests]
        self.assertEqual(methods[:4], ['getCortexInfo', 'requestAccess', 'controlDevice', 'queryHeadsets'])
        self.assertIn('authorize', methods)
        self.assertEqual(methods[-2:], ['unsubscribe', 'updateSession'])
        create = next(r for r in self.mock.requests if r['method'] == 'createSession')
        self.assertEqual(create['params']['status'], 'active')
        self.assertEqual(create['params']['cortexToken'], 'mock-token')
        self.assertTrue(any(r['params'].get('command') == 'connect' for r in self.mock.requests))
        self.assertIsNone(source.token)
        self.assertEqual(len({r['id'] for r in self.mock.requests}), len(self.mock.requests))

    async def test_launcher_approval_required(self):
        await self.setup_mock(access=False)
        source = self.source()
        await self.observe_state(source, 'approval_required')
        self.assertIn('Launcher', source.status['message'])
        self.assertNotIn('authorize', [r['method'] for r in self.mock.requests])

    async def test_approval_retry_recovers_without_restart(self):
        await self.setup_mock(access=False)
        source = self.source()
        source.retry_delay = 0.02
        generator = source.stream()
        pending = asyncio.create_task(anext(generator))
        async def wait_approval():
            while source.status['state'] != 'approval_required':
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait_approval(), 2)
        self.mock.access = True
        self.assertIn('eeg', await asyncio.wait_for(pending, 2))
        self.assertTrue(source.status['ready'])
        await generator.aclose()

    async def test_no_headset(self):
        await self.setup_mock(headset=False)
        source = self.source()
        await self.observe_state(source, 'no_headset')
        self.assertEqual(source.status['message'], 'No EMOTIV headset detected.')

    async def test_license_subscription_failure(self):
        await self.setup_mock(licensed=False)
        source = self.source()
        await self.observe_state(source, 'license_unavailable')
        self.assertEqual(source.status['message'], LICENSE_MESSAGE)
        self.assertIn('updateSession', [r['method'] for r in self.mock.requests])

    async def test_license_rpc_error(self):
        await self.setup_mock(rpc_license=True)
        source = self.source()
        await self.observe_state(source, 'license_unavailable')
        self.assertEqual(source.status['message'], LICENSE_MESSAGE)

    async def test_inactive_session_rejected_and_closed(self):
        await self.setup_mock(inactive=True)
        source = self.source()
        await self.observe_state(source, 'cortex_error')
        self.assertNotIn('subscribe', [r['method'] for r in self.mock.requests])
        self.assertIn('updateSession', [r['method'] for r in self.mock.requests])

    async def check_drop(self, warning=False):
        await self.setup_mock(disconnect=not warning, warning=warning)
        source = self.source()
        generator = source.stream()
        self.assertIn('eeg', await asyncio.wait_for(anext(generator), 2))
        await asyncio.wait_for(anext(generator), 2)
        pending = asyncio.create_task(anext(generator))
        try:
            async def wait_state():
                while source.status['state'] != ('headset_disconnected' if warning else 'disconnected'):
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_state(), 2)
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await generator.aclose()
        self.assertFalse(source.status['ready'])

    async def test_cortex_disconnect(self):
        await self.check_drop()

    async def test_headset_disconnect_warning(self):
        await self.check_drop(warning=True)

    async def test_credentials_missing(self):
        await self.setup_mock()
        source = self.source()
        source.client_secret = ''
        await self.observe_state(source, 'credentials_missing')
        self.assertFalse(self.mock.requests)

    async def test_verified_tls_and_wrong_hostname_rejected(self):
        await self.setup_mock()
        context = tls_context(self.cert)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        wrong_host = CortexClient(url=self.url.replace('localhost', '127.0.0.1'), ca_file=self.cert)
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        def expected_peer_reset(loop, context):
            if isinstance(context.get('exception'), ConnectionResetError):
                return  # peer reset is expected after client rejects its hostname
            if previous:
                previous(loop, context)
            else:
                loop.default_exception_handler(context)
        loop.set_exception_handler(expected_peer_reset)
        try:
            with self.assertRaises(ssl.SSLCertVerificationError):
                await wrong_host.connect()
            await asyncio.sleep(0.05)
        finally:
            loop.set_exception_handler(previous)

    async def test_cancelled_cleanup_still_closes_socket(self):
        source = CortexEEGSource('mock-client', 'mock-secret')
        source.token, source.session, source.subscribed = 'mock-token', 'mock-session', True
        class Client:
            async def request(self, *args, **kwargs):
                await asyncio.Event().wait()
            close = AsyncMock()
        client = Client()
        task = asyncio.create_task(source._cleanup(client))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        client.close.assert_awaited_once()
        self.assertIsNone(source.token)
        self.assertIsNone(source.session)

    async def test_event_queue_is_bounded(self):
        client = CortexClient()
        for i in range(1000):
            client._event({'eeg': [i]})
        self.assertEqual(client.events.qsize(), 256)


class MappingTests(unittest.TestCase):
    def test_14_channel_layout_and_metadata_exclusion(self):
        electrodes = ['AF3', 'F7', 'F3', 'FC5', 'T7', 'P7', 'O1', 'O2', 'P8', 'T8', 'FC6', 'F4', 'F8', 'AF4']
        cols = ['COUNTER', 'INTERPOLATED', *electrodes, 'RAW_CQ', 'MARKERS']
        indices = channel_indices(cols)
        self.assertEqual(list(indices), electrodes)
        message = {'sid': 's', 'time': 1.0, 'eeg': [0, 0, *range(14), 0, []]}
        self.assertEqual(list(convert_eeg(message, indices, 's')['eeg'].values()), list(range(14)))
        self.assertIsNone(convert_eeg(message, indices, 'other'))
        message['eeg'] = [0]
        self.assertIsNone(convert_eeg(message, indices, 's'))

    def test_bad_cols_and_nonfinite_sample_rejected(self):
        with self.assertRaises(CortexError):
            channel_indices(['COUNTER', 'MARKERS'])
        with self.assertRaises(CortexError):
            channel_indices(['F3', 'F3'])
        self.assertIsNone(convert_eeg({'sid': 's', 'time': float('nan'), 'eeg': [1]}, {'F3': 0}, 's'))


class ConfigurationTests(unittest.TestCase):
    def test_simulation_factory(self):
        with patch.dict('os.environ', {'DATA_SOURCE': 'simulation'}):
            self.assertIsInstance(create_eeg_source(), SimulatedEEGSource)

    def test_emotiv_factory_and_invalid_mode(self):
        with patch.dict('os.environ', {'DATA_SOURCE': 'emotiv', 'EMOTIV_CLIENT_ID': '', 'EMOTIV_CLIENT_SECRET': ''}):
            self.assertIsInstance(create_eeg_source(), CortexEEGSource)
        with patch.dict('os.environ', {'DATA_SOURCE': 'unsupported'}):
            with self.assertRaises(ValueError):
                create_eeg_source()

    def test_dynamic_csv_layout_and_layout_change_stops_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = EEGRecorder(Path(directory))
            recorder.start(['F3', 'O2'])
            recorder.append({'timestamp': 1, 'eeg': {'F3': 4.5, 'O2': 6.7}})
            recorder.append({'timestamp': 2, 'eeg': {'O2': 8.9, 'F3': 7.8}})
            recorder.append({'timestamp': 3, 'eeg': {'AF3': 1}})
            self.assertFalse(recorder.is_recording)
            self.assertIn('layout changed', recorder.status()['error'])
            contents = (recorder.directory / recorder.last_recording['filename']).read_text()
            self.assertEqual(contents.splitlines(), ['timestamp,F3,O2', '1,4.5,6.7', '2,7.8,8.9'])
