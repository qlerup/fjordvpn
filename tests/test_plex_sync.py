import copy
import json
import xml.etree.ElementTree as ET
from unittest.mock import Mock,MagicMock

import pytest
import requests

from plex_sync import PlexSync
from test_gateway import gateway,store_with_key
from test_app import config,create_app,FakeRuntime,login

TOKEN='private-plex-token-123456789'
UUID='a'*32


def target(**changes):
    return {'id':'0','name':'Plex','host':'192.168.1.96','port':32400,'plex_sync':True,**changes}


def settings(key,**changes):
    return gateway(key,tcp={'enabled':True,'targets':[target(plex_token=TOKEN)]},**changes)


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    store,key=store_with_key(tmp_path)
    p=store.create({'name':'Gateway','gateway':settings(key)},config())
    p=store.power(p['id'],True)
    observed=dict(state='online',public_ip='185.111.109.2',tcp_status={'0':{'state':'ready','public_port':41853}})
    runtime=Mock();runtime.status.return_value=observed
    sync=PlexSync(store,runtime)
    values={'customConnections':'https://keep.example:443','CertificateUUID':UUID,'secureConnections':'1'}
    calls=[]
    def xml(session,method,url,token=None,params=None):
        calls.append((method,url,token,params))
        if url.endswith('/identity'):
            return ET.Element('MediaContainer',machineIdentifier='plex-server')
        if method=='PUT':
            values.update(params);return None
        root=ET.Element('MediaContainer')
        for k,v in values.items():ET.SubElement(root,'Setting',id=k,value=v)
        return root
    sync.xml=Mock(side_effect=xml)
    return sync,store,p,observed,values,calls,xml


def test_token_is_private_not_in_profile_observer_or_api(tmp_path):
    app=create_app(tmp_path,testing=True,runtime_factory=FakeRuntime);store=app.extensions['store']
    key=store.save_key({'name':'Example','zone_id':'b'*32,'token':'a'*40})
    p=store.create({'name':'Gateway','gateway':settings(key)},config())
    folder=store.directory(p['id'])
    assert store.plex_token_path(p['id'],'0').read_text()==TOKEN
    assert TOKEN not in (folder/'profile.json').read_text()
    assert TOKEN not in (folder/'relay/settings.json').read_text()
    client,_=login(app)
    assert TOKEN not in client.get('/api/profiles').get_data(as_text=True)
    assert TOKEN not in json.dumps(p)


def test_blank_token_is_preserved_but_retarget_requires_explicit_token(tmp_path):
    store,key=store_with_key(tmp_path)
    p=store.create({'name':'Gateway','gateway':settings(key)},config())
    p=store.edit(p['id'],{'name':'Renamed','gateway':p['gateway']})
    assert store.plex_token_path(p['id'],'0').read_text()==TOKEN
    changed=copy.deepcopy(p['gateway']);changed['tcp']['targets'][0]['host']='192.168.1.97'
    with pytest.raises(ValueError,match='Plex-token'):
        store.edit(p['id'],{'name':p['name'],'gateway':changed})
    changed['tcp']['targets'][0]['plex_token']='new-plex-token-987654321'
    store.edit(p['id'],{'name':p['name'],'gateway':changed})
    assert store.plex_token_path(p['id'],'0').read_text()=='new-plex-token-987654321'


@pytest.mark.parametrize('row',[target(plex_sync='yes',plex_token=TOKEN),target(),target(plex_token='bad secret')])
def test_invalid_or_missing_credential_rejected_without_leaking_it(tmp_path,row):
    store,key=store_with_key(tmp_path)
    with pytest.raises(ValueError) as e:
        store.create({'name':'Gateway','gateway':gateway(key,tcp={'enabled':True,'targets':[row]})},config())
    assert TOKEN not in str(e.value) and 'bad secret' not in str(e.value)


def test_only_one_controller_per_plex_server(tmp_path):
    store,key=store_with_key(tmp_path)
    store.create({'name':'First','gateway':settings(key)},config())
    with pytest.raises(ValueError,match='Plex-server'):
        store.create({'name':'Second','gateway':settings(key,routes=[{'domain':'new.example.com','host':'192.168.1.110','port':80}])},config(2))


def test_verified_endpoint_updates_only_custom_connections_and_no_public_token(fixture):
    sync,store,p,observed,values,calls,xml=fixture
    sync.tick()
    endpoint=f'https://185-111-109-2.{UUID}.plex.direct:41853'
    assert values['customConnections']=='https://keep.example:443,'+endpoint
    assert values['secureConnections']=='1'
    assert sync.status(p,observed)['0']['url']==endpoint
    assert all(not token for method,url,token,params in calls if url.startswith('https://'))
    assert all(set(params)=={'customConnections'} for method,url,token,params in calls if method=='PUT')
    count=len(calls);sync.tick();assert len(calls)==count


def test_changed_ip_port_replaces_only_managed_url_even_after_restart(fixture):
    sync,store,p,observed,values,calls,xml=fixture
    sync.tick()
    old=sync.status(p,observed)['0']['url']
    values['customConnections']+=',https://another.example:443'
    observed['public_ip']='1.1.1.1';observed['tcp_status']['0']['public_port']=62000
    assert sync.status(p,observed)['0']['state']=='pending'
    restarted=PlexSync(store,sync.runtime);restarted.xml=Mock(side_effect=xml)
    restarted.tick()
    assert old not in values['customConnections']
    assert values['customConnections'].split(',')==['https://keep.example:443','https://another.example:443',f'https://1-1-1-1.{UUID}.plex.direct:62000']


@pytest.mark.parametrize('failure',['tls','wrong-server','offline'])
def test_failed_public_check_preserves_advertised_urls(fixture,failure):
    sync,store,p,observed,values,calls,xml=fixture
    before=values['customConnections']
    def fail(session,method,url,token=None,params=None):
        if url.startswith('https:'):
            if failure=='tls':raise requests.exceptions.SSLError('must not leak '+TOKEN)
            if failure=='offline':raise requests.ConnectionError('must not leak '+TOKEN)
            return ET.Element('MediaContainer',machineIdentifier='other-server')
        return xml(session,method,url,token,params)
    sync.xml=Mock(side_effect=fail);sync.tick()
    assert values['customConnections']==before
    assert not any(method=='PUT' for method,*_ in calls)
    status=sync.status(p,observed)['0']
    assert status['state']=='error' and TOKEN not in json.dumps(status)


def test_stale_profile_cannot_change_plex(fixture):
    sync,store,p,observed,values,calls,xml=fixture
    def changed(session,method,url,token=None,params=None):
        if url.startswith('https:'):
            store.edit(p['id'],{'name':'Changed','gateway':p['gateway']})
        return xml(session,method,url,token,params)
    sync.xml=Mock(side_effect=changed);sync.tick()
    assert values['customConnections']=='https://keep.example:443'
    assert not any(method=='PUT' for method,*_ in calls)


def test_removed_target_stops_sync_and_deletes_credential(fixture):
    sync,store,p,observed,values,calls,xml=fixture
    sync.tick();calls.clear()
    cfg=copy.deepcopy(p['gateway']);cfg['tcp']={'enabled':False}
    store.edit(p['id'],{'name':p['name'],'gateway':cfg})
    sync.tick()
    assert not calls and not store.plex_token_path(p['id'],'0').exists()
    assert not sync.statuses


def test_xml_helper_never_follows_redirects_and_rejects_auth_errors():
    session=MagicMock();response=session.request.return_value.__enter__.return_value
    response.status_code=401
    with pytest.raises(ValueError,match='Plex-login'):
        PlexSync.xml(session,'GET','http://192.168.1.96:32400/:/prefs',TOKEN)
    kwargs=session.request.call_args.kwargs
    assert kwargs['allow_redirects'] is False and kwargs['headers']=={'X-Plex-Token':TOKEN}
