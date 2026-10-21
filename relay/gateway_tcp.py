"""Independent NAT-PMP mapping and TCP relay in the gateway VPN namespace."""
import socket
import struct
import subprocess
import time

INTERNAL_PORTS = (56800, 56801)  # Separate from FjordSeed's 56789/56790.


def request(protocol, private, public=0, lifetime=60):
    opcode = {'udp': 1, 'tcp': 2}[protocol]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(2)
        client.connect(('10.2.0.1', 5351))
        client.send(struct.pack('!BBHHHI', 0, opcode, 0, private, public, lifetime))
        data = client.recv(32)
    if len(data) != 16:
        raise ValueError('Invalid NAT-PMP response')
    version, operation, result, epoch, assigned_private, assigned_public, seconds = struct.unpack('!BBHIHHI', data)
    if (version != 0 or operation != opcode + 128 or result != 0 or assigned_private != private
            or seconds != lifetime or (lifetime and not 1 <= assigned_public <= 65535)
            or (public and lifetime and assigned_public != public)):
        raise ValueError('NAT-PMP mapping failed')
    return assigned_public


class Forwarder:
    def __init__(self, stop):
        self.stop = stop
        self.process = None
        self.current = None
        self.private = self.public = 0
        self.next_check = self.expires = 0

    def close(self):
        self.stop(self.process)
        self.process = None
        private, public = self.private, self.public
        self.private = self.public = 0
        self.current = None
        self.next_check = self.expires = 0
        if private:
            # Explicit private port only; never remove the gateway's primary lease.
            for protocol in ('udp', 'tcp'):
                try:
                    request(protocol, private, public, 0)
                except (OSError, ValueError):
                    pass

    def update(self, config, address, gateway_port, revision, healthy):
        pending = {'state': 'pending', 'public_port': None, 'message': 'Afventer VPN og TCP-port.'}
        if not config.get('enabled') or not healthy or not address or not gateway_port:
            self.close()
            return pending
        # Profile revisions also change for unrelated web/Minecraft edits.
        wanted = (address, gateway_port, config['host'], config['port'])
        if self.current != wanted:
            self.close()
            self.current = wanted
        try:
            now = time.monotonic()
            if not self.public or now >= self.next_check or now >= self.expires:
                self.private = next(p for p in INTERNAL_PORTS if p != gateway_port)
                # Proton uses a paired UDP/TCP allocation. Only TCP is opened locally.
                udp = request('udp', self.private, self.public)
                tcp = request('tcp', self.private, self.public or udp)
                if udp != tcp or tcp in (*INTERNAL_PORTS, gateway_port, 56789, 56790, 8080, 9999):
                    raise ValueError('Conflicting NAT-PMP mapping')
                self.public = tcp
                self.next_check, self.expires = now + 20, now + 50
            if self.process is None:
                self.process = subprocess.Popen([
                    'socat', f'TCP4-LISTEN:{self.private},reuseaddr,fork,nodelay',
                    f"TCP4:{config['host']}:{config['port']},connect-timeout=5,nodelay"],
                    start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if self.process.poll() is not None or time.monotonic() >= self.expires:
                raise ValueError('TCP relay unavailable')
            return {'state': 'ready', 'public_port': self.public,
                    'message': 'TCP-videresendelse aktiv. Brug den viste offentlige IP og TCP-port.'}
        except (OSError, ValueError):
            self.close()
            return {'state': 'error', 'public_port': None,
                    'message': 'TCP-porten kunne ikke åbnes eller fornyes. Prøver igen.'}
