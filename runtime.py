"""Docker lifecycle limited to this application's UUID-labelled resources."""
import json
import os
from pathlib import Path
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
        self.project = ''
        self.relay_image = 'fjordvpn-relay:1'
        self.host_root = self.store.root
        if os.environ.get('FJORDVPN_CONTAINER') == '1':
            parent=self.client.containers.get(os.environ['HOSTNAME'])
            self.project=parent.labels.get('com.docker.compose.project','')
            self.relay_image=parent.attrs['Image']
            source=next((m['Source'] for m in parent.attrs['Mounts'] if m['Destination']==str(store.root) and m['Type']=='bind'),None)
            expected=os.environ.get('HOST_DATA_DIR','')
            if not source or not expected.startswith('/') or source.rstrip('/')!=expected.rstrip('/'):
                raise RuntimeError('HOST_DATA_DIR skal være den samme absolutte værtssti som datamappens bind-mount.')
            self.host_root=Path(source)

    def labels(self, ident, kind):
        labels={OWNER:'1',LABEL:ident}
        if self.project:
            labels.update({'com.docker.compose.project':self.project,
                'com.docker.compose.service':f'profile-{ident}-{kind}',
                'com.docker.compose.oneoff':'False','com.docker.compose.config-hash':'fjordvpn-profile-v1'})
        return labels

    @property
    def restart_policy(self):
        # In Compose the manager restores desired profiles; stopping the app stops
        # its tunnels, and `down --remove-orphans` owns their complete cleanup.
        return {'Name':'no' if self.project else 'unless-stopped'}

    def container(self, ident, kind):
        try:
            c = self.client.containers.get(f'fjordvpn-{ident}-{kind}')
        except docker.errors.NotFound:
            return None
        if c.labels.get(OWNER) != '1' or c.labels.get(LABEL) != ident:
            raise ValueError('En anden container bruger navnet. Den er ikke ændret.')
        if self.project and c.labels.get('com.docker.compose.project')!=self.project:
            raise ValueError('VPN-containeren tilhører en anden installation.')
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
        host_folder=self.host_root/'profiles'/ident
        if vpn is None:
            vpn = self.client.containers.create(GLUETUN, name=f'fjordvpn-{ident}-vpn',
                labels=self.labels(ident,'vpn'), cap_add=['NET_ADMIN'], devices=['/dev/net/tun:/dev/net/tun'],
                environment={'TZ': 'Europe/Copenhagen', 'VPN_SERVICE_PROVIDER': 'custom',
                    'VPN_TYPE': 'wireguard', 'VPN_PORT_FORWARDING': 'on',
                    'VPN_PORT_FORWARDING_PROVIDER': 'protonvpn',
                    'FIREWALL_OUTBOUND_SUBNETS': ','.join(map(str, self.store.networks))},
                volumes={str(host_folder / 'wg0.conf'): {'bind': '/gluetun/wireguard/wg0.conf', 'mode': 'ro'},
                         str(host_folder / 'state'): {'bind': '/tmp/gluetun', 'mode': 'rw'}},
                restart_policy=self.restart_policy,
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
            vpn.update(restart_policy=self.restart_policy)
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
            extra={'command':['python','/app/relay/observer.py']} if os.environ.get('FJORDVPN_CONTAINER')=='1' else {}
            observer = self.client.containers.create(self.relay_image, name=f'fjordvpn-{ident}-relay',
                labels=self.labels(ident,'relay'), network_mode='container:' + vpn.id,
                user=f'{os.getuid()}:{os.getgid()}',
                environment={'LAN_SUBNETS': ','.join(map(str,self.store.networks))},
                volumes={str(host_folder/'state'):{'bind':'/vpn-state','mode':'ro'},
                    str(host_folder/'relay'):{'bind':'/config','mode':'ro'},
                    str(host_folder/'observed'):{'bind':'/observed','mode':'rw'}},
                read_only=True, cap_drop=['ALL'], security_opt=['no-new-privileges:true'],
                init=True, restart_policy=self.restart_policy, mem_limit='96m',
                log_config=docker.types.LogConfig(type='json-file', config={'max-size':'2m','max-file':'2'}),**extra)
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
                except Exception as exc:
                    # Docker error bodies may contain configuration. Never send them to UI/logs.
                    if '/dev/net/tun' in str(exc):
                        self.errors[p['id']] = 'TUN mangler på Docker-værten. Giv /dev/net/tun videre til din LXC, og prøv igen.'
                    else:
                        self.errors[p['id']] = 'Kunne ikke ændre VPN-driften. Kontrollér Docker og prøv igen.'

    def shutdown(self):
        """Compose stop leaves persistent profiles intact, but stops their runtime."""
        if not self.project:
            return
        from concurrent.futures import ThreadPoolExecutor
        def stop_profile(p):
            for kind in ('relay','vpn'):
                try:
                    c=self.container(p['id'],kind)
                    if c:
                        c.update(restart_policy={'Name':'no'})
                        c.stop(timeout=3)
                except docker.errors.NotFound:
                    pass
        with self.store.lock:
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(stop_profile,self.store.all()))
