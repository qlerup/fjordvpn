"""Publish verified gateway TCP endpoints through Plex's local preferences API."""
import ipaddress
import json
import re
import time
import xml.etree.ElementTree as ET

import requests
from configuration import plex_targets


def port_state(profile, observed, row):
    if row['id'] == 'solo':
        return {'state':'ready' if observed.get('relay_active') and observed.get('state') == 'online' else 'pending',
                'public_port':observed.get('public_port')}
    return observed.get('tcp_status', {}).get(row['id'], {})


class PlexSync:
    def __init__(self,store,runtime):
        self.store,self.runtime=store,runtime
        self.statuses,self.next_check={},{}

    @staticmethod
    def signature(profile,observed,row):
        status=port_state(profile, observed, row)
        return (profile['revision'],observed.get('public_ip'),status.get('state'),status.get('public_port'))

    def status(self,profile,observed):
        result={}
        for row in plex_targets(profile):
            if not row.get('plex_sync'):
                continue
            cached=self.statuses.get((profile['id'],row['id']),{})
            if (profile['desired'] and observed.get('state')=='online'
                    and cached.get('_signature')==self.signature(profile,observed,row)
                    and time.monotonic()-cached.get('_checked',0)<90):
                result[row['id']]={k:v for k,v in cached.items() if not k.startswith('_')}
            else:
                result[row['id']]={'state':'pending','message':'Afventer kontrol af Plex-adressen.'}
        return result

    def current(self,profile,observed,row):
        fresh=self.store.get(profile['id'])
        latest=self.runtime.status(fresh)
        if (fresh!=profile or not fresh['desired'] or latest.get('state')!='online'
                or self.signature(fresh,latest,row)!=self.signature(profile,observed,row)):
            raise ValueError('VPN eller Plex-mål ændrede sig. Afventer næste kontrol.')

    @staticmethod
    def xml(session,method,url,token=None,params=None):
        # Never place the credential in URLs, redirects, public probes or logs.
        headers={'X-Plex-Client-Identifier':'fjordvpn-port-sync'}
        if token:
            headers['X-Plex-Token']=token
        with session.request(method,url,headers=headers,params=params,timeout=(3,5),
                             allow_redirects=False,stream=True) as response:
            if response.status_code in (401,403):
                raise ValueError('Plex-login blev afvist. Kontrollér Plex-tokenet.')
            response.raise_for_status()
            if response.status_code!=200:
                raise ValueError('Plex svarede ikke som forventet.')
            content=response.raw.read(262145,decode_content=True)
            if len(content)>262144:
                raise ValueError('Plex-svaret var for stort.')
        return ET.fromstring(content) if content.strip() else None

    def sync(self,profile,observed,row):
        state=port_state(profile, observed, row)
        address=ipaddress.IPv4Address(observed['public_ip'])
        port=state.get('public_port')
        if (state.get('state')!='ready' or not address.is_global or type(port) is not int
                or not 1<=port<=65535):
            raise ValueError('Afventer en gyldig offentlig TCP-adresse.')
        token=self.store.plex_token_path(profile['id'],row['id']).read_text().strip()
        local=f"http://{row['host']}:{row['port']}"
        ledger_path=self.store.directory(profile['id'])/'plex-sync-state.json'
        with requests.Session() as session:
            session.trust_env=False
            identity=self.xml(session,'GET',local+'/identity')
            if identity is None or not identity.get('machineIdentifier'):
                raise ValueError('Det lokale mål svarede ikke som en Plex-server.')
            prefs=self.xml(session,'GET',local+'/:/prefs',token)
            values={p.get('id'):p.get('value','') for p in prefs}
            uuid=values.get('CertificateUUID','')
            if not re.fullmatch(r'[a-fA-F0-9]{32}',uuid) or 'customConnections' not in values:
                raise ValueError('Plex mangler oplysninger til sikker serveropdagelse.')
            url=f"https://{str(address).replace('.','-')}.{uuid}.plex.direct:{port}"
            # TLS verification stays enabled. Do not advertise an unverified URL.
            public=self.xml(session,'GET',url+'/identity')
            if public is None or public.get('machineIdentifier')!=identity.get('machineIdentifier'):
                raise ValueError('VPN-adressen peger ikke på den valgte Plex-server.')
            with self.store.lock:
                self.current(profile,observed,row)
                latest=self.xml(session,'GET',local+'/:/prefs',token)
                values={p.get('id'):p.get('value','') for p in latest}
                if values.get('CertificateUUID')!=uuid or 'customConnections' not in values:
                    raise ValueError('Plex-indstillingerne ændrede sig. Afventer næste kontrol.')
                ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
                old=ledger.get(row['id'],{})
                urls=[u.strip() for u in values['customConnections'].split(',') if u.strip()]
                if old.get('server_id')==identity.get('machineIdentifier'):
                    urls=[u for u in urls if u!=old.get('url') or u==url]
                if url not in urls:
                    urls.append(url)
                wanted=','.join(urls)
                changes = {'customConnections':wanted} if wanted!=values['customConnections'] else {}
                full_tunnel = row['id']=='solo' and profile.get('internet_gateway',{}).get('enabled')
                if full_tunnel:
                    desired = {'ManualPortMappingMode':'1', 'ManualPortMappingPort':str(port),
                               'PublishServerOnPlexOnlineKey':'1'}
                    changes.update({k:v for k,v in desired.items() if values.get(k)!=v})
                if changes:
                    self.xml(session,'PUT',local+'/:/prefs',token,changes)
                    check=self.xml(session,'GET',local+'/:/prefs',token)
                    saved={p.get('id'):p.get('value','') for p in check}
                    if saved.get('customConnections')!=wanted:
                        raise ValueError('Plex bekræftede ikke den nye adresse.')
                self.current(profile,observed,row)
                if full_tunnel and (changes or not old.get('internet_gateway')):
                    self.xml(session,'PUT',local+'/myplex/refreshReachability',token)
                ledger[row['id']]={'url':url,'server_id':identity.get('machineIdentifier'),
                                   'internet_gateway':bool(full_tunnel)}
                from store import atomic
                atomic(ledger_path,json.dumps(ledger))
        return url

    def tick(self):
        active=set()
        for profile in self.store.all():
            for row in plex_targets(profile):
                if not row.get('plex_sync') or not profile['desired']:
                    continue
                key=(profile['id'],row['id']);active.add(key)
                observed=self.runtime.status(profile)
                signature=self.signature(profile,observed,row)
                if (self.statuses.get(key,{}).get('_signature')==signature
                        and time.monotonic()<self.next_check.get(key,0)):
                    continue
                self.next_check[key]=time.monotonic()+30
                if observed.get('state')!='online' or port_state(profile, observed, row).get('state')!='ready':
                    result={'state':'pending','message':'Afventer VPN og Plex-målets TCP-port.'}
                else:
                    try:
                        url=self.sync(profile,observed,row)
                        result={'state':'ready','message':'Plex-adressen er opdateret og HTTPS verificeret.','url':url}
                    except requests.exceptions.SSLError:
                        result={'state':'error','message':'Plex HTTPS kunne ikke verificeres. Den tidligere annonce bevares.'}
                    except ValueError as exc:
                        # Only our fixed validation messages may reach the browser.
                        safe=str(exc) if type(exc) is ValueError else 'Plex-svaret kunne ikke læses.'
                        result={'state':'error','message':safe}
                    except Exception:
                        result={'state':'error','message':'Plex-adressen kunne ikke opdateres. Prøver igen.'}
                result.update(_signature=signature,_checked=time.monotonic())
                self.statuses[key]=result
        for key in list(self.statuses):
            if key not in active:
                self.statuses.pop(key,None);self.next_check.pop(key,None)
