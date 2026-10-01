import ipaddress
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import docker
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from runtime import Runtime, OWNER, LABEL
from store import Store
from test_app import config


def test_packaged_runtime_uses_host_mounts_and_compose_ownership(tmp_path,monkeypatch):
    import runtime
    monkeypatch.setenv('FJORDVPN_CONTAINER','1')
    monkeypatch.setenv('HOSTNAME','manager-id')
    monkeypatch.setenv('HOST_DATA_DIR','/srv/fjordvpn-data')
    monkeypatch.setattr(runtime.os,'getuid',lambda:0,raising=False)
    monkeypatch.setattr(runtime.os,'getgid',lambda:0,raising=False)
    store=Store(tmp_path,[ipaddress.ip_network('192.168.1.0/24')])
    p=store.create({'name':'test'},config());p=store.power(p['id'],True)
    parent=MagicMock();parent.labels={'com.docker.compose.project':'fjordvpn-hub'}
    parent.attrs={'Image':'sha256:test-image','Mounts':[{'Source':'/srv/fjordvpn-data','Destination':str(tmp_path),'Type':'bind'}]}
    client=MagicMock();client.containers.get.side_effect=[parent,docker.errors.NotFound('no'),docker.errors.NotFound('no')]
    vpn=MagicMock();vpn.id='vpn-child';vpn.status='created';relay=MagicMock();relay.status='created'
    client.containers.create.side_effect=[vpn,relay]
    rt=Runtime(store,client);rt.reconcile(p)
    calls=client.containers.create.call_args_list
    assert calls[1].args==('sha256:test-image',)
    assert calls[1].kwargs['command']==['python','/app/relay/observer.py']
    for call in calls:
        assert call.kwargs['labels']['com.docker.compose.project']=='fjordvpn-hub'
        assert call.kwargs['labels'][LABEL]==p['id']
        assert call.kwargs['restart_policy']=={'Name':'no'}
        assert all(str(tmp_path) not in path for path in call.kwargs['volumes'])
    vpn.labels=relay.labels=rt.labels(p['id'],'vpn');vpn.status=relay.status='running'
    client.containers.get.side_effect=[relay,vpn]
    rt.shutdown()
    relay.stop.assert_called_once();vpn.stop.assert_called_once()
    assert store.get(p['id'])['desired'] is True # Restart restores desired state.
    assert (store.directory(p['id'])/'wg0.conf').exists()


def test_manifest_assets_are_real():
    root=Path(__file__).resolve().parents[1]
    manifest=json.loads((root/'fjordhub.json').read_text(encoding='utf-8'))
    assert manifest['id']=='fjordvpn'
    assert manifest['compose_file']=='docker-compose.yml'
    assert (root/'static/brand/fjordvpn-icon-512.png').stat().st_size>1000
    assert (root/'Dockerfile').exists()


def test_mismatched_host_path_cannot_start_container(tmp_path,monkeypatch):
    monkeypatch.setenv('FJORDVPN_CONTAINER','1');monkeypatch.setenv('HOSTNAME','manager')
    monkeypatch.setenv('HOST_DATA_DIR','/wrong')
    store=Store(tmp_path,[ipaddress.ip_network('192.168.1.0/24')])
    parent=MagicMock();parent.attrs={'Image':'sha256:test','Mounts':[{'Source':'/right','Destination':str(tmp_path),'Type':'bind'}]}
    client=MagicMock();client.containers.get.return_value=parent
    with pytest.raises(RuntimeError):Runtime(store,client)
    client.containers.create.assert_not_called()
