import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import urllib.request

shutdown = threading.Event()
for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda *_: shutdown.set())


def stop(process):
    if process:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


class PublicLocation:
    """Read Gluetun's own location result, cached for the current egress IP."""
    def __init__(self):
        self.address = None
        self.country = ''
        self.retry_at = 0

    def get(self, address):
        if address != self.address:
            self.address, self.country, self.retry_at = address, '', 0
        if self.country or time.monotonic() < self.retry_at:
            return self.country
        self.retry_at = time.monotonic() + 30
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/v1/publicip/ip', timeout=2) as response:
                data = json.loads(response.read(8192))
            if not isinstance(data, dict):
                return ''
            country = data.get('country', '')
            if data.get('public_ip') == address and isinstance(country, str) and 0 < len(country) <= 80:
                self.country = country
        except (OSError, ValueError, TypeError):
            pass  # Location is optional; failure never changes tunnel readiness.
        return self.country


def observe():
    process, current = None, None
    location = PublicLocation()
    networks = [ipaddress.IPv4Network(n) for n in os.environ['LAN_SUBNETS'].split(',')]
    try:
        while not shutdown.is_set():
            state = dict(healthy=False, public_ip='', public_port='', country='', relay_active=False,
                         checked_at=time.time(), message='Venter på VPN-forbindelse', revision='')
            wanted = None
            try:
                settings = json.loads(Path('/config/settings.json').read_text())
                state['revision'] = settings['revision']
                with urllib.request.urlopen('http://127.0.0.1:9999/', timeout=2) as r:
                    state['healthy'] = r.status == 200
                if state['healthy']:
                    state['message'] = 'VPN klar · venter på offentlig port'
                    address = ipaddress.IPv4Address(Path('/vpn-state/ip').read_text().strip())
                    if address.is_global:
                        state['public_ip'] = str(address)
                        state['country'] = location.get(str(address))
                    port = int(Path('/vpn-state/forwarded_port').read_text().strip())
                    if 1 <= port <= 65535:
                        state['public_port'] = port
                        state['message'] = 'VPN og offentlig port er klar'
                        if settings['enabled']:
                            target = ipaddress.IPv4Address(settings['host'])
                            target_port = settings['port']
                            if any(target in n and target not in (n.network_address,n.broadcast_address) for n in networks) and type(target_port) is int and 1 <= target_port <= 65535:
                                wanted = (port, str(target), target_port, settings['revision'])
            except (OSError, ValueError, KeyError, TypeError):
                pass
            if current != wanted or (process is not None and process.poll() is not None):
                stop(process)
                process, current = None, None
                if wanted:
                    port, host, target_port, _ = wanted
                    process = subprocess.Popen(['socat', f'TCP4-LISTEN:{port},reuseaddr,fork,nodelay',
                        f'TCP4:{host}:{target_port},connect-timeout=5,nodelay'], start_new_session=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    current = wanted
            state['relay_active'] = bool(process and process.poll() is None)
            if state['relay_active']:
                state['message'] = 'TCP-videresendelse aktiv'
            state['checked_at'] = time.time()
            temporary = Path('/observed/status.tmp')
            temporary.write_text(json.dumps(state))
            temporary.replace('/observed/status.json')
            shutdown.wait(2)
    finally:
        stop(process)


if __name__ == '__main__':
    observe()
