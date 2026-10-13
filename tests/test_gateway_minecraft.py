"""Java shares a gateway port while DNS/SRV belongs to another zone."""
import copy
import json
import socket
import socketserver
import threading
from unittest.mock import Mock,patch

import pytest
from gateway import settings,caddy_config,GatewaySync
from minecraft_gateway import probe,read_exact,read_varint,varint,public_probe
from stream_sync import StreamPending
from test_gateway import NETWORKS,gateway,fixture_sync
from test_gateway_cleanup import managed
from test_app import config


def mc(key='c'*32):
    return {'enabled':True,'domain':'mc.nordprison.dk','host':'192.168.1.152','port':25566,'key_id':key}


@pytest.mark.parametrize('field,value',[('enabled','yes'),('domain','bad/path'),('host','8.8.8.8'),
                                       ('port',True),('key_id','invalid')])
def test_minecraft_validation(field,value):
    data=gateway(minecraft={**mc(),field:value})
    with pytest.raises(ValueError):settings(data,NETWORKS)


def test_minecraft_domain_separate_and_layer4_keeps_tls_original_socket():
    data=gateway(minecraft=mc());valid=settings(data,NETWORKS)
    value=caddy_config(valid['routes'],40000,minecraft=valid['minecraft'])
    wrapper=value['apps']['http']['servers']['gateway']['listener_wrappers'][0]
    assert wrapper['wrapper']=='layer4'
    assert wrapper['routes'][0]['match']==[{'not':[{'regexp':{'count':3,'hex':True,'pattern':'^16030[0-4]$'}}]}]
    assert wrapper['routes'][0]['handle'][0]['upstreams']==[{'dial':['192.168.1.152:25566']}]
    assert 'mc.nordprison.dk' not in value['apps']['tls']['automation']['policies'][0]['subjects']
    data['minecraft']['domain']='app.example.com'
    with pytest.raises(ValueError):settings(data,NETWORKS)


def test_other_zone_gets_dns_only_a_and_srv_without_origin_permissions(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path)
    key=store.save_key({'name':'Minecraft','zone_id':'c'*32,'token':'m'*40})
    p=store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'minecraft':mc(key)}})
    calls=[]
    def cf(token,method,path,**kwargs):
        calls.append((token,method,path,kwargs))
        if method=='GET':
            return [] if path.endswith('/dns_records') else {'name':'nordprison.dk'}
        return {'id':'minecraft-'+kwargs['json']['type']}
    sync.cf=Mock(side_effect=cf)
    with patch('gateway.public_probe',return_value={'name':'Paper 1.16.5','protocol':754}):
        assert sync.sync_minecraft(p,observed)['protocol']==754
    writes=[c for c in calls if c[1]!='GET']
    assert all(c[0]=='m'*40 and '/zones/'+'c'*32 in c[2] for c in calls)
    assert all('/rulesets' not in c[2] for c in calls)
    assert writes[0][3]['json']['proxied'] is False
    assert writes[1][3]['json']['data']=={'priority':0,'weight':0,'port':38850,'target':'mc.nordprison.dk'}
    tracked=store.gateway_dns(p['id'])['records']
    assert tracked['c'*32+':_minecraft._tcp.mc.nordprison.dk']['type']=='SRV'


def test_minecraft_reservation_applies_to_legacy_profiles_and_key_rotation(tmp_path):
    _,p,_,store=fixture_sync(tmp_path)
    key=store.save_key({'name':'MC','zone_id':'c'*32,'token':'m'*40})
    store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'minecraft':mc(key)}})
    with pytest.raises(ValueError):store.create({'name':'Legacy','stream_sync':{'enabled':True,
        'domain':'mc.nordprison.dk','key_id':key,'fjordflix':False}},config(2))
    with pytest.raises(ValueError):store.save_key({'name':'MC','zone_id':'d'*32,'token':'m'*40},key)


def test_disabling_minecraft_cleans_owned_a_and_srv_and_keeps_web_dns(tmp_path):
    sync,p,_,store,records,rules=managed(tmp_path)
    key=store.save_key({'name':'MC','zone_id':'c'*32,'token':'m'*40})
    p=store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'minecraft':mc(key)}})
    marker=sync.record_marker(p)
    values=[{'id':'mc-a','type':'A','name':'mc.nordprison.dk','content':'1.1.1.1','proxied':False,'comment':marker},
            {'id':'mc-srv','type':'SRV','name':'_minecraft._tcp.mc.nordprison.dk','data':{
                'priority':0,'weight':0,'port':38850,'target':'mc.nordprison.dk'},'comment':marker}]
    for row in values:
        records[row['id']]=copy.deepcopy(row);sync.remember_record(p,store.key(key),row,row)
    p=store.edit(p['id'],{'name':p['name'],'gateway':{**p['gateway'],'minecraft':{'enabled':False}}})
    sync.cf.reset_mock();sync.cleanup(p)
    assert len(records)==2 and 'mc-a' not in records and 'mc-srv' not in records
    assert not any('/rulesets' in c.args[2] for c in sync.cf.call_args_list)
    assert rules['rules'][0]=={'id':'unrelated','ref':'someone_else'}


def test_media_failure_does_not_block_minecraft_sync(tmp_path):
    sync,p,observed,store=fixture_sync(tmp_path)
    key=store.save_key({'name':'MC','zone_id':'c'*32,'token':'m'*40})
    p['gateway']['routes'][1]['fjordflix']=True;p['gateway']['minecraft']=mc(key);store.save(p)
    sync.stream.stream_state=Mock(side_effect=ValueError('FjordFlix er slukket.'))
    sync.sync_minecraft=Mock(return_value={'name':'Paper'})
    checks={}
    with pytest.raises(ValueError):sync.sync(p,observed,checks)
    sync.sync_minecraft.assert_called_once()
    assert checks['mc.nordprison.dk']['state']=='ready'
    assert checks['app.example.com']['state']=='ready'
    assert checks['other.example.com']['state']=='error'


def test_real_java_status_probe_and_bounded_parser():
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            data=read_exact(self.request,read_varint(self.request))
            assert b'mc.nordprison.dk' in data and data[0]==0
            assert read_exact(self.request,read_varint(self.request))==b'\x00'
            body=json.dumps({'version':{'name':'Paper 1.16.5','protocol':754}}).encode()
            response=b'\x00'+varint(len(body))+body
            self.request.sendall(varint(len(response))+response)
    with socketserver.TCPServer(('127.0.0.1',0),Handler) as server:
        threading.Thread(target=server.serve_forever,daemon=True).start()
        assert probe('127.0.0.1',server.server_address[1],'mc.nordprison.dk')['protocol']==754
        server.shutdown()
    with patch('minecraft_gateway.socket.getaddrinfo',return_value=[(0,0,0,'',('8.8.8.8',40000))]):
        with pytest.raises(StreamPending):public_probe('mc.nordprison.dk',40000,'1.1.1.1')
    with pytest.raises(ValueError):read_varint(Mock(recv=Mock(return_value=b'\xff')))
