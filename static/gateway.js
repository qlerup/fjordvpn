let editingGateway=null;
function gatewayTCPRoute(row={}){
 const card=el('div','gateway-route gateway-tcp-route');card.dataset.tcpId=row.id;card.dataset.targetType='tcp';
 const top=el('div','gateway-route-heading'),remove=el('button','text-button','Fjern TCP-mål');remove.type='button';remove.onclick=()=>card.remove();top.append(el('strong','','TCP-mål'),remove);card.append(top);
 function field(label,key,value,type='text'){
  const wrapper=el('label','',label),input=el('input');input.type=type;input.dataset.tcpField=key;input.value=value??'';input.required=true;wrapper.append(input);return [wrapper,input];
 }
 const [name,n]=field('Navn','name',row.name);n.maxLength=64;n.placeholder='Fx Plex';
 const [host,h]=field('Lokal IP','host',row.host);h.placeholder='192.168.1.96';h.inputMode='decimal';
 const [port,p]=field('Lokal port','port',row.port??32400,'number');p.min=1;p.max=65535;
 const fields=el('div','field-row');fields.append(host,port);card.append(name,fields);return card;
}
function nextTargetId(kind){
 const used=new Set([...$('#gateway-routes').querySelectorAll(`[data-target-type=${kind}]`)].map(row=>kind==='tcp'?row.dataset.tcpId:row.dataset.gameId));
 const id=Array.from({length:25},(_,i)=>String(i)).find(id=>!used.has(id));
 return id;
}
function gatewayGameRoute(row={}){
 const card=el('div','gateway-route gateway-game-route');card.dataset.targetType='game';card.dataset.gameId=row.id;
 const top=el('div','gateway-route-heading'),remove=el('button','text-button','Fjern gameserver');remove.type='button';remove.onclick=()=>card.remove();top.append(el('strong','','Gameserver · Minecraft Java'),remove);card.append(top);
 card.append(el('p','small muted',row.id==='legacy'?'Denne eksisterende gameserver beholder den delte VPN-port.':'Minecraft Java (TCP) med egen VPN-port. A- og SRV-records opdateres automatisk. Andre spil og UDP understøttes ikke.'));
 function field(label,key,value,type='text'){
  const wrapper=el('label','',label),input=el('input');input.type=type;input.dataset.gameField=key;input.value=value??'';input.required=true;wrapper.append(input);return [wrapper,input];
 }
 const [domain,d]=field('Serverdomæne','domain',row.domain);d.placeholder='mc.ditdomæne.dk';d.maxLength=253;
 const [host,h]=field('Lokal IP','host',row.host);h.placeholder='192.168.1.152';
 const [port,p]=field('Lokal port','port',row.port??25565,'number');p.min=1;p.max=65535;
 const fields=el('div','field-row');fields.append(host,port);
 const keyLabel=el('label','','Cloudflare-nøgle til serverdomænet'),key=el('select');key.dataset.gameField='key_id';
 key.append(el('option','','Brug gatewayens nøgle'));key.firstChild.value='';
 for(const saved of cloudflareKeys){const option=el('option','',saved.name);option.value=saved.id;key.append(option);}key.value=row.key_id||'';keyLabel.append(key);
 card.append(domain,fields,keyLabel,el('p','small muted','Nøglen kræver DNS Edit og Zone Read. Domænet kan ligge i en anden zone end webapps.'));return card;
}
$('#gateway-add-target').onclick=()=>$('#gateway-target-dialog').showModal();
for(const kind of ['web','tcp','game'])$('#gateway-add-'+kind).onclick=()=>{
 const count=$('#gateway-routes').querySelectorAll(`[data-target-type=${kind}]`).length;
 if(count>=25){toast('Der kan højst tilføjes 25 mål af denne type.');return;}
 const row=kind==='web'?gatewayRoute():kind==='tcp'?gatewayTCPRoute({id:nextTargetId(kind)}):gatewayGameRoute({id:nextTargetId(kind)});
 $('#gateway-routes').append(row);$('#gateway-target-dialog').close();row.querySelector('input').focus();
};
function gatewayRoute(row={}){
 const card=el('div','gateway-route gateway-web-route');card.dataset.targetType='web';
 const top=el('div','gateway-route-heading'),title=el('strong','','Subdomæne og lokalt mål'),remove=el('button','text-button','Fjern mål');
 remove.type='button';remove.title='Fjernes fra gateway og Cloudflare, når du gemmer';remove.onclick=()=>card.remove();top.append(title,remove);card.append(top);
 function field(label,key,placeholder,value,type='text'){
  const wrapper=el('label','',label),input=el('input');input.type=type;input.dataset.gatewayField=key;input.placeholder=placeholder;input.value=value??'';wrapper.append(input);return [wrapper,input];
 }
 const [domain,d]=field('Subdomæne','domain','app.ditdomæne.dk',row.domain);d.required=true;d.maxLength=253;
 const directLabel=el('label','check'),direct=el('input');direct.type='checkbox';direct.dataset.gatewayField='direct';direct.checked=!!row.direct;
 directLabel.append(direct,document.createTextNode('Direkte adgang / DNS only (f.eks. Plex)'));
 const directHelp=el('p','small muted','Valgfrit: Trafikken går direkte gennem VPN-gatewayen uden Cloudflare-proxy. Alle stier videresendes. Brug HTTPS-adressen med den aktuelle VPN-port, som vises på gatewaykortet. I Plex angives den under Custom server access URLs; opdatér den, hvis VPN-porten ændres.');
 const flixLabel=el('label','check'),flix=el('input');flix.type='checkbox';flix.dataset.gatewayField='fjordflix';flix.checked=!!row.fjordflix;
 flixLabel.append(flix,document.createTextNode('Direkte FjordFlix-streaming og automatisk streamport'));
 const flixHelp=el('p','small muted','Brug på media-domænet. Cloudflare-proxy slås fra, kun /media/ videresendes, og FjordFlix får gatewayens aktuelle HTTPS-port efter en forbindelsestest. Slå den gamle VPNs automatiske DNS/streamopdatering fra først.');
 direct.onchange=()=>{if(direct.checked)flix.checked=false;};
 flix.onchange=()=>{if(flix.checked)direct.checked=false;};
 const fields=el('div','gateway-target-fields');
 const protocolLabel=el('label','','Forbindelse til målet'),protocol=el('select');protocol.dataset.gatewayField='protocol';
 for(const value of ['http','https']){const option=el('option','',value.toUpperCase());option.value=value;protocol.append(option);}protocol.value=row.protocol||'http';protocolLabel.append(protocol);
 const [host,h]=field('Lokal IP','host','192.168.1.110',row.host);h.required=true;h.inputMode='decimal';
 const [port,p]=field('Lokal port','port','80',row.port??80,'number');p.min=1;p.max=65535;p.required=true;
 fields.append(protocolLabel,host,port);card.append(domain,directLabel,directHelp,flixLabel,flixHelp,fields);
 const [tls,t]=field('Certifikatnavn på lokalt HTTPS-mål (valgfrit)','tls_server_name','Bruger subdomænet, hvis feltet er tomt',row.tls_server_name);t.maxLength=253;
 function updateProtocol(){tls.hidden=protocol.value!=='https';if(protocol.value==='https'&&p.value==='80')p.value='443';}
 protocol.onchange=updateProtocol;card.append(tls);updateProtocol();return card;
}
async function openGatewayForm(profile=null){
 try{await loadCloudflareKeys();}catch(e){toast(e.message);return;}
 editingGateway=profile;$('#gateway-form').reset();$('#gateway-error').hidden=true;
 $('#gateway-title').textContent=profile?'Gatewayindstillinger':'Ny VPN-gateway';
 $('#gateway-name').value=profile?.name||'';$('#gateway-upload').hidden=!!profile;$('#gateway-file').required=!profile;
 $('#gateway-start-label').hidden=!!profile;$('#gateway-start').checked=false;
 $('#gateway-auto-sync').checked=profile?.gateway?.auto_sync??true;
 $('#gateway-key').replaceChildren(el('option','','Vælg en nøgle'),...cloudflareKeys.map(key=>{const option=el('option','',key.name);option.value=key.id;return option;}));
 $('#gateway-key').firstChild.value='';$('#gateway-key').value=profile?.gateway?.key_id||'';
 const config=profile?.gateway||{},mc=config.minecraft||{};
 $('#gateway-routes').replaceChildren(...(config.routes||[]).map(gatewayRoute),
  ...gatewayTCPRows(config.tcp).map(gatewayTCPRoute),
  ...(mc.enabled?[gatewayGameRoute({...mc,id:'legacy'})]:[]),...(config.gameservers||[]).map(gatewayGameRoute));
 const order=config.target_order||[];
 const key=row=>row.dataset.targetType==='web'?'web:'+row.querySelector('[data-gateway-field=domain]').value:row.dataset.targetType==='tcp'?'tcp:'+row.dataset.tcpId:'game:'+row.dataset.gameId;
 const cards=[...$('#gateway-routes').children];cards.sort((a,b)=>{const ai=order.indexOf(key(a)),bi=order.indexOf(key(b));return (ai<0?100:ai)-(bi<0?100:bi);});$('#gateway-routes').replaceChildren(...cards);
 $('#gateway-save').textContent=profile?'Gem gateway':'Opret gateway';
 $('#gateway-dialog').showModal();$('#gateway-name').focus();
}
$('#new-gateway').onclick=()=>openGatewayForm();
$('#gateway-form').onsubmit=async event=>{
 event.preventDefault();$('#gateway-save').disabled=true;$('#gateway-error').hidden=true;
 const routes=[...document.querySelectorAll('#gateway-routes .gateway-web-route')].map(row=>{
  const values={};for(const field of row.querySelectorAll('[data-gateway-field]'))values[field.dataset.gatewayField]=field.type==='checkbox'?field.checked:field.value;
  values.port=Number(values.port);return values;
 });
 const targets=[...document.querySelectorAll('#gateway-routes .gateway-tcp-route')].map(row=>{
  const value={id:row.dataset.tcpId};for(const field of row.querySelectorAll('[data-tcp-field]'))value[field.dataset.tcpField]=field.value;
  value.port=Number(value.port);return value;
 });
 const games=[...document.querySelectorAll('#gateway-routes .gateway-game-route')].map(row=>{
  const value={id:row.dataset.gameId};for(const field of row.querySelectorAll('[data-game-field]'))value[field.dataset.gameField]=field.value;
  value.port=Number(value.port);return value;
 });
 const legacy=games.find(row=>row.id==='legacy');
 const target_order=[...$('#gateway-routes').children].map(row=>row.dataset.targetType==='web'?'web:'+row.querySelector('[data-gateway-field=domain]').value.trim().toLowerCase():row.dataset.targetType==='tcp'?'tcp:'+row.dataset.tcpId:'game:'+row.dataset.gameId);
 const values={name:$('#gateway-name').value,host:'',port:null,relay_enabled:false,
  gateway:{enabled:true,key_id:$('#gateway-key').value,auto_sync:$('#gateway-auto-sync').checked,routes,
   tcp:{enabled:targets.length>0,targets},
   minecraft:legacy?{...legacy,enabled:true}:{enabled:false},gameservers:games.filter(row=>row.id!=='legacy'),target_order},start:$('#gateway-start').checked};
 try{
  if(editingGateway){await api(`/api/profiles/${editingGateway.id}/settings`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});}
  else{const body=new FormData();body.set('settings',JSON.stringify(values));body.set('config',$('#gateway-file').files[0]);await api('/api/gateways',{method:'POST',body});}
  $('#gateway-dialog').close();toast(editingGateway?'Gatewayindstillinger er gemt':'Gatewayen er oprettet');await refresh();
 }catch(e){$('#gateway-error').textContent=e.message;$('#gateway-error').hidden=false;}
 finally{$('#gateway-save').disabled=false;}
};
