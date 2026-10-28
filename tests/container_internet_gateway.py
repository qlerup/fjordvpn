"""Manual isolated Docker integration check; run inside the test image with /test mounted."""
import ipaddress
import json
from pathlib import Path
import time
import sys
sys.path.insert(0, '/app')

import docker
import internet_gateway as ig

client = docker.from_env(timeout=20)
IMAGE = 'fjordvpn-app:internet-candidate'
OWNER = {'dk.fjordvpn.test':'internet-20261028'}
containers = []
network = None
root = Path('/test')
host_root = '/opt/fjordvpn-internet-itest'


def execute(container, command, ok=True):
    result = container.exec_run(command)
    if ok and result.exit_code:
        raise RuntimeError(str(command[:3])+': '+result.output.decode(errors='replace')[:1000])
    return result


def create(name, **options):
    c = client.containers.run(IMAGE, name='fjordvpn-itest-'+name, detach=True,
        labels=OWNER, **options)
    containers.append(c)
    return c


try:
    assert not client.containers.list(all=True, filters={'label':'dk.fjordvpn.test=internet-20261028'})
    router = create('router', command=['sh','-c','iptables -P FORWARD DROP && exec sleep 600'],
        cap_drop=['ALL'], cap_add=['NET_ADMIN'], sysctls={'net.ipv4.ip_forward':'1'})
    peer = create('client', command=['sleep','600'], cap_drop=['ALL'], cap_add=['NET_ADMIN'])
    router.reload();peer.reload()
    router_ip = router.attrs['NetworkSettings']['Networks']['bridge']['IPAddress']
    peer_ip = peer.attrs['NetworkSettings']['Networks']['bridge']['IPAddress']
    for args in [
        ['-A','OUTPUT','-o','lo','-j','ACCEPT'],
        ['-A','OUTPUT','-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT'],
        ['-A','OUTPUT','-o','eth0','-d','172.17.0.0/16','-j','ACCEPT'],
        ['-A','OUTPUT','-o','tun0','-j','ACCEPT'],
        ['-P','OUTPUT','DROP'],
    ]:execute(router,['iptables',*args])
    baseline = execute(peer,['python','-c',
        "import urllib.request;print(urllib.request.urlopen('https://v4.plex.tv/pms/:/ip',timeout=8).read().decode())"])
    print('ordinary_exit',baseline.output.decode().strip(),flush=True)
    network = client.networks.create('fjordvpn-itest-uplink', driver='bridge', internal=True, labels=OWNER,
        ipam=docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet='10.249.240.0/29')]))
    network.connect(router, ipv4_address='10.249.240.2')
    interfaces = json.loads(execute(router,['ip','-j','address']).output)
    interface = next(x['ifname'] for x in interfaces if any(a.get('local')=='10.249.240.2' for a in x.get('addr_info',[])))
    for command in [['ip','link','set',interface,'down'],['ip','link','set',interface,'name','tun0'],['ip','link','set','tun0','up']]:
        execute(router,command)
    echo = create('echo',network=network.name,networking_config={network.name:
        client.api.create_endpoint_config(ipv4_address='10.249.240.3')}, command=['python','-c',
        "from http.server import BaseHTTPRequestHandler,HTTPServer\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  b=(self.client_address[0]+'\\n').encode()+b'x'*5242880;self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)\nHTTPServer(('0.0.0.0',8080),H).serve_forever()"])
    profile = {'id':'f'*32,'host':peer_ip,'internet_gateway':{'enabled':True,'subnet':'10.250.250.0/30'}}
    server_key, server_pub = ig.keypair();peer_key,peer_pub=ig.keypair()
    root.mkdir(exist_ok=True)
    for filename,value in {'internet-server.key':server_key,'internet-client.key':peer_key,
        'internet-public.json':json.dumps({'server':server_pub,'client':peer_pub}),
        'internet-runtime.json':json.dumps({'host':router_ip,'port':ig.PORT}),
        'settings.json':json.dumps({'profile':profile,'client_public':peer_pub})}.items():
        path=root/filename;path.write_text(value);path.chmod(0o600)
    worker=create('worker',command=['python','/app/internet_gateway_worker.py'],
        network_mode='container:'+router.id,cap_drop=['ALL'],cap_add=['NET_ADMIN'],read_only=True,
        volumes={host_root+'/internet-server.key':{'bind':'/config/server.key','mode':'ro'},
                 host_root+'/settings.json':{'bind':'/config/settings.json','mode':'ro'},
                 host_root:{'bind':'/observed','mode':'rw'}})
    config=ig.client_config(profile,root,[ipaddress.ip_network('172.17.0.0/16')])
    import io,tarfile
    payload=config.encode();archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w') as tar:
        info=tarfile.TarInfo('fjvpn-itest.conf');info.size=len(payload);info.mode=0o600;tar.addfile(info,io.BytesIO(payload))
    peer.put_archive('/tmp',archive.getvalue())
    stripped=execute(peer,['wg-quick','strip','/tmp/fjvpn-itest.conf']).output
    # Configure real kernel WireGuard; /1 routes avoid Docker's read-only sysctl mount.
    setup=['ip link add fjvpn-itest type wireguard',
           'wg setconf fjvpn-itest /tmp/fjvpn-stripped.conf',
           'ip address add 10.250.250.2/32 dev fjvpn-itest',
           'ip link set fjvpn-itest mtu 1280 up',
           'ip route add 0.0.0.0/1 dev fjvpn-itest',
           'ip route add 128.0.0.0/1 dev fjvpn-itest']
    data=io.BytesIO()
    with tarfile.open(fileobj=data,mode='w') as tar:
        info=tarfile.TarInfo('fjvpn-stripped.conf');info.size=len(stripped);info.mode=0o600;tar.addfile(info,io.BytesIO(stripped))
    peer.put_archive('/tmp',data.getvalue())
    setup += [line.split(' = ',1)[1].replace('%i','fjvpn-itest') for line in config.splitlines() if line.startswith('PostUp = ')]
    execute(peer,['sh','-ec','\n'.join(setup)])
    fetch=['python','-c',"import urllib.request,time;t=time.monotonic();b=urllib.request.urlopen('http://10.249.240.3:8080',timeout=10).read();print(len(b),b.split(b'\\n',1)[0].decode(),round(time.monotonic()-t,3));assert b.startswith(b'10.249.240.2\\n') and len(b)>5242880"]
    for attempt in range(10):
        result=execute(peer,fetch,ok=False)
        if result.exit_code==0:break
        time.sleep(1)
    assert result.exit_code==0,result.output.decode(errors='replace')
    print('wireguard_transfer',result.output.decode().strip(),flush=True)
    execute(peer,['sh','-c',"python -m http.server 32400 --bind 0.0.0.0 >/tmp/http.log 2>&1 &"])
    time.sleep(1)
    execute(router,['python','-c',"import urllib.request;assert urllib.request.urlopen('http://10.250.250.2:32400',timeout=3).status==200"])
    print('relay_to_private_server_allowed_with_output_drop',True,flush=True)
    time.sleep(4)
    state=json.loads((root/'internet-status.json').read_text())
    if state['state']=='error':
        result=worker.exec_run(['python','-c',"import json;from internet_gateway_worker import configure;from pathlib import Path;c=json.loads(Path('/config/settings.json').read_text());configure(c['profile'],c['client_public'])"])
        print('worker_diagnostic',result.output.decode(errors='replace'),flush=True)
        print('rules',execute(router,['ip','-j','rule']).output.decode(),flush=True)
    assert state['state']=='ready',state
    print('handshake_status',state['state'],flush=True)
    rules=json.loads(execute(router,['ip','-j','rule']).output)
    assert len([r for r in rules if r.get('priority')==97])==1
    # Give the router an ordinary fallback, then take away its VPN interface.
    execute(router,['ip','link','set','tun0','down'])
    execute(router,['ip','route','replace','10.249.240.0/29','via','172.17.0.1','dev','eth0'])
    blocked=execute(peer,['python','-c',"import urllib.request;urllib.request.urlopen('http://10.249.240.3:8080',timeout=3).read()"],ok=False)
    assert blocked.exit_code!=0
    print('vpn_down_forwarding_blocked',True,flush=True)
    execute(peer,['ip','link','delete','fjvpn-itest'])
    blocked=execute(peer,['python','-c',"import urllib.request;urllib.request.urlopen('https://v4.plex.tv/pms/:/ip',timeout=3).read()"],ok=False)
    assert blocked.exit_code!=0
    print('client_tunnel_down_fallback_blocked',True,flush=True)
    print('PASS',flush=True)
finally:
    for c in reversed(containers):
        c.reload()
        assert c.labels.get('dk.fjordvpn.test')=='internet-20261028'
        c.remove(force=True)
    if network:
        network.remove()
