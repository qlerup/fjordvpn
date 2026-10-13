import io
import json
import tarfile
import time
from unittest.mock import MagicMock

import pytest
from runtime import Runtime, LABEL, OWNER


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
        return runtime.seedbox_status({'id':'profile','desired':True},
                                      {'state':'online','public_ip':'1.2.3.4'})
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
