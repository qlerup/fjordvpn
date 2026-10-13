"""Opt-in HTTPS gateways. Legacy TCP relays and torrent profiles are unchanged."""
import ipaddress
import json
import re
import time

import requests
from configuration import validate_target
from stream_sync import StreamSync


DOMAIN = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}')


def hostname(value):
    if not isinstance(value, str) or len(value)>253 or not DOMAIN.fullmatch(value.strip().lower()):
        raise ValueError('Indtast et subdomæne uden https, port eller sti.')
    return value.strip().lower()


def settings(value, networks):
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ValueError('Ugyldige gatewayindstillinger.')
    if not value['enabled']:
        return {'enabled': False, 'routes': [], 'key_id': ''}
    auto=value.get('auto_sync',True)
    if type(auto) is not bool:
        raise ValueError('Vælg automatisk IP- og portopdatering.')
    key=value.get('key_id')
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{32}',key):
        raise ValueError('Vælg en gemt Cloudflare-nøgle til gatewayen.')
    routes=value.get('routes')
    if not isinstance(routes,list) or not 1<=len(routes)<=25:
        raise ValueError('Tilføj mellem 1 og 25 subdomæner til gatewayen.')
    result=[];seen=set()
    for row in routes:
        if not isinstance(row,dict):
            raise ValueError('Ugyldigt lokalt mål.')
        domain=hostname(row.get('domain'))
        target=validate_target({'name':domain[:64], 'host':row.get('host'), 'port':row.get('port'),
                                'relay_enabled':True},networks)
        protocol=row.get('protocol','http')
        if protocol not in ('http','https'):
            raise ValueError('Vælg HTTP eller HTTPS til det lokale mål.')
        server_name=hostname(row['tls_server_name']) if row.get('tls_server_name') else domain
        flix=row.get('fjordflix',False)
        if type(flix) is not bool:
            raise ValueError('Vælg om domænet bruges til FjordFlix-streaming.')
        if domain in seen:
            raise ValueError('Hvert subdomæne må kun have ét lokalt mål.')
        seen.add(domain)
        result.append({'domain':domain,'host':target['host'],'port':target['port'],
                       'protocol':protocol,'tls_server_name':server_name,'fjordflix':flix})
    if sum(r['fjordflix'] for r in result)>1:
        raise ValueError('Kun ét domæne kan styre FjordFlix-streamadressen.')
    return {'enabled':True,'key_id':key,'routes':result,'auto_sync':auto}


def caddy_config(routes,port,token_file='/config/gateway-token',storage='/observed/caddy-data'):
    """No on-demand TLS, no public admin API, no catch-all upstream."""
    handlers=[]
    for row in routes:
        proxy={'handler':'reverse_proxy','upstreams':[{'dial':f"{row['host']}:{row['port']}"}]}
        if row['protocol']=='https':
            proxy['transport']={'protocol':'http','tls':{'server_name':row['tls_server_name']}}
        match={'host':[row['domain']]}
        if row.get('fjordflix'):
            match['path']=['/media/*']
        handlers.append({'match':[match],'handle':[proxy],'terminal':True})
    handlers.append({'handle':[{'handler':'static_response','status_code':404}]})
    return {'admin':{'disabled':True,'config':{'persist':False}},
            'storage':{'module':'file_system','root':storage},
            'apps':{'http':{'servers':{'gateway':{'listen':[f':{port}'],
                'tls_connection_policies':[{}],'automatic_https':{'disable_redirects':True},'routes':handlers}}},
                'tls':{'automation':{'policies':[{'subjects':[r['domain'] for r in routes],
                    'issuers':[{'module':'acme','challenges':{'dns':{
                        'provider':{'name':'cloudflare','api_token':'{file.'+token_file+'}'},
                        'resolvers':['1.1.1.1:53','8.8.8.8:53']}}}]}]}}}}


class GatewaySync:
    def __init__(self,store,runtime):
        self.store,self.runtime=store,runtime
        self.statuses,self.next_check={},{}
        self.stream=StreamSync(store,runtime)

    def cf(self,token,method,path,allow_missing=False,**kwargs):
        with requests.Session() as session:
            session.trust_env=False
            response=session.request(method,'https://api.cloudflare.com/client/v4'+path,
                headers={'Authorization':'Bearer '+token},timeout=8,allow_redirects=False,**kwargs)
        if allow_missing and response.status_code==404:
            return None
        if not response.ok:
            raise ValueError('Cloudflare afviste gatewayen. Nøglen kræver DNS Edit, Zone Read og Origin Rules Edit for zonen.')
        body=response.json()
        if body.get('success') is not True:
            raise ValueError('Cloudflare kunne ikke opdatere gatewayen.')
        return body['result']

    def current(self,profile,observed):
        fresh=self.store.get(profile['id']);latest=self.runtime.status(fresh)
        if (fresh!=profile or latest.get('state')!='online' or not latest.get('relay_active')
                or latest.get('public_ip')!=observed['public_ip']
                or str(latest.get('public_port'))!=str(observed['public_port'])):
            raise ValueError('Gatewayen ændrede sig. Afventer næste kontrol.')

    def sync(self,profile,observed):
        config=profile['gateway'];key=self.store.key(config['key_id']);token=key['token']
        address=ipaddress.IPv4Address(observed['public_ip']);port=int(observed['public_port'])
        if not address.is_global or not 1<=port<=65535:
            raise ValueError('Afventer offentlig VPN-adresse og port.')
        zone='/zones/'+key['zone_id']
        name=self.cf(token,'GET',zone)['name'].lower()
        domains=[r['domain'] for r in config['routes']]
        web_domains=[r['domain'] for r in config['routes'] if not r.get('fjordflix')]
        if any(not d.endswith('.'+name) for d in domains):
            raise ValueError('Alle subdomæner skal tilhøre den valgte Cloudflare-zone.')
        stream_state=None;stream_error=None
        try:
            stream_state=next((self.stream.stream_state(r['domain']) for r in config['routes'] if r.get('fjordflix')),None)
        except Exception as exc:
            # A stopped FjordFlix app must not block updates for other webapps.
            # Keep its direct DNS untouched and report the error after web sync.
            stream_error=exc
        # Inspect every DNS name before writing any of them. No record is deleted.
        records=[]
        for domain in domains:
            rows=self.cf(token,'GET',zone+'/dns_records',params={'name':domain,'per_page':100})
            if len(rows)>1 or any(r.get('type')!='A' or r.get('name','').lower()!=domain for r in rows):
                raise ValueError('Et subdomæne har CNAME, AAAA eller flere records. Vælg et ledigt navn eller én A-record.')
            records.append(rows[0] if rows else None)
        phase=zone+'/rulesets/phases/http_request_origin/entrypoint'
        ruleset=self.cf(token,'GET',phase,allow_missing=True)
        ref='fjordvpn_gateway_'+profile['id']
        rule={'ref':ref,'description':'FjordVPN gateway '+profile['id'],
              'expression':'http.host in {'+' '.join(json.dumps(d) for d in web_domains)+'}' if web_domains else 'false',
              'action':'route','action_parameters':{'origin':{'port':port}},'enabled':bool(web_domains)}
        with self.store.lock:
            self.current(profile,observed)
            if ruleset is None:
                self.cf(token,'POST',zone+'/rulesets',json={'name':'FjordVPN origins','kind':'zone',
                    'phase':'http_request_origin','rules':[rule]})
            else:
                owned=[r for r in ruleset.get('rules',[]) if r.get('ref')==ref]
                if len(owned)>1:
                    raise ValueError('Cloudflare har flere regler for gatewayen. Kontrollér Origin Rules.')
                path=zone+'/rulesets/'+ruleset['id']+'/rules'
                if owned:
                    if any(owned[0].get(k)!=v for k,v in rule.items()):
                        self.cf(token,'PATCH',path+'/'+owned[0]['id'],json=rule)
                else:
                    self.cf(token,'POST',path,json=rule)
        for row,record in zip(config['routes'],records):
            if row.get('fjordflix') and stream_error:
                continue
            proxied=not row.get('fjordflix',False)
            payload={'type':'A','name':row['domain'],'content':str(address),'proxied':proxied,'ttl':1 if proxied else 60}
            if record and record.get('content')==str(address) and record.get('proxied') is proxied:
                continue
            with self.store.lock:
                self.current(profile,observed)
                self.cf(token,'PATCH' if record else 'POST',zone+'/dns_records'+('/'+record['id'] if record else ''),json=payload)
        if stream_error:
            raise stream_error
        urls=[]
        for row in config['routes']:
            if row.get('fjordflix'):
                url=self.stream.verify_stream(row['domain'],port,lambda:self.current(profile,observed),stream_state)
                urls.append(url)
            else:
                urls.append('https://'+row['domain'])
        return urls

    def tick(self):
        for profile in self.store.all():
            ident=profile['id']
            if not profile.get('gateway',{}).get('enabled') or not profile['desired']:
                self.statuses.pop(ident,None);self.next_check.pop(ident,None);continue
            if not profile['gateway'].get('auto_sync',True):
                self.statuses[ident]={'ok':True,'message':'Automatisk IP og port er slået fra. Cloudflare skal opdateres manuelt.'}
                self.next_check.pop(ident,None);continue
            if time.monotonic()<self.next_check.get(ident,0):
                continue
            self.next_check[ident]=time.monotonic()+30
            try:
                observed=self.runtime.status(profile)
                if observed['state']!='online' or not observed.get('relay_active'):
                    raise ValueError('Afventer VPN og gateway. HTTPS-certifikater oprettes automatisk.')
                urls=self.sync(profile,observed)
                message='DNS, HTTPS-port og FjordFlix-streamadresse er ajour.' if any(r.get('fjordflix') for r in profile['gateway']['routes']) else 'DNS og HTTPS-port er ajour. Certifikater kan være under oprettelse.'
                self.statuses[ident]={'ok':True,'message':message,'urls':urls}
            except ValueError as exc:
                self.statuses[ident]={'ok':False,'message':str(exc) if not isinstance(exc,json.JSONDecodeError) else 'Ugyldigt svar fra Cloudflare.'}
            except Exception:
                self.statuses[ident]={'ok':False,'message':'Gateway-synkronisering afventer. Prøver igen automatisk.'}
