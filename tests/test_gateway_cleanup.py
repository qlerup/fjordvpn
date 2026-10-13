"""Cloudflare cleanup is scoped, durable, and safe during hostname handoff."""
import copy
from unittest.mock import Mock

import pytest
import requests

from gateway import GatewaySync
from store import Store
from test_gateway import fixture_sync,NETWORKS
from test_app import config


def managed(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path)
    records={};rules={'id':'ruleset','rules':[{'id':'unrelated','ref':'someone_else'}]}
    def cf(token,method,path,**kwargs):
        body=kwargs.get('json',{})
        if path.endswith('/dns_records') and method=='GET':
            return [copy.deepcopy(r) for r in records.values() if r['name']==kwargs['params']['name']]
        if '/dns_records' in path:
            if method=='POST':
                record={**body,'id':'record-'+body['name']};records[record['id']]=record
            elif method=='PATCH':
                ident=path.split('/')[-1];records[ident].update(body);record=records[ident]
            elif method=='DELETE':
                records.pop(path.split('/')[-1],None);return {}
            return copy.deepcopy(record)
        if path.endswith('/entrypoint'):return copy.deepcopy(rules)
        if '/rulesets/' in path:
            if method=='POST':rules['rules'].append({**body,'id':'owned'})
            elif method=='PATCH':
                next(r for r in rules['rules'] if r['id']==path.split('/')[-1]).update(body)
            elif method=='DELETE':rules['rules'][:]=[r for r in rules['rules'] if r['id']!=path.split('/')[-1]]
            return {'id':'owned'}
        return {'name':'example.com'}
    sync.cf=Mock(side_effect=cf);sync.sync(p,observed);sync.cf.reset_mock()
    return sync,p,observed,store,records,rules


def remove(store,p,domains):
    return store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],
        'routes':[r for r in p['gateway']['routes'] if r['domain'] not in domains]}})


def test_removed_target_deletes_only_its_owned_dns_and_rewrites_own_rule(tmp_path):
    sync,p,observed,store,records,rules=managed(tmp_path)
    p=remove(store,p,{'app.example.com'});sync.sync(p,observed)
    assert {r['name'] for r in records.values()}=={'other.example.com'}
    owned=next(r for r in rules['rules'] if r['id']=='owned')
    assert owned['expression']=='http.host in {"other.example.com"}'
    assert rules['rules'][0]=={'id':'unrelated','ref':'someone_else'}
    assert store.gateway_dns(p['id'])['pending']==[]
    assert 'b'*32+':app.example.com' not in store.gateway_dns(p['id'])['records']
    deletes=[c.args[2] for c in sync.cf.call_args_list if c.args[1]=='DELETE']
    assert deletes==['/zones/'+'b'*32+'/dns_records/record-app.example.com']


@pytest.mark.parametrize('changes',[{'content':'8.8.8.8'},{'proxied':False},{'comment':'Another owner'},
                                   {'id':'replacement'},{'type':'CNAME'}])
def test_changed_dns_is_preserved_with_visible_warning(tmp_path,changes):
    sync,p,observed,store,records,_=managed(tmp_path)
    records['record-app.example.com'].update(changes)
    p=remove(store,p,{'app.example.com'});sync.sync(p,observed)
    assert 'record-app.example.com' in records
    assert not any(c.args[1]=='DELETE' for c in sync.cf.call_args_list)
    assert 'app.example.com' in store.gateway_dns(p['id'])['warnings'][0]


def test_partial_cleanup_survives_restart_and_retries_without_deleting_active_dns(tmp_path):
    sync,p,observed,store,records,_=managed(tmp_path)
    p=remove(store,p,{'app.example.com'})
    original=sync.cf.side_effect
    def fail(token,method,path,**kwargs):
        if method=='DELETE':raise requests.ConnectionError('offline')
        return original(token,method,path,**kwargs)
    sync.cf.side_effect=fail
    with pytest.raises(requests.ConnectionError):sync.sync(p,observed)
    assert store.gateway_dns(p['id'])['pending']
    restarted=GatewaySync(Store(tmp_path,NETWORKS),sync.runtime)
    restarted.cf=Mock(side_effect=original);restarted.probe_web=sync.probe_web
    restarted.sync(p,observed)
    assert {r['name'] for r in records.values()}=={'other.example.com'}
    assert restarted.store.gateway_dns(p['id'])['pending']==[]


def test_readded_target_cancels_cleanup(tmp_path):
    sync,p,observed,store,records,_=managed(tmp_path)
    original=p['gateway'];p=remove(store,p,{'app.example.com'})
    p=store.edit(p['id'],{'name':p['name'],'gateway':original});sync.sync(p,observed)
    assert len(records)==2 and store.gateway_dns(p['id'])['pending']==[]
    assert not any(c.args[1]=='DELETE' for c in sync.cf.call_args_list)


def test_untracked_legacy_record_is_not_blindly_deleted(tmp_path):
    sync,p,observed,store,records,_=managed(tmp_path)
    store.save_gateway_dns(p['id'],{'records':{},'pending':[],'warnings':[]})
    p=remove(store,p,{'app.example.com'});sync.sync(p,observed)
    assert 'record-app.example.com' in records and store.gateway_dns(p['id'])['warnings']


def test_handoff_to_another_profile_preserves_dns(tmp_path):
    sync,p,observed,store,records,_=managed(tmp_path)
    p=remove(store,p,{'app.example.com'})
    key=p['gateway']['key_id']
    store.create({'name':'New owner','stream_sync':{'enabled':True,'fjordflix':False,
        'domain':'app.example.com','key_id':key}},config(2))
    sync.sync(p,observed)
    assert len(records)==2 and not any(c.args[1]=='DELETE' for c in sync.cf.call_args_list)


def test_only_owned_origin_rule_is_removed_when_remaining_route_is_direct_media(tmp_path):
    sync,p,_,store,records,rules=managed(tmp_path)
    p=remove(store,p,{'app.example.com'})
    p['gateway']['routes'][0]['fjordflix']=True;store.save(p)
    sync.cleanup(p)
    assert rules['rules']==[{'id':'unrelated','ref':'someone_else'}]
    assert {r['name'] for r in records.values()}=={'other.example.com'}


def test_replacing_key_in_same_zone_uses_new_credentials_for_cleanup(tmp_path):
    sync,p,observed,store,records,_=managed(tmp_path)
    key=store.save_key({'name':'Replacement','zone_id':'b'*32,'token':'n'*40})
    p=store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'key_id':key,
        'routes':[p['gateway']['routes'][1]]}})
    sync.sync(p,observed)
    deletes=[c for c in sync.cf.call_args_list if c.args[1]=='DELETE']
    assert len(deletes)==1 and deletes[0].args[0]=='n'*40
    assert len(records)==1
