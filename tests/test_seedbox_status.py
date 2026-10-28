import io
import json
import tarfile
import time
from unittest.mock import MagicMock

import pytest
from runtime import Runtime, LABEL, OWNER
from test_app import app, config


@pytest.fixture
def fixture():
    client = MagicMock()
    runtime = Runtime(MagicMock(), client)
    runtime.project = 'vpn'
    vpn = MagicMock(id='vpn-id', status='running')
    vpn.labels = {LABEL:'profile', OWNER:'1', 'com.docker.compose.project':'vpn'}
    vpn.attrs = {'State':{'StartedAt':'started'}}
    child = MagicMock(status='running')
    child.labels = {'dk.fjordseed.vpn-project':'vpn', 'dk.fjordseed.vpn-generation':'vpn-id:started',
                    'dk.fjordseed.torrent-internal-port':'56789'}
    child.attrs = {'HostConfig':{'NetworkMode':'container:vpn-id'}}
    client.containers.list.return_value = [child]
    client.containers.get.return_value = vpn
    status = dict(checked_at=time.time(), ready=True, torrent_mode='gateway',
                  public_ip='1.2.3.4', public_torrent_port=50294, internal_port=56789)
    def report():
        buf = io.BytesIO()
        data = json.dumps(status).encode()
        with tarfile.open(fileobj=buf, mode='w') as archive:
            member = tarfile.TarInfo('worker-status.json');member.size=len(data)
            archive.addfile(member, io.BytesIO(data))
        return iter([buf.getvalue()]), {}
    child.get_archive.side_effect = lambda _:report()
    def check():
        return runtime.seedbox_status({'id':'profile','desired':True,'gateway':{'enabled':status.get('_gateway',True)}},
                                      {'state':'online','public_ip':'1.2.3.4','public_port':50294})
    return check, status, child, client


def test_ready_reports_only_public_connection_data(fixture):
    check, status, child, client = fixture
    status['secret']='never-return'
    result=check()
    assert result['state']=='ready' and result['public_port']==50294
    assert 'never-return' not in json.dumps(result)
    child.get_archive.assert_called_once_with('/config/worker-status.json')


@pytest.mark.parametrize('change',[
    {'checked_at':0}, {'checked_at':time.time()+100}, {'ready':False},
    {'public_ip':'9.9.9.9'}, {'public_torrent_port':0}, {'public_torrent_port':True},
    {'internal_port':55555}, {'torrent_mode':'dedicated'}])
def test_invalid_or_stale_worker_is_never_green(fixture, change):
    check,status,_,_=fixture;status.update(change)
    assert check()['state']=='pending'


def test_restarted_namespace_and_stopped_client(fixture):
    check,_,child,_=fixture
    child.labels['dk.fjordseed.vpn-generation']='old'
    assert check()['state']=='pending'
    child.get_archive.assert_not_called()
    child.status='exited'
    assert check()['state']=='off'


def test_foreign_installation_hidden_and_read_failure_red(fixture):
    check,_,child,_=fixture
    child.get_archive.side_effect=ValueError('secret')
    assert check()['state']=='error' and 'secret' not in check()['message']
    child.labels['dk.fjordseed.vpn-project']='foreign'
    assert check() is None


def test_solo_uses_actual_namespace_and_current_vpn_port(fixture):
    check, status, child, client = fixture
    status.update(_gateway=False, torrent_mode='dedicated', internal_port=50294)
    child.labels['dk.fjordseed.torrent-internal-port']='0'
    assert check()['state']=='ready'
    assert 'torrent-mode=gateway' not in str(client.containers.list.call_args)
    status['public_torrent_port']=50295
    assert check()['state']=='pending'
    status['public_torrent_port']=50294
    child.attrs['HostConfig']['NetworkMode']='container:old-vpn'
    assert check()['state']=='pending'


def test_unattached_profile_has_no_seedbox(fixture):
    check, _, _, client = fixture
    client.containers.list.return_value=[]
    assert check() is None


def test_solo_connection_card_desktop_and_mobile(app):
    import threading
    from pathlib import Path
    from werkzeug.serving import make_server
    from playwright.sync_api import sync_playwright, expect
    store=app.extensions['store']
    store.create({'name':'FjordSeed - Ekstra'}, config())
    runtime=app.extensions['runtime'];original=runtime.status
    runtime.status=lambda p:dict(original(p), seedbox_status={
        'state':'ready','public_port':33138,'message':'Download og upload bruger denne VPN.'})
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            page=browser.new_page()
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            password=(store.root/'initial-login.txt').read_text(encoding='utf-8').splitlines()[1].split(': ',1)[1]
            page.locator('[name=username]').fill('admin')
            page.locator('[name=password]').fill(password)
            page.get_by_role('button',name='Log ind').click()
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':1000})
                expect(page.locator('.connected-apps-label')).to_have_text('Tilknyttede apps')
                expect(page.locator('.seedbox-status')).to_contain_text('FjordSeed · Forbundet')
                expect(page.locator('.seedbox-status')).to_contain_text('TCP/UDP :33138')
                assert page.locator('.vpn-card').evaluate('(d)=>d.scrollWidth<=d.clientWidth+1')
                Path('test-results').mkdir(exist_ok=True)
                page.screenshot(path=f'test-results/connections-{width}.png')
            assert not errors
            browser.close()
    finally:
        server.shutdown()
