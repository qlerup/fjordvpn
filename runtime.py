"""Docker lifecycle limited to this application's UUID-labelled resources."""
import json
import os
import time

import docker

GLUETUN = 'qmcgaw/gluetun@sha256:fa19cc76b2af13d57a8d3dc3066f2ada061b1c761b8aecf989b3877c0486e027'
LABEL = 'dk.fjordvpn.profile'
OWNER = 'dk.fjordvpn.managed'


class Runtime:
    def __init__(self, store, client=None):
        self.store = store
        self.client = client or docker.from_env(timeout=15)
        self.errors = {}

    def container(self, ident, kind):
        try:
            c = self.client.containers.get(f'fjordvpn-{ident}-{kind}')
        except docker.errors.NotFound:
            return None
        if c.labels.get(OWNER) != '1' or c.labels.get(LABEL) != ident:
            raise ValueError('En anden container bruger navnet. Den er ikke ændret.')
        return c

    def reconcile(self, p):
        ident = p['id']
        vpn, observer = self.container(ident, 'vpn'), self.container(ident, 'relay')
        if not p['desired']:
            for c in (observer, vpn):
                if c:
                    c.update(restart_policy={'Name': 'no'})
                    if c.status != 'exited':
                        c.stop(timeout=5)
            return
        folder = self.store.directory(ident)
        labels = {OWNER: '1', LABEL: ident}
        if vpn is None:
            vpn = self.client.containers.create(GLUETUN, name=f'fjordvpn-{ident}-vpn',
                labels=labels, cap_add=['NET_ADMIN'], devices=['/dev/net/tun:/dev/net/tun'],
                environment={'TZ': 'Europe/Copenhagen', 'VPN_SERVICE_PROVIDER': 'custom',
                    'VPN_TYPE': 'wireguard', 'VPN_PORT_FORWARDING': 'on',
                    'VPN_PORT_FORWARDING_PROVIDER': 'protonvpn',
                    'FIREWALL_OUTBOUND_SUBNETS': ','.join(map(str, self.store.networks))},
                volumes={str(folder / 'wg0.conf'): {'bind': '/gluetun/wireguard/wg0.conf', 'mode': 'ro'},
                         str(folder / 'state'): {'bind': '/tmp/gluetun', 'mode': 'rw'}},
                restart_policy={'Name': 'unless-stopped'},
                mem_limit='384m', log_config=docker.types.LogConfig(type='json-file', config={'max-size':'5m','max-file':'2'}))
        if vpn.status != 'running':
            # Recreate the dependent namespace after each tunnel restart.
            if observer:
                observer.stop(timeout=5)
                observer.remove()
                observer = None
            for name in ('forwarded_port', 'ip'):
                (folder / 'state' / name).unlink(missing_ok=True)
            (folder / 'observed' / 'status.json').unlink(missing_ok=True)
            vpn.update(restart_policy={'Name': 'unless-stopped'})
            vpn.start()
        vpn.reload()
        if observer:
            observer.reload()
            # Gluetun can be restarted by Docker, invalidating the observer namespace.
            if observer.attrs['State']['StartedAt'] < vpn.attrs['State']['StartedAt']:
                observer.stop(timeout=5)
                observer.remove()
                observer = None
        if observer is None:
            observer = self.client.containers.create('fjordvpn-relay:1', name=f'fjordvpn-{ident}-relay',
                labels=labels, network_mode='container:' + vpn.id,
                user=f'{os.getuid()}:{os.getgid()}',
                environment={'LAN_SUBNETS': ','.join(map(str,self.store.networks))},
                volumes={str(folder/'state'):{'bind':'/vpn-state','mode':'ro'},
                    str(folder/'relay'):{'bind':'/config','mode':'ro'},
                    str(folder/'observed'):{'bind':'/observed','mode':'rw'}},
                read_only=True, cap_drop=['ALL'], security_opt=['no-new-privileges:true'],
                init=True, restart_policy={'Name':'unless-stopped'}, mem_limit='96m',
                log_config=docker.types.LogConfig(type='json-file', config={'max-size':'2m','max-file':'2'}))
        if observer.status != 'running':
            observer.start()

    def status(self, p):
        result = {k:v for k,v in p.items() if k != 'fingerprint'}
        result.update(kind='managed', state='off', public_ip='', public_port='', relay_active=False,
                      checked_at=time.time(), message='Slukket')
        if not p['desired']:
            try:
                c = self.container(p['id'], 'vpn')
                if c and c.status == 'running':
                    result.update(state='stopping',message='Lukker VPN og videresendelse…')
            except (docker.errors.DockerException, ValueError):
                result.update(state='unknown',message='Docker-status er ikke tilgængelig.')
        if p['desired']:
            result.update(state='connecting', message='Opretter forbindelse til Proton…')
            try:
                c = self.container(p['id'], 'vpn')
                if c and c.status == 'running':
                    obs = json.loads((self.store.directory(p['id'])/'observed/status.json').read_text())
                    if 0 <= time.time() - obs['checked_at'] < 20:
                        result.update({k:obs[k] for k in ('public_ip','public_port','relay_active','message','checked_at')})
                        result['state'] = 'online' if obs['healthy'] else 'connecting'
                        if obs.get('revision') != p['revision']:
                            result.update(relay_active=False, message='Anvender videresendelse…')
            except (OSError, ValueError, KeyError, docker.errors.DockerException):
                pass
        if self.errors.get(p['id']):
            result.update(state='error', message=self.errors[p['id']])
        return result

    def tick(self):
        with self.store.lock:
            for p in self.store.all():
                try:
                    self.reconcile(p)
                    self.errors.pop(p['id'], None)
                except Exception:
                    # Docker error bodies may contain configuration. Never send them to UI/logs.
                    self.errors[p['id']] = 'Kunne ikke ændre VPN-driften. Kontrollér Docker og prøv igen.'
