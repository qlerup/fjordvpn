import base64
import json
from unittest.mock import Mock, MagicMock

import pytest

import internet_gateway
from configuration import plex_targets
from plex_sync import PlexSync
from runtime import Runtime
from test_app import app, login, upload, config
from test_gateway import store_with_key
from test_plex_sync import TOKEN, UUID, settings as gateway_settings


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    counter = iter(range(1, 100))
    def pair():
        value = next(counter)
        return tuple(base64.b64encode(bytes([n])*32).decode() for n in (value, value+100))
    monkeypatch.setattr(internet_gateway, 'keypair', pair)


def solo(**changes):
    return dict(name='Plex', host='192.168.1.96', port=32400, relay_enabled=True,
                internet_gateway={'enabled':True}, **changes)


def test_opt_in_isolated_keys_and_private_addresses(app):
    store = app.extensions['store']
    ordinary = store.create({'name':'Seed'}, config())
    before = (store.directory(ordinary['id'])/'profile.json').read_bytes()
    p = store.create(solo(), config(2))
    folder = store.directory(p['id'])
    assert (store.directory(ordinary['id'])/'profile.json').read_bytes() == before
    assert not list(store.directory(ordinary['id']).glob('internet-*.key'))
    assert internet_gateway.addresses(p) == ('10.250.0.1','10.250.0.2')
    relay = json.loads((folder/'relay/settings.json').read_text())
    assert relay['host'] == '10.250.0.2'
    assert p['host'] == '192.168.1.96'
    settings = {k:p[k] for k in ('name','host','port','relay_enabled')}
    store.edit(p['id'], settings)
    assert (folder/'internet-client.key').exists()
    assert json.loads((folder/'relay/settings.json').read_text())['host'] == '10.250.0.2'
    with pytest.raises(ValueError, match='anden VPN'):
        store.create(solo(), config(3))
    second = store.create({**solo(), 'host':'192.168.1.97'}, config(3))
    assert second['internet_gateway']['subnet'] != p['internet_gateway']['subnet']


@pytest.mark.parametrize('change', [
    {'internet_gateway':True}, {'internet_gateway':{'enabled':'yes'}},
    {'relay_enabled':False}, {'plex_sync':{'enabled':'yes'}},
    {'plex_sync':{'enabled':True}},
])
def test_invalid_gateway_and_plex_options_are_rejected(app, change):
    with pytest.raises(ValueError):
        app.extensions['store'].create({**solo(), **change}, config())
    assert app.extensions['store'].all() == []


def test_download_requires_auth_and_never_exposes_keys_in_status(app):
    c, h = login(app)
    p = app.extensions['store'].create(solo(plex_sync={'enabled':True,'token':TOKEN}), config())
    folder = app.extensions['store'].directory(p['id'])
    route = f"/api/profiles/{p['id']}/internet-config"
    assert app.test_client().get(route).status_code == 401
    assert c.get(route).status_code == 409
    (folder/'internet-runtime.json').write_text(json.dumps({'host':'192.168.1.110','port':51877}))
    response = c.get(route)
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'attachment;' in response.headers['Content-Disposition']
    text = response.get_data(as_text=True)
    assert 'Endpoint = 192.168.1.110:51877' in text
    assert 'AllowedIPs = 0.0.0.0/0' in text
    assert 'PrivateKey = '+(folder/'internet-client.key').read_text() in text
    assert (folder/'internet-server.key').read_text() not in text
    assert 'PostDown' not in text  # No automatic removal of the fail-closed guard.
    assert 'ip6tables' in text and '-j REJECT' in text and '192.168.1.0/24' in text
    status = c.get('/api/profiles').get_data(as_text=True)
    for secret in [TOKEN, (folder/'internet-client.key').read_text(), (folder/'internet-server.key').read_text()]:
        assert secret not in status
        assert secret not in (folder/'profile.json').read_text()
    assert c.get('/api/profiles').json['profiles'][0]['internet_config_ready'] is True


def test_solo_and_gateway_cannot_both_control_one_plex(tmp_path):
    store, key = store_with_key(tmp_path)
    p = store.create(solo(plex_sync={'enabled':True,'token':TOKEN}), config())
    with pytest.raises(ValueError, match='Plex-server'):
        store.create({'name':'Web','gateway':gateway_settings(key)}, config(2))
    data = {k:p[k] for k in ('name','host','port','relay_enabled')}
    store.edit(p['id'], {**data, 'plex_sync':{'enabled':False}})
    store.create({'name':'Web','gateway':gateway_settings(key)}, config(2))
    with pytest.raises(ValueError, match='Plex-server'):
        store.edit(p['id'], {**data, 'plex_sync':{'enabled':True,'token':TOKEN}})


def test_plex_token_retention_and_retarget_validation(app):
    store = app.extensions['store']
    p = store.create(solo(plex_sync={'enabled':True,'token':TOKEN}), config())
    data = {k:p[k] for k in ('name','host','port','relay_enabled')}
    store.edit(p['id'], {**data, 'plex_sync':{'enabled':True,'token':''}})
    assert store.plex_token_path(p['id'],'solo').read_text() == TOKEN
    with pytest.raises(ValueError, match='Plex-token'):
        store.edit(p['id'], {**data, 'host':'192.168.1.97'})


@pytest.mark.parametrize('full_tunnel',[False, True])
def test_solo_plex_sync_tracks_public_port_and_only_full_tunnel_enables_router_test(app, full_tunnel):
    store = app.extensions['store']
    data = {**solo(plex_sync={'enabled':True,'token':TOKEN}), 'internet_gateway':{'enabled':full_tunnel}}
    p = store.create(data, config()); p = store.power(p['id'], True)
    observed = dict(state='online', relay_active=True, public_ip='185.111.109.2', public_port=46273)
    runtime = Mock(); runtime.status.return_value = observed
    sync = PlexSync(store, runtime)
    values = {'customConnections':'https://keep.example:443','CertificateUUID':UUID}
    calls = []
    import xml.etree.ElementTree as E
    def xml(session, method, url, token=None, params=None):
        calls.append((method,url,token,params))
        if url.endswith('/identity'):return E.Element('MediaContainer',machineIdentifier='server')
        if method=='PUT':
            if params:values.update(params)
            return None
        root=E.Element('MediaContainer')
        for k,v in values.items():E.SubElement(root,'Setting',id=k,value=v)
        return root
    sync.xml=Mock(side_effect=xml);sync.tick()
    assert sync.status(p,observed)['solo']['state']=='ready'
    assert values['customConnections'].endswith(':46273')
    assert ('PublishServerOnPlexOnlineKey' in values) == full_tunnel
    assert any(u.endswith('/myplex/refreshReachability') for _,u,_,_ in calls) == full_tunnel
    if full_tunnel:assert values['ManualPortMappingPort']=='46273'
    assert all(not t for _,u,t,_ in calls if u.startswith('https:'))
    observed['public_port']=46274;sync.tick()
    assert ':46273' not in values['customConnections'] and values['customConnections'].endswith(':46274')


def test_worker_rules_cannot_fall_back_to_lan_or_forward_other_sources(app):
    p=app.extensions['store'].create(solo(), config())
    rules=internet_gateway.firewall_rules(p)
    forwards=[args for table,chain,_,args in rules if table=='filter' and chain=='FORWARD']
    outgoing=next(a for a in forwards if '-j' in a and a[-1]=='ACCEPT' and 'tun0' in a and a[a.index('-o')+1]=='tun0')
    assert outgoing[outgoing.index('-i')+1]==internet_gateway.INTERFACE
    assert outgoing[outgoing.index('-s')+1]=='10.250.0.2/32'
    assert any(a[-1]=='DROP' and '-s' in a for a in forwards)
    assert all('tun0' in a for table,_,_,a in rules if table=='nat')
    # Docker's UDP proxy can originate a valid peer's handshake after restarting.
    udp=next(a for table,chain,_,a in rules if table=='filter' and chain=='INPUT' and '51821' in a)
    assert '-s' not in udp and udp[-1]=='ACCEPT'


@pytest.mark.parametrize('rule',[
    {'priority':97,'src':'all','dst':'10.250.250.0','dstlen':30,'table':'main'},
    {'priority':97,'src':'all','dst':'10.250.250.0/30','table':254},
])
def test_policy_rule_is_recognised_across_iproute2_formats(rule):
    assert internet_gateway.private_rule_present([rule], '10.250.250.0/30')
    assert not internet_gateway.private_rule_present([rule], '10.250.251.0/30')


def test_runtime_helper_stays_inside_owned_vpn_namespace(app):
    store=app.extensions['store'];p=store.create(solo(),config());folder=store.directory(p['id'])
    (folder/'internet-runtime.json').write_text(json.dumps({'host':'192.168.1.110','port':51877}))
    client=MagicMock();client.containers.get.side_effect=__import__('docker').errors.NotFound('none')
    vpn=MagicMock();vpn.id='vpn-namespace'
    vpn.attrs={'NetworkSettings':{'Ports':{'51821/udp':[{'HostPort':'51877'}]}}}
    rt=Runtime(store,client);rt.internet_reconcile(p,vpn,folder,folder)
    kwargs=client.containers.create.call_args.kwargs
    assert kwargs['network_mode']=='container:vpn-namespace'
    assert kwargs['cap_add']==['NET_ADMIN'] and kwargs['cap_drop']==['ALL']
    assert kwargs['read_only'] is True
    assert all('wg0.conf' not in name and 'client.key' not in name for name in kwargs['volumes'])


def test_local_port_is_persisted_and_not_reallocated_on_restart(app):
    store=app.extensions['store'];p=store.create(solo(),config());folder=store.directory(p['id'])
    client=MagicMock();client.containers.run.return_value=b'51999\n'
    rt=Runtime(store,client)
    assert rt.internet_port(folder)==51999
    restarted=Runtime(store,client)
    assert restarted.internet_port(folder)==51999
    assert client.containers.run.call_count==1
    assert not internet_gateway.config_ready(folder)  # Host discovery has not completed.


def test_solo_options_round_trip_in_browser(app):
    import threading
    from pathlib import Path
    from werkzeug.serving import make_server
    from playwright.sync_api import sync_playwright, expect
    store=app.extensions['store']
    p=store.create({**solo(), 'internet_gateway':{'enabled':False}},config())
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            page=browser.new_page(viewport={'width':1440,'height':1050})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            password=(store.root/'initial-login.txt').read_text().splitlines()[1].split(': ',1)[1]
            page.locator('[name=username]').fill('admin');page.locator('[name=password]').fill(password)
            page.get_by_role('button',name='Log ind').click()
            card=page.locator('.vpn-card').filter(has_text='Plex')
            card.get_by_role('button',name='Indstillinger',exact=True).click()
            page.locator('#internet-enabled').check()
            page.locator('#solo-plex-enabled').check()
            page.locator('#solo-plex-token').fill(TOKEN)
            expect(page.locator('#relay-enabled')).to_be_checked()
            page.locator('#save-button').click()
            expect(page.locator('#profile-dialog')).not_to_be_visible()
            saved=store.get(p['id'])
            assert saved['internet_gateway']['enabled'] and saved['plex_sync']['enabled']
            folder=store.directory(p['id'])
            (folder/'internet-runtime.json').write_text(json.dumps({'host':'192.168.1.110','port':51877}))
            page.reload()
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':1050})
                card.get_by_role('button',name='Indstillinger',exact=True).click()
                expect(page.locator('#internet-enabled')).to_be_checked()
                expect(page.locator('#solo-plex-enabled')).to_be_checked()
                expect(page.locator('#solo-plex-token')).to_have_value('')
                expect(page.locator('#internet-download')).to_be_visible()
                assert page.locator('#profile-dialog').evaluate('(d)=>d.scrollWidth<=d.clientWidth+1')
                if width==1440:
                    Path('test-results').mkdir(exist_ok=True)
                    page.screenshot(path='test-results/solo-internet-options.png')
                page.locator('#save-button').click()
                expect(page.locator('#profile-dialog')).not_to_be_visible()
            assert not errors
            browser.close()
    finally:
        server.shutdown()
