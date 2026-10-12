"""Cortex workflow and cols-based EEG adapter; no simulation fallback."""

import asyncio
import math
import re
import ssl

from backend.cortex_client import CortexClient, CortexDisconnected, CortexError, CortexRPCError

ELECTRODE = re.compile(r"(?:AF|FP|F|FC|FT|C|CP|TP|T|P|PO|O)(?:\d+|Z)", re.IGNORECASE)
LICENSE_MESSAGE = "Raw EEG stream is not available for this Cortex license."


class SourceStateError(CortexError):
    def __init__(self, state, message):
        self.state = state
        super().__init__(message)


def is_license_error(message):
    return any(word in message.lower() for word in ('license', 'licence', 'permission', 'raw eeg', 'access denied', 'no access', 'not allowed'))


def channel_indices(cols):
    if not isinstance(cols, list):
        raise CortexError("Cortex EEG subscription did not provide cols.")
    indices = {name: i for i, name in enumerate(cols) if isinstance(name, str) and ELECTRODE.fullmatch(name)}
    if not indices or len(indices) != sum(isinstance(c, str) and bool(ELECTRODE.fullmatch(c)) for c in cols):
        raise CortexError("Cortex EEG cols contain no supported electrodes or duplicate channel names.")
    return indices


def convert_eeg(message, indices, session):
    if message.get('sid') != session or not isinstance(message.get('eeg'), list):
        return None
    values, timestamp = message['eeg'], message.get('time')
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool) or not math.isfinite(timestamp):
        return None
    if any(i >= len(values) or not isinstance(values[i], (int, float)) or isinstance(values[i], bool) or not math.isfinite(values[i]) for i in indices.values()):
        return None
    return {'timestamp': timestamp, 'eeg': {name: values[i] for name, i in indices.items()}}


class CortexEEGSource:
    def __init__(self, client_id, client_secret, ca_file=None, headset_id=None,
                 client_factory=None, retry_delay=5, discovery_delay=0.5):
        self.token = self.session = None
        self.subscribed = False
        self.client_id, self.client_secret = client_id, client_secret
        self.ca_file, self.headset_id = ca_file, headset_id
        self.client_factory = client_factory or (lambda: CortexClient(ca_file=ca_file))
        self.retry_delay, self.discovery_delay = retry_delay, discovery_delay
        self.status = {'data_source': 'emotiv', 'state': 'connecting', 'ready': False,
                       'message': 'Connecting to local Cortex.', 'channels': [], 'sampling_rate': None}

    def _state(self, state, message, ready=False):
        self.status.update(state=state, message=message, ready=ready)

    async def prepare(self, client):
        await client.request('getCortexInfo')
        credentials = {'clientId': self.client_id, 'clientSecret': self.client_secret}
        access = await client.request('requestAccess', credentials)
        if not access.get('accessGranted'):
            raise SourceStateError('approval_required', 'Approve this app in EMOTIV Launcher, then wait for retry.')
        await client.request('controlDevice', {'command': 'refresh'})
        headsets = []
        for attempt in range(5):
            headsets = await client.request('queryHeadsets')
            if self.headset_id:
                headsets = [h for h in headsets if h.get('id') == self.headset_id]
            if headsets:
                break
            if attempt < 4:
                await asyncio.sleep(self.discovery_delay)
        if not headsets:
            raise SourceStateError('no_headset', 'No EMOTIV headset detected.')
        headset = next((h for h in headsets if h.get('status') == 'connected'), headsets[0])
        if headset.get('status') != 'connected':
            await client.request('controlDevice', {'command': 'connect', 'headset': headset['id']})
            for _ in range(20):
                available = await client.request('queryHeadsets')
                connected = next((h for h in available if h.get('id') == headset['id'] and h.get('status') == 'connected'), None)
                if connected:
                    headset = connected
                    break
                await asyncio.sleep(self.discovery_delay)
            else:
                raise SourceStateError('headset_disconnected', 'EMOTIV headset did not connect; check Launcher and headset power.')
        auth = await client.request('authorize', {**credentials, 'debit': 0})
        self.token = auth['cortexToken']
        session = await client.request('createSession', {
            'cortexToken': self.token, 'headset': headset['id'], 'status': 'active',
        })
        self.session = session['id']
        if session.get('status') not in ('active', 'activated'):
            raise CortexError("Cortex session is not active/activated.")
        try:
            subscription = await client.request('subscribe', {
                'cortexToken': self.token, 'session': self.session, 'streams': ['eeg'],
            })
        except CortexRPCError as exc:
            if is_license_error(exc.rpc_message):
                raise SourceStateError('license_unavailable', LICENSE_MESSAGE) from exc
            raise
        for failure in subscription.get('failure', []):
            if failure.get('streamName') == 'eeg':
                if is_license_error(str(failure.get('message', ''))):
                    raise SourceStateError('license_unavailable', LICENSE_MESSAGE)
                raise CortexError("Cortex EEG subscription failed.")
        entry = next((s for s in subscription.get('success', []) if s.get('streamName') == 'eeg'), None)
        if entry is None:
            raise CortexError("Cortex EEG subscription returned no EEG stream.")
        self.subscribed = True
        indices = channel_indices(entry.get('cols'))
        rate = headset.get('samplingRate', entry.get('sampleRate'))
        self.status.update(channels=list(indices), sampling_rate=rate if isinstance(rate, (int, float)) and 0 < rate <= 1024 else None)
        self._state('waiting_for_data', 'Waiting for EEG data.')
        return indices

    async def _cleanup(self, client):
        try:
            if self.token and self.session:
                if self.subscribed:
                    try:
                        await client.request('unsubscribe', {'cortexToken': self.token, 'session': self.session, 'streams': ['eeg']}, timeout=2)
                    except Exception:
                        pass
                try:
                    await client.request('updateSession', {'cortexToken': self.token, 'session': self.session, 'status': 'close'}, timeout=2)
                except Exception:
                    pass
        finally:
            # A shutdown cancellation during an RPC must still close the socket.
            try:
                await client.close()
            except Exception:
                pass
            finally:
                self.token = self.session = None
                self.subscribed = False

    async def stream(self):
        while True:
            self.token = self.session = None
            self.subscribed = False
            self.status.update(channels=[], sampling_rate=None)
            client = self.client_factory()
            try:
                if not self.client_id or not self.client_secret:
                    raise SourceStateError('credentials_missing', 'Set EMOTIV_CLIENT_ID and EMOTIV_CLIENT_SECRET in local .env.')
                self._state('connecting', 'Connecting to local Cortex.')
                await client.connect()
                indices = await self.prepare(client)
                previous, first, count = None, None, 0
                last_valid = asyncio.get_running_loop().time()
                while True:
                    remaining = 10 - (asyncio.get_running_loop().time() - last_valid)
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    message = await client.next_event(timeout=remaining)
                    warning = message.get('warning', {})
                    if warning.get('code') in (0, 1, 3, 10, 102, 103):
                        details = warning.get('message')
                        if isinstance(details, dict) and details.get('sessionId') not in (None, self.session):
                            continue
                        raise SourceStateError('headset_disconnected', 'Cortex session or headset disconnected; retrying.')
                    if 'error' in message:
                        raise CortexError("Cortex stream reported an error.")
                    sample = convert_eeg(message, indices, self.session)
                    if sample is None or (previous is not None and sample['timestamp'] <= previous):
                        continue
                    last_valid = asyncio.get_running_loop().time()
                    previous = sample['timestamp']
                    first = previous if first is None else first
                    count += 1
                    if self.status['sampling_rate'] is None and previous - first >= 1:
                        self.status['sampling_rate'] = round((count - 1) / (previous - first))
                    self._state('connected', 'Receiving EMOTIV EEG.', ready=True)
                    yield sample
            except SourceStateError as exc:
                self._state(exc.state, str(exc))
            except ssl.SSLError:
                self._state('tls_error', 'Cortex TLS verification failed. Check the trusted EMOTIV CA and localhost certificate.')
            except CortexDisconnected:
                self._state('disconnected', 'Cortex WebSocket disconnected; retrying.')
            except (asyncio.TimeoutError, TimeoutError):
                self._state('no_data', 'No Cortex EEG data received; check headset and license.')
            except CortexError as exc:
                self._state('cortex_error', str(exc))
            except (OSError, ValueError):
                self._state('connection_error', 'Cannot connect to local Cortex; check Launcher, port 6868 and EMOTIV_CA_CERT.')
            except Exception:
                self._state('cortex_error', 'Unexpected Cortex response; check compatible Launcher/API version.')
            finally:
                self.status['ready'] = False
                await self._cleanup(client)
            await asyncio.sleep(self.retry_delay)
