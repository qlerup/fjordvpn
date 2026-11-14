"""Passive tunnel and TCP delivery measurements; never generates test traffic."""
from collections import deque
import json
from pathlib import Path
import re
import subprocess
import time


class TrafficMonitor:
    def __init__(self, root='/sys/class/net/tun0', run=subprocess.run):
        self.root = Path(root)
        self.run = run
        self.previous = None
        self.connections = {}
        self.history = deque(maxlen=150)

    def tcp(self):
        address = self.run(['ip', '-j', 'address', 'show', 'tun0'], capture_output=True,
                           text=True, timeout=1, check=True)
        addresses = json.loads(address.stdout)[0]['addr_info']
        ipv4 = next(a['local'] for a in addresses if a['family'] == 'inet')
        result = self.run(['ss', '-tinH', 'state', 'established', 'src', ipv4],
                          capture_output=True, text=True, timeout=1, check=True)
        rows = {}
        header = None
        for line in result.stdout.splitlines():
            if line and not line[0].isspace():
                header = line.split()
            elif header and len(header) >= 4:
                acked = re.search(r'\bbytes_acked:(\d+)', line)
                if acked:
                    # Socket endpoints stay private; only aggregate rates are published.
                    rows[tuple(header[-2:])] = int(acked[1])
        return rows

    def sample(self, now=None, timestamp=None):
        now = time.monotonic() if now is None else now
        timestamp = time.time() if timestamp is None else timestamp
        try:
            counters = tuple(int((self.root / 'statistics' / (name + '_bytes')).read_text())
                             for name in ('rx', 'tx'))
            identity = (self.root / 'ifindex').read_text().strip()
        except (OSError, ValueError):
            self.previous = None
            self.connections = {}
            self.history.clear()
            return {'state': 'unavailable', 'history': []}
        try:
            connections = self.tcp()
        except (OSError, ValueError, KeyError, IndexError, StopIteration, subprocess.SubprocessError):
            connections = None
        previous, self.previous = self.previous, (now, counters, identity)
        old_connections, self.connections = self.connections, connections or {}
        if (previous is None or previous[2] != identity or now <= previous[0]
                or now - previous[0] > 15 or any(a < b for a, b in zip(counters, previous[1]))):
            self.history.clear()
            return {'state': 'warming', 'history': []}
        elapsed = now - previous[0]
        download, upload = [round((value - old) * 8 / elapsed / 1_000_000, 3)
                            for value, old in zip(counters, previous[1])]
        delivered = None
        if connections is not None and old_connections:
            delivered = round(sum(max(0, value - old_connections[key])
                                  for key, value in connections.items() if key in old_connections)
                              * 8 / elapsed / 1_000_000, 3)
        point = dict(at=timestamp, download_mbps=download, upload_mbps=upload,
                     delivered_mbps=delivered)
        self.history.append(point)
        return dict(point, state='ready', interval_seconds=round(elapsed, 2),
                    history=list(self.history))
