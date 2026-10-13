import io
import ipaddress
import json
from unittest.mock import Mock,patch

import pytest
from gateway import settings,caddy_config,GatewaySync
from store import Store
from seedbox_export import profiles
from stream_sync import StreamPending
import requests
from test_app import config,create_app,FakeRuntime,login


NETWORKS=[ipaddress.ip_network('192.168.1.0/24')]
TOKEN='a'*40


def gateway(key='b'*32,**changes):
    return {'enabled':True,'key_id':key,'routes':[
        {'domain':'app.example.com','host':'192.168.1.110','port':8099,'protocol':'http'},
        {'domain':'other.example.com','host':'192.168.1.111','port':443,'protocol':'https'}],**changes}


@pytest.mark.parametrize('field,value',[('domain','evil.com/path'),('domain','*.example.com'),
    ('host','8.8.8.8'),('host','localhost'),('host','192.168.1.0'),('port',True),('port',65536),
    ('protocol','tcp'),('tls_server_name','bad/path')])
def test_route_validation(field,value):
    data=gateway();data['routes'][0][field]=value
    with pytest.raises(ValueError):settings(data,NETWORKS)


def test_gateway_requires_key_and_unique_routes():
    for data in (gateway(routes=[]),gateway(key=''),gateway(routes=[gateway()['routes'][0]]*2)):
        with pytest.raises(ValueError):settings(data,NETWORKS)


def test_gateway_caddy_routing_tls_and_unknown_host():
    routes=settings(gateway(),NETWORKS)['routes'];value=caddy_config(routes,38850)
    assert value['admin']['disabled']
    server=value['apps']['http']['servers']['gateway']
    assert server['listen']==[':38850'] and server['tls_connection_policies']==[{}]
    assert server['routes'][0]['match']==[{'host':['app.example.com']}]
    assert server['routes'][0]['handle'][0]['routes'][1]['handle'][0]['upstreams']==[{'dial':'192.168.1.110:8099'}]
    tls=server['routes'][1]['handle'][0]['routes'][1]['handle'][0]['transport']['tls']
    assert tls=={'server_name':'other.example.com'}
    assert server['routes'][-1]['handle'][0]['status_code']==404
    assert TOKEN not in json.dumps(value)
    assert 'insecure_skip_verify' not in json.dumps(value)


def store_with_key(tmp_path):
    store=Store(tmp_path,NETWORKS)
    key=store.save_key({'name':'Example','zone_id':'b'*32,'token':TOKEN})
    return store,key


def test_new_gateway_preserves_old_profiles_and_reserves_torrent_port(tmp_path):
    store,key=store_with_key(tmp_path)
    old=store.create({'name':'Seedbox'},config(1));store.power(old['id'],True)
    before=(store.directory(old['id'])/'profile.json').read_bytes()
    p=store.create({'name':'Web gateway','gateway':gateway(key)},config(2))
    assert p['desired'] is False
    assert (store.directory(old['id'])/'profile.json').read_bytes()==before
    assert next(v for v in profiles(tmp_path) if v['id']==p['id'])['relay_enabled'] is True
    relay=json.loads((store.directory(p['id'])/'relay/settings.json').read_text())
    assert relay['mode']=='gateway' and relay['enabled']
    assert TOKEN not in json.dumps(p)
    with pytest.raises(ValueError):store.create({'name':'Duplicate key','gateway':gateway(key)},config(1))
    with pytest.raises(ValueError):store.edit(old['id'],{'name':'Seedbox','gateway':gateway(key)})


def test_conflicting_domains_rotation_and_legacy_stream_reservations(tmp_path):
    store,key=store_with_key(tmp_path)
    p=store.create({'name':'Gateway','gateway':gateway(key)},config(1))
    with pytest.raises(ValueError):store.create({'name':'Other gateway','gateway':gateway(key)},config(2))
    with pytest.raises(ValueError):store.create({'name':'Legacy','stream_sync':{
        'enabled':True,'key_id':key,'fjordflix':False,'domain':'app.example.com'}},config(3))
    with pytest.raises(ValueError):store.save_key({'name':'Changed','zone_id':'c'*32,'token':TOKEN},key)
    store.save_key({'name':'Rotated','zone_id':'b'*32,'token':'r'*40},key)
    assert (store.directory(p['id'])/'relay/gateway-token').read_text()=='r'*40


def test_gateway_api_is_opt_in_and_does_not_expose_credentials(tmp_path):
    app=create_app(tmp_path,testing=True,runtime_factory=FakeRuntime);client,headers=login(app)
    key=app.extensions['store'].save_key({'name':'Example','zone_id':'b'*32,'token':TOKEN})
    def post(h):return client.post('/api/gateways',headers=h,data={'settings':json.dumps({'name':'Gateway','gateway':gateway(key)}),
        'config':(io.BytesIO(config(1)),'gateway.conf')})
    assert post({}).status_code==403
    saved=post(headers);assert saved.status_code==201
    listed=client.get('/api/profiles').json
    assert listed['profiles'][0]['desired'] is False
    assert TOKEN not in json.dumps(listed)


def fixture_sync(tmp_path,records=None,ruleset=None):
    store,key=store_with_key(tmp_path)
    p=store.create({'name':'Gateway','gateway':gateway(key)},config());p=store.power(p['id'],True)
    observed={'state':'online','relay_active':True,'public_ip':'1.1.1.1','public_port':38850}
    runtime=Mock();runtime.status.return_value=observed
    sync=GatewaySync(store,runtime)
    def cf(token,method,path,**kwargs):
        if method!='GET':return {'id':'result'}
        if path.endswith('/dns_records'):return records or []
        if path.endswith('/entrypoint'):return ruleset
        return {'name':'example.com'}
    sync.cf=Mock(side_effect=cf)
    sync.probe_web=Mock(return_value={'state':'ready','message':'HTTPS svarer.'})
    return sync,p,observed,store


def test_cloudflare_creates_proxied_dns_and_one_port_rule_without_replacing_other_rules(tmp_path):
    sync,p,observed,_=fixture_sync(tmp_path,ruleset={'id':'ruleset','rules':[{'id':'other','ref':'someone_else'}]})
    assert sync.sync(p,observed)==['https://app.example.com','https://other.example.com']
    writes=[c for c in sync.cf.call_args_list if c.args[1]!='GET']
    assert len(writes)==3 and all(c.args[1]=='POST' for c in writes)
    rule=writes[0].kwargs['json']
    assert rule['action_parameters']=={'origin':{'port':38850}}
    assert rule['ref']=='fjordvpn_gateway_'+p['id']
    assert all(c.kwargs['json']['proxied'] is True for c in writes[1:])
    assert all('ruleset/rules/other' not in c.args[2] for c in writes)


def test_cloudflare_rejects_competing_dns_before_any_write(tmp_path):
    sync,p,observed,_=fixture_sync(tmp_path,records=[{'type':'CNAME','name':'app.example.com'}])
    with pytest.raises(ValueError):sync.sync(p,observed)
    assert all(c.args[1]=='GET' for c in sync.cf.call_args_list)


def test_stale_profile_cannot_publish_dns_and_legacy_profiles_are_not_synced(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path)
    store.power(p['id'],False)
    with pytest.raises(ValueError):sync.sync(p,observed)
    assert all(c.args[1]=='GET' for c in sync.cf.call_args_list)
    store.create({'name':'Legacy'},config(2));sync.cf.reset_mock();sync.tick()
    sync.cf.assert_not_called()


def test_auto_sync_can_be_disabled_and_reenabled(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path)
    values={'name':p['name'],'gateway':{**p['gateway'],'auto_sync':False}}
    store.edit(p['id'],values);sync.tick();sync.cf.assert_not_called()
    values['gateway']['auto_sync']=True;store.edit(p['id'],values)
    sync.tick();assert sync.cf.called


def test_auto_sync_requires_boolean():
    with pytest.raises(ValueError):settings(gateway(auto_sync='yes'),NETWORKS)


def test_stream_route_validated_and_only_media_paths_are_exposed():
    data=gateway();data['routes'][1]['fjordflix']=True
    routes=settings(data,NETWORKS)['routes']
    match=caddy_config(routes,40000)['apps']['http']['servers']['gateway']['routes'][1]['match']
    assert match==[{'host':['other.example.com'],'path':['/media/*']}]
    data['routes'][0]['fjordflix']=True
    with pytest.raises(ValueError):settings(data,NETWORKS)
    data['routes'][0]['fjordflix']='yes'
    with pytest.raises(ValueError):settings(data,NETWORKS)


def test_gateway_stream_migrates_dns_and_proves_https_before_port_activation(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path,ruleset={'id':'ruleset','rules':[]})
    p['gateway']['routes'][1]['fjordflix']=True;store.save(p)
    sync.stream.flix=Mock(return_value={'media':'https://other.example.com:30000','web':'https://app.example.com'})
    with patch('stream_sync.secrets.token_urlsafe',return_value='nonce'),patch('stream_sync.requests.Session') as session:
        response=session.return_value.__enter__.return_value.get.return_value
        response.status_code=200;response.json.return_value={'nonce':'wrong'}
        with pytest.raises(ValueError,match='HTTPS'):sync.sync(p,observed)
        assert not any(c.args[0]=='activate' for c in sync.stream.flix.call_args_list)
        writes=[c.kwargs['json'] for c in sync.cf.call_args_list if c.args[1]!='GET']
        rule=next(w for w in writes if 'expression' in w)
        assert 'app.example.com' in rule['expression'] and 'other.example.com' not in rule['expression']
        dns=[w for w in writes if w.get('type')=='A']
        assert [(w['name'],w['proxied'],w['ttl']) for w in dns]==[
            ('app.example.com',True,1),('other.example.com',False,60)]
        response.json.return_value={'nonce':'nonce'}
        assert sync.sync(p,observed)==['https://app.example.com','https://other.example.com:38850']
        sync.stream.flix.assert_called_with('activate','https://other.example.com:38850','https://app.example.com')
        assert session.return_value.__enter__.return_value.get.call_args.kwargs['allow_redirects'] is False
        # A changed VPN endpoint must never activate the tested old port.
        sync.stream.flix.reset_mock();sync.runtime.status.return_value={**observed,'public_port':40000}
        with pytest.raises(ValueError):sync.stream.verify_stream('other.example.com',38850,lambda:sync.current(p,observed))
        assert not any(c.args[0]=='activate' for c in sync.stream.flix.call_args_list)


def test_gateway_stream_has_single_writer_across_legacy_and_gateway_profiles(tmp_path):
    store,key=store_with_key(tmp_path)
    legacy=store.create({'name':'Legacy','host':'192.168.1.110','port':443,'relay_enabled':True,
        'stream_sync':{'enabled':True,'key_id':key,'domain':'media.example.com','fjordflix':True}},config(1))
    data=gateway(key);data['routes'][1]['fjordflix']=True
    with pytest.raises(ValueError,match='FjordFlix'):store.create({'name':'Gateway','gateway':data},config(2))
    legacy['stream_sync']['enabled']=False;store.save(legacy)
    store.create({'name':'Gateway','gateway':data},config(2))
    with pytest.raises(ValueError,match='FjordFlix'):store.edit(legacy['id'],{
        'name':'Legacy','host':'192.168.1.110','port':443,'relay_enabled':True,
        'stream_sync':{**legacy['stream_sync'],'enabled':True}})


def test_wrong_stream_domain_keeps_media_dns_and_allows_web_updates(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path)
    p['gateway']['routes'][1]['fjordflix']=True;store.save(p)
    sync.stream.flix=Mock(return_value={'media':'https://unrelated.example.com:30000','web':'https://app.example.com'})
    with pytest.raises(ValueError):sync.sync(p,observed)
    writes=[c.kwargs['json'] for c in sync.cf.call_args_list if c.args[1]!='GET']
    dns=[w for w in writes if w.get('type')=='A']
    assert [w['name'] for w in dns]==['app.example.com']


def test_route_status_distinguishes_web_ready_and_media_pending(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path)
    p['gateway']['routes'][1]['fjordflix']=True;store.save(p)
    sync.stream.stream_state=Mock(return_value={'media':'https://other.example.com:30000','web':'https://app.example.com'})
    sync.stream.verify_stream=Mock(side_effect=StreamPending('Afventer korrekt HTTPS-forbindelse.'))
    sync.tick();status=sync.status(p,observed)
    assert status['state']=='pending' and not status['ok']
    assert status['routes']['app.example.com']['state']=='ready'
    assert status['routes']['other.example.com']['state']=='pending'
    sync.next_check.clear();sync.stream.verify_stream.side_effect=None
    sync.stream.verify_stream.return_value='https://other.example.com:38850'
    sync.tick();assert sync.status(p,observed)['state']=='ready'
    # A previously working domain turns red if its next check fails.
    sync.next_check.clear();sync.stream.verify_stream.side_effect=requests.ConnectionError('private diagnostic')
    sync.tick();status=sync.status(p,observed)
    assert status['state']=='error' and status['routes']['app.example.com']['state']=='ready'
    assert 'private diagnostic' not in json.dumps(status)


def test_route_readiness_is_discarded_after_edit_reconnect_or_stale_check(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path);sync.tick()
    assert sync.status(p,observed)['state']=='ready'
    assert '_signature' not in sync.status(p,observed)
    assert sync.status(p,{**observed,'public_port':40000})=={}
    changed=store.edit(p['id'],{'name':'Edited','gateway':p['gateway']})
    assert sync.status(changed,observed)=={}
    sync.tick();assert sync.status(changed,observed)['state']=='ready'
    sync.statuses[p['id']]['_checked']-=91
    assert sync.status(changed,observed)=={}


def test_configuration_failure_is_red_without_false_ready_status(tmp_path):
    sync,p,observed,_=fixture_sync(tmp_path,records=[{'type':'CNAME','name':'app.example.com'}])
    sync.tick();status=sync.status(p,observed)
    assert status['state']=='error'
    assert all(r['state']=='error' for r in status['routes'].values())
    sync.probe_web.assert_not_called()


@pytest.mark.parametrize('code,state',[(200,'ready'),(302,'ready'),(401,'ready'),(404,'pending'),(526,'pending'),(500,'error'),(403,'error')])
def test_https_probe_uses_verified_tls_no_redirect_and_no_body_download(tmp_path,code,state):
    sync,p,observed,_=fixture_sync(tmp_path)
    with patch('gateway.requests.Session') as session:
        client=session.return_value.__enter__.return_value
        client.get.return_value.status_code=code
        result=GatewaySync.probe_web(sync,p,observed,p['gateway']['routes'][0])
        assert result['state']==state
        client.get.assert_called_once_with('https://app.example.com',timeout=8,allow_redirects=False,stream=True)
        assert client.trust_env is False
        client.get.return_value.close.assert_called_once()


def test_setup_failure_turns_red_after_five_minutes(tmp_path):
    sync,p,observed,_=fixture_sync(tmp_path)
    with patch('gateway.time.monotonic',return_value=100):
        assert sync.waiting(p,observed,'app.example.com','Afventer DNS.')['state']=='pending'
    with patch('gateway.time.monotonic',return_value=401):
        assert sync.waiting(p,observed,'app.example.com','Afventer DNS.')['state']=='error'
