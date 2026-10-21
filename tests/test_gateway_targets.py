"""Multiple named targets retain independent leases, DNS ownership and routes."""
import copy
import json
from unittest.mock import Mock, patch

import pytest

from gateway import settings, process_key, forwarding_config, game_rows
from relay import gateway_tcp as tcp
from runtime import Runtime
from test_gateway import gateway, NETWORKS, fixture_sync
from test_gateway_cleanup import managed
from test_app import config


def target(ident='0', host='192.168.1.96'):
    return dict(id=ident,name='Plex '+ident,host=host,port=32400)


def game(ident='0',key='b'*32):
    return dict(id=ident,domain='mc'+ident+'.example.com',host='192.168.1.152',port=25565,key_id=key)


def test_list_validation_limits_ids_names_and_domains():
    data=gateway(tcp={'enabled':True,'targets':[target(),target('1')]},gameservers=[game(),game('1')])
    valid=settings(data,NETWORKS)
    assert len(valid['tcp']['targets'])==len(valid['gameservers'])==2
    assert len(tcp.target_rows(forwarding_config(valid)))==4
    for changes in [dict(tcp={'enabled':True,'targets':[target(),target()]}),
                    dict(tcp={'enabled':True,'targets':[target('25')]}),
                    dict(tcp={'enabled':True,'targets':[dict(target(),name='')]}),
                    dict(tcp={'enabled':True,'targets':[]}),
                    dict(gameservers=[game(),game()]),dict(gameservers=[game('25')]),
                    dict(gameservers=[dict(game(),domain='app.example.com')]),
                    dict(gameservers=[dict(game(),host='8.8.8.8')]),
                    dict(gameservers=[dict(game(),key_id='invalid')])]:
        with pytest.raises(ValueError):settings({**data,**changes},NETWORKS)
    with pytest.raises(ValueError):settings({**data,'gameservers':[game(str(i)) for i in range(26)]},NETWORKS)


def test_legacy_conversion_keeps_endpoint_and_shared_game_settings():
    legacy=gateway(tcp={'enabled':True,'host':'192.168.1.96','port':32400},
        minecraft={'enabled':True,**{k:v for k,v in game().items() if k!='id'}})
    valid=settings(legacy,NETWORKS)
    assert valid['tcp']['targets']==[dict(id='0',name='TCP',host='192.168.1.96',port=32400)]
    assert valid['minecraft']==legacy['minecraft']
    assert process_key(valid,40000,b'key')==process_key(settings({**legacy,'gameservers':[game('1')]},NETWORKS),40000,b'key')


def test_multiple_target_leases_and_removal_are_independent(monkeypatch):
    processes=[]
    def spawn(*args,**kwargs):
        process=Mock();process.poll.return_value=None;processes.append(process);return process
    monkeypatch.setattr(tcp.subprocess,'Popen',spawn)
    calls=[]
    def request(protocol,private,public=0,lifetime=60):
        calls.append((protocol,private,public,lifetime))
        return public or 62000+private-56800
    monkeypatch.setattr(tcp,'request',request)
    stop=Mock();pool=tcp.Forwarders(stop)
    cfg={'enabled':True,'targets':[target(),target('1'),dict(target('g0'),port=25565)]}
    def update(value=cfg):return pool.update(value,'185.111.109.2',63503,'revision',True)
    states=update()
    assert {s['public_port'] for s in states.values()}=={62000,62002,62050}
    first=pool.workers['0'];second=pool.workers['1'];games=pool.workers['g0']
    stop.reset_mock();calls.clear()
    update({'enabled':True,'targets':list(reversed(cfg['targets']))})
    assert not calls and len(processes)==3
    stop.assert_not_called()
    update({'enabled':True,'targets':[cfg['targets'][1],cfg['targets'][2]]})
    assert pool.workers['1'] is second and pool.workers['g0'] is games
    assert len(processes)==3 and first.process is None
    assert all(private==56800 for _,private,_,_ in calls)
    stop.assert_any_call(processes[0])
    pool.close()


def test_one_provider_failure_does_not_close_other_target(monkeypatch):
    process=Mock();process.poll.return_value=None
    monkeypatch.setattr(tcp.subprocess,'Popen',Mock(return_value=process))
    def request(protocol,private,public=0,lifetime=60):
        if private==56802:raise OSError('unavailable')
        return public or 62000
    monkeypatch.setattr(tcp,'request',request)
    pool=tcp.Forwarders(Mock())
    states=pool.update({'enabled':True,'targets':[target(),target('1')]},'1.1.1.1',40000,'r',True)
    assert states['0']['state']=='ready' and states['1']['state']=='error'
    assert pool.workers['0'].process is process
    pool.close()


def test_multiple_gameservers_get_distinct_srv_ports(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path)
    p=store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'gameservers':[game(key=p['gateway']['key_id']),game('1',p['gateway']['key_id'])]}})
    observed['tcp_status']={'g0':{'state':'ready','public_port':62050},'g1':{'state':'ready','public_port':62052}}
    with patch('gateway.public_probe',return_value={'name':'Paper'}) as probe:
        urls=sync.sync(p,observed,{})
    srv=[c.kwargs['json'] for c in sync.cf.call_args_list if c.args[1]!='GET' and c.kwargs.get('json',{}).get('type')=='SRV']
    assert {(r['name'],r['data']['port']) for r in srv}=={('_minecraft._tcp.mc0.example.com',62050),('_minecraft._tcp.mc1.example.com',62052)}
    assert probe.call_count==2 and 'minecraft://mc1.example.com' in urls


def test_game_domain_reservation_and_key_zone_are_checked(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path)
    key=p['gateway']['key_id']
    p=store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'gameservers':[game(key=key)]}})
    with pytest.raises(ValueError):store.create({'name':'Conflict','gateway':gateway(key,gameservers=[game(key=key)])},config(2))
    with pytest.raises(ValueError):store.create({'name':'Legacy','stream_sync':{'enabled':True,'domain':game()['domain'],'key_id':key,'fjordflix':False}},config(3))
    with pytest.raises(ValueError):store.save_key({'name':'New zone','zone_id':'c'*32,'token':'a'*40},key)


def test_removing_one_game_queues_only_its_dns_records(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path)
    key=p['gateway']['key_id']
    p=store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'gameservers':[game(key=key),game('1',key)]}})
    p=store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'gameservers':p['gateway']['gameservers'][1:]}})
    pending=store.gateway_dns(p['id'])['pending']
    assert {r['domain'] for r in pending}=={'mc0.example.com','_minecraft._tcp.mc0.example.com'}
    assert len(p['gateway']['routes'])==2


def test_cleanup_keeps_other_games_and_existing_web_records(tmp_path):
    sync,p,observed,store,records,rules=managed(tmp_path)
    key=p['gateway']['key_id']
    p=store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'gameservers':[game(key=key),game('1',key)]}})
    observed['tcp_status']={'g0':{'state':'ready','public_port':62050},'g1':{'state':'ready','public_port':62052}}
    with patch('gateway.public_probe',return_value={'name':'Paper'}):sync.sync(p,observed,{})
    names={r['name'] for r in records.values()}
    assert len(names)==6
    retained={r['id']:copy.deepcopy(r) for r in records.values() if 'mc0.example.com' not in r['name']}
    p=store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'gameservers':p['gateway']['gameservers'][1:]}})
    sync.cleanup(p)
    assert records==retained
    assert rules['rules'][0]=={'id':'unrelated','ref':'someone_else'}


def test_firewall_removes_only_deleted_targets_ports(tmp_path):
    from unittest.mock import MagicMock
    rt=Runtime(Mock(),MagicMock());vpn=Mock()
    rules=set()
    def command(args):
        op=args[1];port=args[args.index('--dport')+1]
        if op=='-C':return Mock(exit_code=0 if port in rules else 1)
        if op=='-I':rules.add(port)
        if op=='-D':rules.remove(port)
        return Mock(exit_code=0)
    vpn.exec_run.side_effect=command
    p={'id':'a'*32,'gateway':{'tcp':{'enabled':True,'targets':[target(),target('1')]},'gameservers':[game()]}}
    (tmp_path/'observed').mkdir()
    rt.tcp_firewall(vpn,p,tmp_path)
    assert rules=={'56800','56801','56802','56803','56850','56851'}
    p['gateway']['tcp']['targets']=p['gateway']['tcp']['targets'][1:]
    rt.tcp_firewall(vpn,p,tmp_path)
    assert rules=={'56802','56803','56850','56851'}


def test_tcp_only_gateway_readiness_does_not_require_dns_sync(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path)
    p=store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'routes':[],
        'auto_sync':False,'tcp':{'enabled':True,'targets':[target()]}}})
    observed['tcp_status']={'0':{'state':'ready','public_port':62000}}
    sync.cf.reset_mock();sync.tick()
    assert sync.status(p,observed)['state']=='ready'
    sync.cf.assert_not_called()
    observed['tcp_status']['0']={'state':'error','public_port':None}
    assert sync.status(p,observed)=={}
    sync.tick()
    assert sync.status(p,observed)['state']=='error'
