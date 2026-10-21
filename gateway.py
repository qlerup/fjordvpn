"""Opt-in HTTPS gateways. Legacy TCP relays and torrent profiles are unchanged."""
import ipaddress
import json
import hashlib
import re
import time

import requests
from configuration import validate_target
from stream_sync import StreamSync,StreamPending
from minecraft_gateway import public_probe
from relay.gateway_tcp import target_rows, MAX_TARGETS


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


def game_rows(config):
    legacy=config.get('minecraft',{})
    return ([legacy] if legacy.get('enabled') else []) + config.get('gameservers',[])


def forwarding_config(config):
    rows=list(target_rows(config.get('tcp',{})))
    rows.extend({'id':'g'+row['id'],'name':row['domain'],'host':row['host'],'port':row['port']}
                for row in config.get('gameservers',[]))
    return {'enabled':bool(rows),'targets':rows}


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
    if not isinstance(routes,list) or len(routes)>25:
        raise ValueError('En gateway kan have op til 25 subdomæner.')
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
        direct=row.get('direct',False)
        if type(direct) is not bool:
            raise ValueError('Vælg om domænet skal bruge direkte adgang.')
        if direct and flix:
            raise ValueError('Vælg enten direkte adgang eller FjordFlix-streaming.')
        if domain in seen:
            raise ValueError('Hvert subdomæne må kun have ét lokalt mål.')
        seen.add(domain)
        result.append({'domain':domain,'host':target['host'],'port':target['port'],
                       'protocol':protocol,'tls_server_name':server_name,'fjordflix':flix,'direct':direct})
    if sum(r['fjordflix'] for r in result)>1:
        raise ValueError('Kun ét domæne kan styre FjordFlix-streamadressen.')
    minecraft=value.get('minecraft',{'enabled':False})
    if not isinstance(minecraft,dict) or type(minecraft.get('enabled')) is not bool:
        raise ValueError('Vælg om Minecraft Java skal bruge gatewayen.')
    mc={'enabled':False}
    if minecraft['enabled']:
        domain=hostname(minecraft.get('domain'))
        if domain in seen:
            raise ValueError('Minecraft-serveren skal have sit eget DNS-only domæne.')
        target=validate_target({'name':domain[:64],'host':minecraft.get('host'),'port':minecraft.get('port'),'relay_enabled':True},networks)
        mc_key=minecraft.get('key_id') or key
        if not isinstance(mc_key,str) or not re.fullmatch('[a-f0-9]{32}',mc_key):
            raise ValueError('Vælg en Cloudflare-nøgle til Minecraft-domænet.')
        mc={'enabled':True,'domain':domain,'host':target['host'],'port':target['port'],'key_id':mc_key}
        seen.add(domain)
    games=value.get('gameservers',[])
    if not isinstance(games,list) or len(games)+(1 if mc.get('enabled') else 0)>25:
        raise ValueError('En gateway kan have op til 25 gameservere.')
    game_targets=[];game_ids=set()
    for row in games:
        if not isinstance(row,dict):
            raise ValueError('Ugyldig gameserver.')
        ident=row.get('id')
        if not isinstance(ident,str) or not re.fullmatch(r'(?:[0-9]|1[0-9]|2[0-4])',ident) or ident in game_ids:
            raise ValueError('Gameservere skal have hver sin gyldige identitet.')
        domain=hostname(row.get('domain'))
        if domain in seen:
            raise ValueError('Hvert domæne må kun have ét mål.')
        target=validate_target({'name':domain[:64],'host':row.get('host'),'port':row.get('port'),'relay_enabled':True},networks)
        game_key=row.get('key_id') or key
        if not isinstance(game_key,str) or not re.fullmatch('[a-f0-9]{32}',game_key):
            raise ValueError('Vælg en Cloudflare-nøgle til gameserveren.')
        game_targets.append({'id':ident,'domain':domain,'host':target['host'],'port':target['port'],'key_id':game_key})
        seen.add(domain);game_ids.add(ident)
    tcp=value.get('tcp',{'enabled':False})
    if not isinstance(tcp,dict) or type(tcp.get('enabled')) is not bool:
        raise ValueError('Vælg om TCP-videresendelse skal aktiveres.')
    direct={'enabled':False}
    if tcp['enabled']:
        rows=target_rows(tcp)
        if not isinstance(rows,list) or not 1 <= len(rows) <= MAX_TARGETS:
            raise ValueError('Tilføj mellem 1 og 25 TCP-mål.')
        targets=[];ids=set()
        for row in rows:
            if not isinstance(row,dict):
                raise ValueError('Ugyldigt TCP-mål.')
            ident=row.get('id')
            if not isinstance(ident,str) or not re.fullmatch(r'(?:[0-9]|1[0-9]|2[0-4])',ident) or ident in ids:
                raise ValueError('TCP-mål skal have hver sin gyldige identitet.')
            ids.add(ident)
            target=validate_target({'name':row.get('name','TCP'),'host':row.get('host'),'port':row.get('port'),
                                    'relay_enabled':True},networks)
            targets.append({'id':ident,'name':target['name'],'host':target['host'],'port':target['port']})
        direct={'enabled':True,'targets':targets}
    if not result and not mc.get('enabled') and not game_targets and not direct['enabled']:
        raise ValueError('Tilføj mindst ét mål til gatewayen.')
    order=['web:'+r['domain'] for r in result]+['tcp:'+r['id'] for r in target_rows(direct)]
    order+=(['game:legacy'] if mc.get('enabled') else [])+['game:'+r['id'] for r in game_targets]
    requested=value.get('target_order')
    if requested is not None:
        if (not isinstance(requested,list) or any(not isinstance(k,str) for k in requested)
                or len(requested)!=len(set(requested))):
            raise ValueError('Ugyldig rækkefølge for gatewayens mål.')
        # Older API clients can add/remove targets without sending layout data.
        order=[k for k in requested if k in order]+[k for k in order if k not in requested]
    return {'enabled':True,'key_id':key,'routes':result,'auto_sync':auto,'minecraft':mc,'tcp':direct,
            'gameservers':game_targets,'target_order':order}


def process_key(config, port, token):
    """Only web/Minecraft changes should restart their shared Caddy process."""
    return ('gateway', port, json.dumps(config['routes'], sort_keys=True),
            hashlib.sha256(token).hexdigest(),
            json.dumps(config.get('minecraft', {'enabled': False}), sort_keys=True))


def caddy_config(routes,port,token_file='/config/gateway-token',storage='/observed/caddy-data',cloudflare_ranges=None,minecraft=None):
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
        elif not row.get('direct'):
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
    value={'admin':{'disabled':True,'config':{'persist':False}},
            'storage':{'module':'file_system','root':storage},
            'apps':{'http':{'servers':{'gateway':{'listen':[f':{port}'],
                'tls_connection_policies':[{}],'automatic_https':{'disable_redirects':True},'routes':handlers}}},
                'tls':{'automation':{'policies':[{'subjects':[r['domain'] for r in routes],
                    'issuers':[{'module':'acme','challenges':{'dns':{
                        'provider':{'name':'cloudflare','api_token':'{file.'+token_file+'}'},
                        'resolvers':['1.1.1.1:53','8.8.8.8:53']}}}]}]}}}}
    if minecraft and minecraft.get('enabled'):
        # TLS passes to the existing HTTP listener without a new socket/proxy,
        # preserving its actual peer address for the Cloudflare-only web gate.
        # The sole non-TLS TCP target is the explicitly selected Java server.
        value['apps']['http']['servers']['gateway']['listener_wrappers']=[
            {'wrapper':'layer4','matching_timeout':5000000000,'routes':[
                # A Java packet length may also be 0x16. Inspect TLS's version
                # bytes too, rather than asking the TLS matcher to read an
                # imaginary multi-kilobyte ClientHello from that Java packet.
                {'match':[{'not':[{'regexp':{'count':3,'hex':True,'pattern':'^16030[0-4]$'}}]}],
                 'handle':[{'handler':'proxy','upstreams':[{'dial':[f"{minecraft['host']}:{minecraft['port']}"]}]}]}]},
            {'wrapper':'tls'}]
    return value


class GatewaySync:
    def __init__(self,store,runtime):
        self.store,self.runtime=store,runtime
        self.statuses,self.next_check={},{}
        self.stream=StreamSync(store,runtime)
        self.progress={}

    def signature(self,profile,observed):
        ports=tuple((row['id'],observed.get('tcp_status',{}).get(row['id'],{}).get('state'),
                     observed.get('tcp_status',{}).get(row['id'],{}).get('public_port'))
                    for row in target_rows(forwarding_config(profile['gateway'])))
        return (profile['revision'],observed.get('public_ip'),str(observed.get('public_port')),ports)

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
        url='https://'+row['domain']+(':'+str(int(observed['public_port'])) if row.get('direct') else '')
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
                or str(latest.get('public_port'))!=str(observed['public_port'])
                or self.signature(profile,latest)!=self.signature(profile,observed)):
            raise ValueError('Gatewayen ændrede sig. Afventer næste kontrol.')

    def record_marker(self, profile):
        return 'FjordVPN gateway '+profile['id']

    def remember_record(self,profile,key,record,payload):
        state=self.store.gateway_dns(profile['id'])
        tracked={
            'domain':payload['name'],'zone_id':key['zone_id'],'key_id':key['id'],
            'id':record['id'],'type':payload['type'],'comment':payload['comment']}
        if payload['type']=='SRV':
            tracked['data']=payload['data']
        else:
            tracked.update(content=payload['content'],proxied=payload['proxied'])
        state['records'][key['zone_id']+':'+payload['name']]=tracked
        self.store.save_gateway_dns(profile['id'],state)

    def cleanup(self,profile):
        """Delete only recorded IDs whose contents and ownership still match."""
        with self.store.lock:
            if self.store.get(profile['id'])!=profile:
                raise ValueError('Gatewayen ændrede sig. Afventer næste kontrol.')
            state=self.store.gateway_dns(profile['id'])
            config=profile['gateway'];current_zone=self.store.key(config['key_id'])['zone_id']
            active={(current_zone,r['domain']) for r in config['routes']}
            active_keys={current_zone:config['key_id']}
            for mc in game_rows(config):
                mc_zone=self.store.key(mc['key_id'])['zone_id'];active_keys.setdefault(mc_zone,mc['key_id'])
                active.update({(mc_zone,mc['domain']),(mc_zone,'_minecraft._tcp.'+mc['domain'])})
            candidates={(r['zone_id'],r['domain']):r for r in state['records'].values()
                        if (r['zone_id'],r['domain']) not in active}
            candidates.update({(r['zone_id'],r['domain']):r for r in state['pending']})
            groups={}
            for (zone,domain),candidate in candidates.items():
                base_domain=domain.removeprefix('_minecraft._tcp.')
                # Re-adding or handing a hostname to another profile cancels deletion.
                used=(zone,domain) in active or any(
                    other['id']!=profile['id'] and (any(r['domain']==base_domain for r in other.get('gateway',{}).get('routes',[]))
                    or (other.get('stream_sync',{}).get('enabled') and other['stream_sync']['domain']==base_domain)
                    or any(r['domain']==base_domain for r in game_rows(other.get('gateway',{}))))
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
                key=self.store.key(active_keys.get(zone,group['key_id']))
                if key['zone_id']!=zone:
                    raise ValueError('Cloudflare-nøglen til oprydning har skiftet zone. Brug den oprindelige zone.')
                token=key['token'];base='/zones/'+zone
                # A separate Minecraft zone requires DNS permissions only.
                was_web=any(state['records'].get(zone+':'+r['domain'],{}).get('proxied') for r in candidates)
                ruleset=self.cf(token,'GET',base+'/rulesets/phases/http_request_origin/entrypoint',allow_missing=True) if zone==current_zone or was_web else None
                owned=[r for r in (ruleset or {}).get('rules',[]) if r.get('ref')=='fjordvpn_gateway_'+profile['id']]
                if len(owned)>1:
                    raise ValueError('Cloudflare har flere regler for gatewayen. Kontrollér Origin Rules.')
                if owned:
                    remaining=[r['domain'] for r in config['routes'] if zone==current_zone and not (r.get('fjordflix') or r.get('direct'))]
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
                    expected={'id':tracked['id'],'name':domain,'type':tracked.get('type','A'),
                              'comment':self.record_marker(profile)} if tracked else {}
                    if tracked and tracked.get('type','A')=='A':
                        expected.update(content=tracked['content'],proxied=tracked['proxied'])
                    matches=bool(tracked) and len(rows)==1 and all(rows[0].get(k)==v for k,v in expected.items())
                    if matches and tracked.get('type')=='SRV':
                        matches=all(rows[0].get('data',{}).get(k)==v for k,v in tracked['data'].items())
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
        web_domains=[r['domain'] for r in config['routes'] if not (r.get('fjordflix') or r.get('direct'))]
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
            proxied=not (row.get('fjordflix') or row.get('direct'))
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
        errors=[]
        if stream_error:
            if route_status is not None:
                for row in config['routes']:
                    if row.get('fjordflix'):
                        route_status[row['domain']]=self.failure(profile,observed,row,stream_error)
            errors.append(stream_error)
        urls=[]
        for row in config['routes']:
            if row.get('fjordflix'):
                if stream_error:
                    continue
                try:
                    url=self.stream.verify_stream(row['domain'],port,lambda:self.current(profile,observed),stream_state)
                except Exception as exc:
                    if route_status is not None:
                        route_status[row['domain']]=self.failure(profile,observed,row,exc)
                    errors.append(exc);continue
                if route_status is not None:
                    route_status[row['domain']]={'state':'ready','message':'HTTPS-test godkendt. FjordFlix-streamadressen er opdateret.','url':url}
                urls.append(url)
            else:
                urls.append('https://'+row['domain']+(':'+str(port) if row.get('direct') else ''))
        mc=config.get('minecraft',{})
        if mc.get('enabled'):
            try:
                version=self.sync_minecraft(profile,observed)
                if route_status is not None:
                    route_status[mc['domain']]={'state':'ready','message':'Minecraft Java svarer. DNS og serverport er opdateret.','version':version}
                urls.append('minecraft://'+mc['domain'])
            except Exception as exc:
                if route_status is not None:
                    route_status[mc['domain']]=self.failure(profile,observed,mc,exc)
                errors.append(exc)
        for game in config.get('gameservers',[]):
            try:
                status=observed.get('tcp_status',{}).get('g'+game['id'],{})
                if status.get('state')!='ready':
                    raise StreamPending('Afventer gameserverens egen VPN-port.')
                port=status.get('public_port')
                if type(port) is not int or not 1<=port<=65535:
                    raise StreamPending('Afventer gyldig gameserverport.')
                version=self.sync_minecraft(profile,observed,game,port)
                if route_status is not None:
                    route_status[game['domain']]={'state':'ready','message':'Minecraft Java svarer. DNS og serverport er opdateret.','version':version,'public_port':port}
                urls.append('minecraft://'+game['domain'])
            except Exception as exc:
                if route_status is not None:
                    route_status[game['domain']]=self.failure(profile,observed,game,exc)
                errors.append(exc)
        if errors:
            raise errors[0]
        return urls

    def sync_minecraft(self,profile,observed,mc=None,port=None):
        mc=mc if mc is not None else profile['gateway']['minecraft']
        port=int(observed['public_port']) if port is None else port
        key=self.store.key(mc['key_id']);token=key['token']
        zone='/zones/'+key['zone_id'];domain=mc['domain'];name=self.cf(token,'GET',zone)['name'].lower()
        if domain!=name and not domain.endswith('.'+name):
            raise ValueError('Minecraft-domænet tilhører ikke den valgte Cloudflare-zone.')
        srv='_minecraft._tcp.'+domain
        records=[]
        for record_name,kind in [(domain,'A'),(srv,'SRV')]:
            rows=self.cf(token,'GET',zone+'/dns_records',params={'name':record_name,'per_page':100})
            if len(rows)>1 or any(r.get('type')!=kind or r.get('name','').lower()!=record_name for r in rows):
                raise ValueError('Minecraft-domænet har konkurrerende DNS-records. Kontrollér A og SRV.')
            records.append(rows[0] if rows else None)
        payloads=[{'type':'A','name':domain,'content':observed['public_ip'],'proxied':False,'ttl':60,
                   'comment':self.record_marker(profile)},
                  {'type':'SRV','name':srv,'ttl':60,'comment':self.record_marker(profile),
                   'data':{'priority':0,'weight':0,'port':port,'target':domain}}]
        for record,payload in zip(records,payloads):
            with self.store.lock:
                self.current(profile,observed)
                matches=record and all(record.get(k)==v for k,v in payload.items() if k not in ('ttl','data'))
                if payload['type']=='SRV':
                    matches=matches and all(record.get('data',{}).get(k)==v for k,v in payload['data'].items())
                if not matches:
                    record=self.cf(token,'PATCH' if record else 'POST',zone+'/dns_records'+('/'+record['id'] if record else ''),json=payload)
                self.remember_record(profile,key,record,payload)
        version=public_probe(domain,port,observed['public_ip'])
        with self.store.lock:
            self.current(profile,observed)
        return version

    def route_rows(self,profile):
        return list(profile['gateway']['routes'])+game_rows(profile['gateway'])

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
                routes={r['domain']:{'state':'pending','message':message} for r in self.route_rows(profile)}
            elif observed.get('state')!='online' or not observed.get('relay_active'):
                state='error' if observed.get('state')=='error' else 'pending'
                message=observed.get('message') if state=='error' else 'Afventer VPN og HTTPS-gateway.'
                routes={r['domain']:{'state':state,'message':message or 'VPN-forbindelsen har en fejl.'} for r in self.route_rows(profile)}
            else:
                try:
                    if self.route_rows(profile):
                        urls=self.sync(profile,observed,routes)
                    else:
                        self.cleanup(profile)
                except Exception as exc:
                    error=exc
                for row in self.route_rows(profile):
                    if row['domain'] not in routes:
                        routes[row['domain']]=self.failure(profile,observed,row,error) if error else self.waiting(profile,observed,row['domain'],'Afventer forbindelsestest.')
                # Do not publish a green result after concurrent editing/reconnection.
                fresh=self.store.get(ident);latest=self.runtime.status(fresh)
                if fresh!=profile or self.signature(fresh,latest)!=signature or latest.get('state')!='online' or not latest.get('relay_active'):
                    self.statuses.pop(ident,None);self.next_check.pop(ident,None);continue
            for domain,value in routes.items():
                if value['state']=='ready':
                    self.progress[ident]['ready'].add(domain)
            checks=list(routes.values())
            checks.extend(observed.get('tcp_status',{}).get(row['id'],{'state':'pending'})
                          for row in target_rows(forwarding_config(profile['gateway'])))
            state='error' if any(v['state']=='error' for v in checks) else 'ready' if checks and all(v['state']=='ready' for v in checks) else 'pending'
            message='Alle domæner er verificeret og online.' if state=='ready' else 'Et eller flere domæner har en fejl. Se status ved domænet.' if state=='error' else 'Opsætning og forbindelsestest er i gang. Se status ved domænet.'
            if not profile['gateway'].get('auto_sync',True):
                message='Automatisk IP og port er slået fra. Cloudflare skal opdateres manuelt.'
            if not self.route_rows(profile):
                message='TCP-forbindelserne er aktive.' if state=='ready' else 'Se status ved de enkelte TCP-mål.'
            warnings=self.store.gateway_dns(ident)['warnings']
            if warnings:
                message+=' Oprydning: '+' '.join(warnings[-5:])
            self.statuses[ident]={'ok':state=='ready','state':state,'message':message,'urls':urls,'routes':routes,
                '_signature':signature,'_checked':time.monotonic()}
