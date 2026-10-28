"""Run with NET_ADMIN only inside one VPN namespace, never on the Docker host."""
import json
from pathlib import Path
import signal
import subprocess
import threading
import time

from internet_gateway import INTERFACE, PORT, addresses, firewall_rules, private_rule_present


def run(command):
    return subprocess.run(command, check=True, capture_output=True, timeout=5).stdout


def configure(profile, client_public):
    for table, chain, action, args in firewall_rules(profile):
        result = subprocess.run(['iptables', '-w', '3', '-t', table, '-C', chain, *args],
                                capture_output=True, timeout=5)
        if result.returncode == 1:
            run(['iptables', '-w', '3', '-t', table, action, chain, *args])
        elif result.returncode:
            raise RuntimeError('Firewall check failed')
    if subprocess.run(['ip', 'link', 'show', INTERFACE], capture_output=True).returncode:
        run(['ip', 'link', 'add', INTERFACE, 'type', 'wireguard'])
    gateway, client = addresses(profile)
    run(['wg', 'set', INTERFACE, 'listen-port', str(PORT), 'private-key', '/config/server.key',
         'peer', client_public, 'allowed-ips', client+'/32'])
    run(['ip', 'address', 'replace', gateway+'/30', 'dev', INTERFACE])
    subnet = profile['internet_gateway']['subnet']
    rules = json.loads(run(['ip', '-j', '-4', 'rule', 'show']))
    if not private_rule_present(rules, subnet):
        run(['ip', '-4', 'rule', 'add', 'priority', '97', 'to', subnet, 'lookup', 'main'])
    run(['ip', 'link', 'set', 'dev', INTERFACE, 'mtu', '1280', 'up'])


def main():
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        try:
            config = json.loads(Path('/config/settings.json').read_text())
            configure(config['profile'], config['client_public'])
            values = run(['wg', 'show', INTERFACE, 'latest-handshakes']).decode().split()
            handshake = int(values[1]) if len(values) == 2 else 0
            ready = 0 <= time.time()-handshake < 180
            state = dict(state='ready' if ready else 'pending', handshake=handshake,
                message='Serverens WireGuard-relæ er forbundet.' if ready else
                        'Installer WireGuard-konfigurationen på serveren for at forbinde internettrafikken.')
        except Exception:
            state = dict(state='error', message='WireGuard-relæet kunne ikke startes. Kontrollér kernelens WireGuard-understøttelse og VPN-status.')
        state['checked_at'] = time.time()
        path = Path('/observed/internet-status.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(state)); temp.chmod(0o600); temp.replace(path)
        stop.wait(3)
    # Leave forwarding guards intact until the namespace itself is removed.


if __name__ == '__main__':
    main()
