import io
import json
import tarfile
import time
from unittest.mock import MagicMock

import pytest
from runtime import Runtime, LABEL, OWNER


@pytest.fixture
def fixture():
    client=MagicMock();runtime=Runtime(MagicMock(),client);runtime.project='vpn'
    vpn=MagicMock(id='vpn-id',status='running')
    vpn.labels={LABEL:'profile',OWNER:'1','com.docker.compose.project':'vpn'}
    vpn.attrs={'State':{'StartedAt':'2026-10-24T00:00:00Z'}}
    child=MagicMock(status='running')
    child.labels={'dk.fjorddrop.vpn-owner':'drop','dk.fjorddrop.vpn-container':'vpn-id',
                  'dk.fjorddrop.vpn-port':'60000','dk.fjordvpn.profile':'profile',
                  'dk.fjorddrop.vpn-project':'vpn'}
    child.attrs={'State':{'StartedAt':'2026-10-24T00:01:00Z'},'HostConfig':{'NetworkMode':'container:vpn-id'}}
    client.containers.get.return_value=vpn;client.containers.list.return_value=[child]
    status={'checked_at':time.time(),'host':'1.2.3.4','port':50294,'local_port':60000,'secret':'must-not-leak'}
    def report(_):
        buf=io.BytesIO();data=json.dumps(status).encode()
        with tarfile.open(fileobj=buf,mode='w') as archive:
            member=tarfile.TarInfo('status.json');member.size=len(data);archive.addfile(member,io.BytesIO(data))
        return iter([buf.getvalue()]),{}
    child.get_archive.side_effect=report
    return runtime,child,vpn,status,client


@pytest.mark.parametrize('gateway',[False,True])
def test_ready_on_single_and_gateway(fixture,gateway):
    runtime,child,_,_,_=fixture
    result=runtime.fjorddrop_status({'id':'profile','desired':True,'gateway':{'enabled':gateway}}, {'state':'online','public_ip':'1.2.3.4'})
    assert result[0]['state']=='ready' and result[0]['public_port']==50294
    assert 'must-not-leak' not in json.dumps(result)
    child.get_archive.assert_called_once_with('/state/status.json')


@pytest.mark.parametrize('change',[{'checked_at':0},{'checked_at':time.time()+100},{'port':True},{'port':0},{'host':'9.9.9.9'},{'local_port':60001}])
def test_invalid_or_stale_report_never_connected(fixture,change):
    runtime,_,_,status,_=fixture;status.update(change)
    assert runtime.fjorddrop_status({'id':'profile','desired':True},{'state':'online','public_ip':'1.2.3.4'})[0]['state']=='pending'


def test_restart_stop_and_disconnect(fixture):
    runtime,child,vpn,_,client=fixture
    check=lambda:runtime.fjorddrop_status({'id':'profile','desired':True},{'state':'online','public_ip':'1.2.3.4'})
    vpn.attrs['State']['StartedAt']='2026-10-24T00:02:00Z'
    assert check()[0]['state']=='pending';child.get_archive.assert_not_called()
    child.status='exited';assert check()[0]['state']=='off'
    client.containers.list.return_value=[];assert check()==[]


def test_existing_drop_versions_and_foreign_profile(fixture):
    runtime,child,_,_,_=fixture
    check=lambda:runtime.fjorddrop_status({'id':'profile','desired':True},{'state':'online','public_ip':'1.2.3.4'})
    child.labels.pop('dk.fjordvpn.profile');child.labels.pop('dk.fjorddrop.vpn-project')
    assert check()[0]['state']=='ready'
    child.labels['dk.fjorddrop.vpn-container']='another-vpn';assert check()==[]


def test_unreadable_status_is_safe(fixture):
    runtime,child,_,_,_=fixture;child.get_archive.side_effect=ValueError('sensitive contents')
    result=runtime.fjorddrop_status({'id':'profile','desired':True},{'state':'online','public_ip':'1.2.3.4'})
    assert result[0]['state']=='error' and 'sensitive' not in json.dumps(result)
