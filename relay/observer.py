import csv
import io
import ipaddress
from html.parser import HTMLParser
import json
import os
import re
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
    """Match the active IP against Proton's published geofeed, not generic GeoIP."""
    URL = 'https://ip.me/static/geofeeds/geofeed-mm.csv'
    LOOKUP_URL = 'https://ip.me/ip/'

    def __init__(self):
        self.address = None
        self.country = ''
        self.retry_at = 0
        self.expires_at = 0
        self.networks = []
        self.lookup_address = None
        self.lookup_country = ''
        self.lookup_expires_at = 0
        self.lookup_retry_at = 0

    def lookup(self, address, now):
        """Fallback for Proton exit ranges absent from the published geofeed."""
        if address == self.lookup_address and now < self.lookup_expires_at:
            return self.lookup_country
        if now < self.lookup_retry_at:
            return ''
        self.lookup_retry_at = now + 60
        try:
            with urllib.request.urlopen(self.LOOKUP_URL + address, timeout=2) as response:
                raw = response.read(262145)
            if len(raw) > 262144:
                return ''
            page = raw.decode('utf-8')
            parser = LocationPage()
            parser.feed(page)
            match = re.search(r'<th>\s*Country Code:\s*</th>\s*<td>\s*<code>\s*([A-Z]{2})\s*</code>\s*</td>', page)
            # Never apply a home-page or redirected lookup for a different IP.
            if parser.address != address or not match:
                return ''
            self.lookup_address = address
            self.lookup_country = match.group(1)
            self.lookup_expires_at = now + 3600
            return self.lookup_country
        except (OSError, ValueError, TypeError):
            return ''

    def get(self, address):
        try:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                return ''
        except ValueError:
            return ''
        now = time.monotonic()
        if address == self.address and self.country and now < self.expires_at:
            return self.country
        self.address, self.country = address, ''
        if now >= self.expires_at:
            if now < self.retry_at:
                return ''
            self.retry_at = now + 60
            try:
                with urllib.request.urlopen(self.URL, timeout=2) as response:
                    raw = response.read(262145)
                if len(raw) > 262144:
                    return ''
                networks = []
                for row in csv.reader(io.StringIO(raw.decode('utf-8-sig'))):
                    if len(row) < 2 or row[0].lstrip().startswith('#'):
                        continue
                    country = row[1].strip().upper()
                    if len(country) != 2 or not country.isascii() or not country.isalpha():
                        continue
                    try:
                        networks.append((ipaddress.ip_network(row[0].strip()), country))
                    except ValueError:
                        continue
                if not networks:
                    return ''
                self.networks = sorted(networks, key=lambda pair: pair[0].prefixlen, reverse=True)
                self.expires_at = now + 21600
            except (OSError, ValueError, TypeError, csv.Error):
                return ''  # Country failure must never change VPN readiness.
        self.country = next((country for network, country in self.networks if ip in network), '')
        return self.country or self.lookup(str(ip), now)


class LocationPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.address = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('id') == 'ip-lookup':
            self.address = attrs.get('value')


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
