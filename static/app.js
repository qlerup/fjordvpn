const $ = (selector) => document.querySelector(selector);
const csrf = $('meta[name="csrf-token"]').content;
let profiles = [], editing = null, toastTimer;
const states = {online:'Online',off:'Slukket',connecting:'Forbinder',stopping:'Slukker',error:'Fejl',unknown:'Ukendt'};
function el(tag, cls, text) { const node=document.createElement(tag); if(cls) node.className=cls; if(text!==undefined) node.textContent=text; return node; }
function toast(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,4000); }
function gatewayTCPRows(config){return config?.enabled?(config.targets||[{id:'0',name:'TCP',host:config.host,port:config.port}]):[];}
async function api(path,options={}) { const response=await fetch(path,{...options,headers:{'X-CSRF-Token':csrf,...options.headers}}); if(response.status===401){location.href='/login';throw Error('Log ind igen.');} const data=await response.json(); if(!response.ok)throw Error(data.error||'Handlingen kunne ikke gennemføres.'); return data; }
function card(p,legacy=false){
 const gateway=p.gateway?.enabled;
 const node=el('article','vpn-card'); const main=el('div','card-main'); const top=el('div','card-top'); const title=el('div','card-title');
 title.append(el('span','vpn-icon',legacy?'◈':'⇄')); const names=el('div'); names.append(el('h3','',p.name),el('p','card-subtitle',legacy?p.location:'Proton VPN · WireGuard')); title.append(names);
 top.append(title,el('span','badge '+p.state,states[p.state]||'Ukendt')); main.append(top);
 if(gateway)names.querySelector('.card-subtitle').textContent='VPN-gateway · HTTPS';
 const row=el('div','address-row'),address=el('div'); address.append(el('p','label','Offentlig IP og port')); const value=p.public_ip?(p.public_ip+(p.public_port?':'+p.public_port:'')):'Afventer forbindelse'; address.append(el('p','address',value));row.append(address);
 if(p.public_ip){const copy=el('button','copy-button','Kopiér');copy.type='button';copy.addEventListener('click',()=>copyText(value));row.append(copy);} main.append(row); if(p.public_ip&&!legacy) main.append(countryInfo(p.country));
 const targetRow=el('div','target-row'),target=el('div');target.append(el('p','label','Lokalt mål'),el('p','target',p.host&&p.port?`${p.host}:${p.port}`:'Intet mål valgt'));targetRow.append(target,el('span','small muted',p.relay_active?'TCP aktiv':legacy?'Eksisterende opsætning':p.relay_enabled?'TCP afventer':'TCP slået fra'));main.append(targetRow);
 if(p.stream_sync?.enabled) main.append(el('p','small '+(p.stream_status?.ok?'':'muted'),p.stream_status?.message||'Automatisk DNS og streamport afventer kontrol.'));
 if(gateway){
  const mc=p.gateway.minecraft,routeRows=[...p.gateway.routes,...(mc?.enabled?[{...mc,minecraft:true}]:[]),...(p.gateway.gameservers||[]).map(row=>({...row,minecraft:true}))];
  const count=routeRows.length+gatewayTCPRows(p.gateway.tcp).length;
  target.querySelector('.target').textContent=count+' mål';targetRow.lastChild.textContent=p.relay_active?(routeRows.some(r=>r.minecraft)?'HTTPS + gameserver aktiv':'Gateway aktiv'):'Gateway afventer';
  const runtimeError=p.state==='error',available=p.desired&&p.state==='online'&&p.relay_active;
  const checks=available?(p.gateway_status?.routes||{}):{};
  for(const route of routeRows){
   const check=checks[route.domain]||{state:runtimeError?'error':'pending',message:runtimeError?(p.message||'VPN-forbindelsen har en fejl.'):p.desired?'Afventer VPN, DNS og HTTPS-test.':'Gatewayen er slukket.'};
   const state=['ready','pending','error'].includes(check.state)?check.state:'pending';
   const label={ready:'Online',pending:'Opsætter',error:'Fejl'}[state],name=el('span','gateway-route-name');
   const dot=el('span','route-status-dot is-'+state);dot.setAttribute('role','img');dot.setAttribute('aria-label',label+': '+check.message);dot.title=label+': '+check.message;dot.tabIndex=0;
   name.append(dot,el('span','',route.domain+(route.minecraft?' · Minecraft Java':route.direct?' · DNS only':route.fjordflix?' · Direkte stream'+(p.public_port?' :'+p.public_port:''):'')));
   const item=el('div','gateway-card-route');item.append(name,el('span','small muted',`${route.minecraft?'tcp':route.protocol}://${route.host}:${route.port}`));
   if(route.minecraft&&route.id&&p.tcp_status?.['g'+route.id]?.state==='ready')item.append(el('span','small muted',`${p.public_ip}:${p.tcp_status['g'+route.id].public_port}`));
   if(route.direct&&available&&p.public_port)item.append(el('span','small muted',`https://${route.domain}:${p.public_port}`));
   if(state!=='ready')item.append(el('span','gateway-route-check small '+(state==='error'?'route-error':'muted'),(p.desired?label+': ':'')+check.message));
   main.append(item);
  }
  if(available){const state=p.gateway_status?.state||'pending',badge=top.querySelector('.badge');badge.className='badge '+({ready:'online',error:'error',pending:'connecting'}[state]||'connecting');badge.textContent={ready:'Online',error:'Fejl',pending:'Opsætter'}[state]||'Opsætter';}
  main.append(el('p','small muted',p.gateway_status?.message||'DNS og HTTPS-port afventer kontrol.'));
 }
 if(gateway&&p.gateway.tcp?.enabled){
  for(const target of gatewayTCPRows(p.gateway.tcp)){
  const check=p.tcp_status?.[target.id]||(target.id==='0'&&p.tcp_status?.state?p.tcp_status:null)||{state:'pending',message:'Afventer VPN og TCP-port.'};
  const state=check.state||'pending',item=el('div','gateway-card-route'),name=el('span','gateway-route-name');
  const dot=el('span','route-status-dot is-'+state);dot.setAttribute('role','img');dot.setAttribute('aria-label',check.message);dot.title=check.message;
  name.append(dot,el('span','',target.name+' · TCP'));item.append(name,el('span','small muted',`${target.host}:${target.port}`));
  if(state==='ready'&&p.state==='online'&&p.public_ip&&check.public_port){
   const endpoint=`${p.public_ip}:${check.public_port}`,copy=el('button','copy-button','Kopiér TCP-adresse');copy.type='button';copy.setAttribute('aria-label','Kopiér TCP-adresse for '+target.name);copy.onclick=()=>copyText(endpoint);
   item.append(el('span','small',endpoint),copy);
  }
  item.append(el('span','gateway-route-check small '+(state==='error'?'route-error':'muted'),check.message));main.append(item);
  if(target.plex_sync){
   const plex=p.plex_status?.[target.id]||{state:'pending',message:'Afventer kontrol af Plex-adressen.'};
   item.append(el('span','gateway-route-check small '+(plex.state==='error'?'route-error':'muted'),plex.message));
   if(plex.state==='ready'&&plex.url){const link=el('a','small','Åbn Plex');link.href=plex.url+'/web';link.target='_blank';link.rel='noopener';item.append(link);}
  }
  }
 }
 if(gateway&&p.seedbox_status){
  const check=p.seedbox_status,state=['ready','pending','error','off'].includes(check.state)?check.state:'pending';
  const label={ready:'Forbundet',pending:'Forbinder',error:'Fejl',off:'Stoppet'}[state];
  const item=el('div','gateway-card-route seedbox-status'),name=el('span','gateway-route-name');
  const dot=el('span','route-status-dot is-'+state);dot.setAttribute('role','img');dot.setAttribute('aria-label',label+': '+check.message);dot.title=label+': '+check.message;dot.tabIndex=0;
  name.append(dot,el('span','','FjordSeed · '+label));item.append(name);
  if(state==='ready')item.append(el('span','small muted','TCP/UDP :'+check.public_port));
  item.append(el('span','gateway-route-check small '+(state==='error'?'route-error':'muted'),check.message));main.append(item);
 }
 const footer=el('div','card-footer');footer.append(el('p','card-message',p.message||''));const actions=el('div','card-actions');
 if(legacy){if(p.url){const link=el('a','','Åbn ↗');link.href=p.url;link.target='_blank';link.rel='noopener';actions.append(link);}}
 else{const edit=el('button','','Indstillinger');edit.addEventListener('click',()=>p.gateway?.enabled?openGatewayForm(p):openForm(p));const power=el('button','',p.desired?'Sluk':'Start');power.addEventListener('click',async()=>{power.disabled=true;try{await api(`/api/profiles/${p.id}/power`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!p.desired})});toast(p.desired?'VPN slukkes…':'VPN starter…');await refresh();}catch(e){toast(e.message);}finally{power.disabled=false;}});const remove=el('button','delete-profile','Slet');remove.type='button';remove.disabled=!!p.desired;remove.title=p.desired?'Sluk VPN’en, før du sletter den.':'Slet VPN-profil';remove.addEventListener('click',()=>openDelete(p));actions.append(edit,power,remove);}
 footer.append(actions);node.append(main,footer);return node;
}
let deletingProfile=null;
function openDelete(profile){deletingProfile=profile;$('#delete-description').textContent='Vil du slette VPN-profilen “'+profile.name+'”?';$('#delete-error').hidden=true;$('#delete-dialog').showModal();$('#delete-dialog .close-dialog').focus();}
$('#delete-form').addEventListener('submit',async event=>{
 event.preventDefault();if(!deletingProfile)return;const save=$('#delete-confirm');save.disabled=true;$('#delete-error').hidden=true;
 try{await api(`/api/profiles/${deletingProfile.id}/delete`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});$('#delete-dialog').close();deletingProfile=null;toast('VPN-profilen er slettet');await refresh();}
 catch(e){$('#delete-error').textContent=e.message;$('#delete-error').hidden=false;}finally{save.disabled=false;}
});
async function copyText(value){try{if(navigator.clipboard){await navigator.clipboard.writeText(value);}else{const area=el('textarea');area.value=value;document.body.append(area);area.select();document.execCommand('copy');area.remove();}toast('Adressen er kopieret');}catch{toast('Adressen kunne ikke kopieres');}}
async function refresh(){try{const data=await api('/api/profiles');profiles=data.profiles;$('#managed-grid').replaceChildren(...profiles.map(p=>card(p)));$('#empty-state').hidden=profiles.length>0;$('#total-count').textContent=profiles.length;$('#online-count').textContent=profiles.filter(p=>p.state==='online').length;$('#refresh-state').textContent='Opdateret '+new Date().toLocaleTimeString('da-DK',{hour:'2-digit',minute:'2-digit'});$('#page-error').hidden=true;}catch(e){$('#page-error').textContent=e.message;$('#page-error').hidden=false;$('#refresh-state').textContent='Forbindelsen er afbrudt';}}
async function openForm(profile=null){try{await loadCloudflareKeys();}catch(e){toast(e.message);return;}editing=profile;$('#profile-form').reset();$('#form-error').hidden=true;$('#dialog-title').textContent=profile?'Indstillinger':'Opret en ny VPN';$('#save-button').textContent=profile?'Gem indstillinger':'Opret VPN';$('#upload-fields').hidden=!!profile;$('#upload-intro').hidden=!!profile;$('#start-field').hidden=!!profile;$('#profile-file').required=!profile;$('#profile-name').value=profile?.name||'';$('#profile-host').value=profile?.host||'';$('#profile-port').value=profile?.port||'';$('#relay-enabled').checked=profile?.relay_enabled||false;$('#stream-sync-enabled').checked=profile?.stream_sync?.enabled||false;$('#stream-flix-enabled').checked=profile?.stream_sync?.fjordflix??!!profile?.stream_sync?.enabled;$('#stream-minecraft-enabled').checked=profile?.stream_sync?.minecraft_srv||false;$('#stream-domain').value=profile?.stream_sync?.domain||'';$('#stream-key').replaceChildren(el('option','','Vælg en nøgle'),...cloudflareKeys.map(key=>{const option=el('option','',key.name);option.value=key.id;return option;}));$('#stream-key').firstChild.value='';$('#stream-key').value=profile?.stream_sync?.key_id||'';$('#profile-dialog').showModal();$('#profile-name').focus();}
$('#new-button').addEventListener('click',()=>openForm());$('#empty-new').addEventListener('click',()=>openForm());$('#mobile-help').addEventListener('click',()=>$('#help-dialog').showModal());$('#help-button').addEventListener('click',()=>$('#help-dialog').showModal());document.querySelectorAll('.close-dialog').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
$('#profile-form').addEventListener('submit',async event=>{event.preventDefault();const save=$('#save-button');save.disabled=true;$('#form-error').hidden=true;const values={name:$('#profile-name').value,host:$('#profile-host').value,port:$('#profile-port').value?Number($('#profile-port').value):null,relay_enabled:$('#relay-enabled').checked,start:$('#start-enabled').checked,stream_sync:{enabled:$('#stream-sync-enabled').checked,fjordflix:$('#stream-flix-enabled').checked,minecraft_srv:$('#stream-minecraft-enabled').checked,domain:$('#stream-domain').value,key_id:$('#stream-key').value}};try{if(editing){await api(`/api/profiles/${editing.id}/settings`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});}else{const body=new FormData();body.set('settings',JSON.stringify(values));body.set('config',$('#profile-file').files[0]);await api('/api/profiles',{method:'POST',body});}$('#profile-dialog').close();toast(editing?'Indstillingerne er gemt':'VPN er oprettet');await refresh();}catch(e){$('#form-error').textContent=e.message;$('#form-error').hidden=false;}finally{save.disabled=false;}});
let cloudflareKeys = [], editingKey = null;
async function loadCloudflareKeys(){cloudflareKeys=(await api('/api/cloudflare-keys')).keys;}
function resetKeyForm(key=null){
 editingKey=key;$('#cloudflare-key-form').reset();$('#key-error').hidden=true;
 $('#key-name').value=key?.name||'';$('#key-zone').value=key?.zone_id||'';
 $('#key-token').required=!key;$('#key-token').placeholder=key?'Gemt token – lad stå tomt for at beholde det':'';
 $('#key-form-title').textContent=key?'Rediger nøgle':'Tilføj nøgle';
}
function renderKeys(){
 $('#cloudflare-key-list').replaceChildren(...cloudflareKeys.map(key=>{
  const row=el('div','target-row'),name=el('div');name.append(el('strong','',key.name),el('p','small muted','Zone: '+key.zone_id));
  const button=el('button','secondary','Rediger');button.type='button';button.onclick=()=>resetKeyForm(key);row.append(name,button);return row;
 }));
 if(!cloudflareKeys.length) $('#cloudflare-key-list').append(el('p','muted','Ingen gemte nøgler endnu.'));
}
async function openKeys(){try{await loadCloudflareKeys();renderKeys();resetKeyForm();$('#cloudflare-dialog').showModal();}catch(e){toast(e.message);}}
$('#cloudflare-menu').onclick=openKeys;$('#cloudflare-mobile').onclick=openKeys;
$('#key-new').onclick=()=>resetKeyForm();
$('#cloudflare-key-form').onsubmit=async event=>{
 event.preventDefault();$('#key-save').disabled=true;$('#key-error').hidden=true;
 try{
  await api('/api/cloudflare-keys'+(editingKey?'/'+editingKey.id:''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('#key-name').value,zone_id:$('#key-zone').value,token:$('#key-token').value})});
  resetKeyForm();await loadCloudflareKeys();renderKeys();toast('Cloudflare-nøglen er gemt');
 }catch(e){$('#key-error').textContent=e.message;$('#key-error').hidden=false;}finally{$('#key-save').disabled=false;}
};
refresh();setInterval(refresh,5000);
