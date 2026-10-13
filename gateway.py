"""Opt-in HTTPS gateways. Legacy TCP relays and torrent profiles are unchanged."""
import ipaddress
import json
import re
import time

import requests
from configuration import validate_target
from stream_sync import StreamSync,StreamPending


DOMAIN = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}')

# Cloudflare's published origin-facing networks, verified 2026-10-13:
# https://www.cloudflare.com/ips-v4 and https://www.cloudflare.com/ips-v6
CLOUDFLARE_RANGES=(
    '173.245.48.0/20','103.21.244.0/22','103.22.200.0/22','103.31.4.0/22',
    '141.101.64.0/18','108.162.192.0/18','190.93.240.0/20','188.114.96.0/20',
    '197.234.240.0/22','198.41.128.0/17','162.158.0.0/15','104.16.0.0/13',
    '104.24.0.0/14','172.64.0.0/13','131.0.72.0/22',
    '2400:cb00::/32','2606:4700::/32','2803:f800::/32','2405:b500::/32',
    '2405:8100::/32','2a06:98c0::/29','2c0f:f248::/32')


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


def caddy_config(routes,port,token_file='/config/gateway-token',storage='/observed/caddy-data',cloudflare_ranges=None):
    """No on-demand TLS, no public admin API, no catch-all upstream."""
    handlers=[]
    for row in routes:
        proxy={'handler':'reverse_proxy','upstreams':[{'dial':f"{row['host']}:{row['port']}"}]}
        if row['protocol']=='https':
            proxy['transport']={'protocol':'http','tls':{'server_name':row['tls_server_name']}}
        match={'host':[row['domain']]}
        handle=[proxy]
        if row.get('fjordflix'):
            match['path']=['/media/*']
        else:
            # Authorize the actual socket peer, never a spoofable forwarding header.
            # The port is shared with direct media, so a global IP firewall would
            # also break legitimate viewers. Gate only the web hostname here.
            ranges=list(CLOUDFLARE_RANGES if cloudflare_ranges is None else cloudflare_ranges)
            if not ranges:
                raise ValueError('Cloudflare-adgang kræver mindst ét tilladt IP-netværk.')
            handle=[{'handler':'subroute','routes':[
                {'match':[{'not':[{'remote_ip':{'ranges':ranges}}]}],
                 'handle':[{'handler':'static_response','status_code':403}],'terminal':True},
                {'handle':[proxy]}]}]
        handlers.append({'match':[match],'handle':handle,'terminal':True})
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
        self.progress={}

    def signature(self,profile,observed):
        return (profile['revision'],observed.get('public_ip'),str(observed.get('public_port')))

    def status(self,profile,observed):
        """Never expose readiness checked for an old configuration or VPN endpoint."""
        cached=self.statuses.get(profile['id'],{})
        if (not profile['desired'] or observed.get('state')!='online' or not observed.get('relay_active')
                or cached.get('_signature')!=self.signature(profile,observed)
                or time.monotonic()-cached.get('_checked',0)>90):
            return {}
        return {k:v for k,v in cached.items() if not k.startswith('_')}

    def waiting(self,profile,observed,domain,message):
        ident=profile['id'];signature=self.signature(profile,observed)
        progress=self.progress.get(ident)
        if not progress or progress['signature']!=signature:
            progress={'signature':signature,'started':time.monotonic(),'ready':set()}
            self.progress[ident]=progress
        failed=domain in progress['ready'] or time.monotonic()-progress['started']>=300
        return {'state':'error' if failed else 'pending','message':message}

    def failure(self,profile,observed,row,exc):
        if isinstance(exc,(StreamPending,requests.RequestException)):
            return self.waiting(profile,observed,row['domain'],'Afventer DNS, HTTPS-certifikat eller forbindelse.' if isinstance(exc,requests.RequestException) else str(exc))
        return {'state':'error','message':str(exc) if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError) else 'Forbindelsen kunne ikke kontrolleres.'}

    def probe_web(self,profile,observed,row):
        url='https://'+row['domain']
        try:
            with requests.Session() as session:
                session.trust_env=False
                # Inspect headers only; never download a page or follow login redirects.
                response=session.get(url,timeout=8,allow_redirects=False,stream=True)
                code=response.status_code;response.close()
            if 200<=code<400 or code==401:
                return {'state':'ready','message':'HTTPS-adressen svarer. Domænet er online.','url':url}
            if code in (404,502,522,523,525,526):
                return self.waiting(profile,observed,row['domain'],f'HTTPS-adressen svarer med HTTP {code}. Afventer fungerende forbindelse.')
            return {'state':'error','message':f'HTTPS-adressen svarer med HTTP {code}.','url':url}
        except requests.RequestException as exc:
            return self.failure(profile,observed,row,exc)

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

    def record_marker(self, profile):
        return 'FjordVPN gateway '+profile['id']

    def remember_record(self,profile,key,record,payload):
        state=self.store.gateway_dns(profile['id'])
        state['records'][key['zone_id']+':'+payload['name']]={
            'domain':payload['name'],'zone_id':key['zone_id'],'key_id':key['id'],
            'id':record['id'],'content':payload['content'],'proxied':payload['proxied'],
            'comment':payload['comment']}
        self.store.save_gateway_dns(profile['id'],state)

    def cleanup(self,profile):
        """Delete only recorded IDs whose contents and ownership still match."""
        with self.store.lock:
            if self.store.get(profile['id'])!=profile:
                raise ValueError('Gatewayen ændrede sig. Afventer næste kontrol.')
            state=self.store.gateway_dns(profile['id'])
            config=profile['gateway'];current_zone=self.store.key(config['key_id'])['zone_id']
            active={(current_zone,r['domain']) for r in config['routes']}
            candidates={(r['zone_id'],r['domain']):r for r in state['records'].values()
                        if (r['zone_id'],r['domain']) not in active}
            candidates.update({(r['zone_id'],r['domain']):r for r in state['pending']})
            groups={}
            for (zone,domain),candidate in candidates.items():
                # Re-adding or handing a hostname to another profile cancels deletion.
                used=(zone,domain) in active or any(
                    other['id']!=profile['id'] and (any(r['domain']==domain for r in other.get('gateway',{}).get('routes',[]))
                    or (other.get('stream_sync',{}).get('enabled') and other['stream_sync']['domain']==domain))
                    for other in self.store.all())
                if (zone,domain) not in active:
                    groups.setdefault(zone,{'key_id':candidate['key_id'],'candidates':[]})
                if used:
                    state['pending']=[r for r in state['pending'] if (r['zone_id'],r['domain'])!=(zone,domain)]
                    if (zone,domain) not in active:
                        state['records'].pop(zone+':'+domain,None)
                    continue
                groups[zone]['candidates'].append(candidate)
            self.store.save_gateway_dns(profile['id'],state)
            for zone,group in groups.items():
                candidates=group['candidates']
                key=self.store.key(config['key_id'] if zone==current_zone else group['key_id'])
                if key['zone_id']!=zone:
                    raise ValueError('Cloudflare-nøglen til oprydning har skiftet zone. Brug den oprindelige zone.')
                token=key['token'];base='/zones/'+zone
                ruleset=self.cf(token,'GET',base+'/rulesets/phases/http_request_origin/entrypoint',allow_missing=True)
                owned=[r for r in (ruleset or {}).get('rules',[]) if r.get('ref')=='fjordvpn_gateway_'+profile['id']]
                if len(owned)>1:
                    raise ValueError('Cloudflare har flere regler for gatewayen. Kontrollér Origin Rules.')
                if owned:
                    remaining=[r['domain'] for r in config['routes'] if zone==current_zone and not r.get('fjordflix')]
                    path=base+'/rulesets/'+ruleset['id']+'/rules/'+owned[0]['id']
                    if remaining:
                        rule={k:owned[0][k] for k in ('ref','description','action','action_parameters') if k in owned[0]}
                        rule.update(expression='http.host in {'+' '.join(json.dumps(d) for d in remaining)+'}',enabled=True)
                        if any(owned[0].get(k)!=v for k,v in rule.items()):
                            self.cf(token,'PATCH',path,json=rule)
                    else:
                        self.cf(token,'DELETE',path,allow_missing=True)
                for candidate in candidates:
                    domain=candidate['domain'];ident=zone+':'+domain
                    tracked=state['records'].get(ident)
                    rows=self.cf(token,'GET',base+'/dns_records',params={'name':domain,'per_page':100})
                    matches=bool(tracked) and len(rows)==1 and all(rows[0].get(k)==v for k,v in {
                        'id':tracked['id'],'name':domain,'type':'A','content':tracked['content'],
                        'proxied':tracked['proxied'],'comment':self.record_marker(profile)}.items())
                    if matches:
                        self.cf(token,'DELETE',base+'/dns_records/'+tracked['id'],allow_missing=True)
                    elif rows:
                        warning=domain+': DNS-recorden blev bevaret, fordi ejerskab eller indhold har ændret sig.'
                        if warning not in state['warnings']:
                            state['warnings'].append(warning)
                    state['records'].pop(ident,None)
                    state['pending']=[r for r in state['pending'] if (r['zone_id'],r['domain'])!=(zone,domain)]
                    self.store.save_gateway_dns(profile['id'],state)
            return state['warnings']

    def sync(self,profile,observed,route_status=None):
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
        self.cleanup(profile)
        stream_state=None;stream_error=None
        try:
            stream_state=next((self.stream.stream_state(r['domain']) for r in config['routes'] if r.get('fjordflix')),None)
        except Exception as exc:
            # A stopped FjordFlix app must not block updates for other webapps.
            # Keep its direct DNS untouched and report the error after web sync.
            stream_error=exc
        # Inspect every active DNS name before writing any of them.
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
            if not web_domains:
                owned=[r for r in (ruleset or {}).get('rules',[]) if r.get('ref')==ref]
                if len(owned)>1:
                    raise ValueError('Cloudflare har flere regler for gatewayen. Kontrollér Origin Rules.')
                if owned:
                    self.cf(token,'DELETE',zone+'/rulesets/'+ruleset['id']+'/rules/'+owned[0]['id'],allow_missing=True)
            elif ruleset is None:
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
            payload={'type':'A','name':row['domain'],'content':str(address),'proxied':proxied,
                     'ttl':1 if proxied else 60,'comment':self.record_marker(profile)}
            with self.store.lock:
                self.current(profile,observed)
                if not (record and all(record.get(k)==payload[k] for k in ('content','proxied','comment'))):
                    record=self.cf(token,'PATCH' if record else 'POST',zone+'/dns_records'+('/'+record['id'] if record else ''),json=payload)
                self.remember_record(profile,key,record,payload)
        if route_status is not None:
            for row in config['routes']:
                if not row.get('fjordflix'):
                    route_status[row['domain']]=self.probe_web(profile,observed,row)
        if stream_error:
            if route_status is not None:
                for row in config['routes']:
                    if row.get('fjordflix'):
                        route_status[row['domain']]=self.failure(profile,observed,row,stream_error)
            raise stream_error
        urls=[]
        for row in config['routes']:
            if row.get('fjordflix'):
                try:
                    url=self.stream.verify_stream(row['domain'],port,lambda:self.current(profile,observed),stream_state)
                except Exception as exc:
                    if route_status is not None:
                        route_status[row['domain']]=self.failure(profile,observed,row,exc)
                    raise
                if route_status is not None:
                    route_status[row['domain']]={'state':'ready','message':'HTTPS-test godkendt. FjordFlix-streamadressen er opdateret.','url':url}
                urls.append(url)
            else:
                urls.append('https://'+row['domain'])
        return urls

    def tick(self):
        for profile in self.store.all():
            ident=profile['id']
            if not profile.get('gateway',{}).get('enabled') or not profile['desired']:
                self.statuses.pop(ident,None);self.next_check.pop(ident,None);self.progress.pop(ident,None);continue
            observed=self.runtime.status(profile)
            signature=self.signature(profile,observed)
            progress=self.progress.get(ident)
            if not progress or progress['signature']!=signature:
                self.progress[ident]={'signature':signature,'started':time.monotonic(),'ready':set()}
                self.next_check.pop(ident,None)
            if time.monotonic()<self.next_check.get(ident,0):
                continue
            self.next_check[ident]=time.monotonic()+30
            routes={};urls=[];error=None
            if not profile['gateway'].get('auto_sync',True):
                message='Automatisk IP og port er slået fra. Domænernes opsætning er ikke verificeret.'
                routes={r['domain']:{'state':'pending','message':message} for r in profile['gateway']['routes']}
            elif observed.get('state')!='online' or not observed.get('relay_active'):
                state='error' if observed.get('state')=='error' else 'pending'
                message=observed.get('message') if state=='error' else 'Afventer VPN og HTTPS-gateway.'
                routes={r['domain']:{'state':state,'message':message or 'VPN-forbindelsen har en fejl.'} for r in profile['gateway']['routes']}
            else:
                try:
                    urls=self.sync(profile,observed,routes)
                except Exception as exc:
                    error=exc
                for row in profile['gateway']['routes']:
                    if row['domain'] not in routes:
                        routes[row['domain']]=self.failure(profile,observed,row,error) if error else self.waiting(profile,observed,row['domain'],'Afventer forbindelsestest.')
                # Do not publish a green result after concurrent editing/reconnection.
                fresh=self.store.get(ident);latest=self.runtime.status(fresh)
                if fresh!=profile or self.signature(fresh,latest)!=signature or latest.get('state')!='online' or not latest.get('relay_active'):
                    self.statuses.pop(ident,None);self.next_check.pop(ident,None);continue
            for domain,value in routes.items():
                if value['state']=='ready':
                    self.progress[ident]['ready'].add(domain)
            state='error' if any(v['state']=='error' for v in routes.values()) else 'ready' if routes and all(v['state']=='ready' for v in routes.values()) else 'pending'
            message='Alle domæner er verificeret og online.' if state=='ready' else 'Et eller flere domæner har en fejl. Se status ved domænet.' if state=='error' else 'Opsætning og forbindelsestest er i gang. Se status ved domænet.'
            if not profile['gateway'].get('auto_sync',True):
                message='Automatisk IP og port er slået fra. Cloudflare skal opdateres manuelt.'
            warnings=self.store.gateway_dns(ident)['warnings']
            if warnings:
                message+=' Oprydning: '+' '.join(warnings[-5:])
            self.statuses[ident]={'ok':state=='ready','state':state,'message':message,'urls':urls,'routes':routes,
                '_signature':signature,'_checked':time.monotonic()}
