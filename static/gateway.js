let editingGateway=null;
function gatewayTCPFields(){const enabled=$('#gateway-tcp-enabled').checked;$('#gateway-tcp-fields').hidden=!enabled;$('#gateway-tcp-fields').disabled=!enabled;}
$('#gateway-tcp-enabled').onchange=gatewayTCPFields;
function gatewayMinecraftFields(){const enabled=$('#gateway-minecraft-enabled').checked;$('#gateway-minecraft-fields').hidden=!enabled;$('#gateway-minecraft-fields').disabled=!enabled;}
$('#gateway-minecraft-enabled').onchange=gatewayMinecraftFields;
function gatewayRoute(row={}){
 const card=el('div','gateway-route');
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
 const mc=profile?.gateway?.minecraft||{};
 const tcp=profile?.gateway?.tcp||{};
 $('#gateway-tcp-enabled').checked=!!tcp.enabled;$('#gateway-tcp-host').value=tcp.host||'';$('#gateway-tcp-port').value=tcp.port??32400;gatewayTCPFields();
 $('#gateway-minecraft-enabled').checked=!!mc.enabled;
 $('#gateway-minecraft-domain').value=mc.domain||'';$('#gateway-minecraft-host').value=mc.host||'';$('#gateway-minecraft-port').value=mc.port??25565;
 $('#gateway-minecraft-key').replaceChildren(el('option','','Brug gatewayens nøgle'),...cloudflareKeys.map(key=>{const option=el('option','',key.name);option.value=key.id;return option;}));
 $('#gateway-minecraft-key').firstChild.value='';$('#gateway-minecraft-key').value=mc.key_id||'';gatewayMinecraftFields();
 $('#gateway-routes').replaceChildren(...(profile?.gateway?.routes||[{}]).map(gatewayRoute));
 $('#gateway-save').textContent=profile?'Gem gateway':'Opret gateway';
 $('#gateway-dialog').showModal();$('#gateway-name').focus();
}
$('#new-gateway').onclick=()=>openGatewayForm();
$('#gateway-add-route').onclick=()=>{
 if($('#gateway-routes').children.length>=25){toast('En gateway kan have op til 25 subdomæner.');return;}
 const row=gatewayRoute();$('#gateway-routes').append(row);row.querySelector('input').focus();
};
$('#gateway-form').onsubmit=async event=>{
 event.preventDefault();$('#gateway-save').disabled=true;$('#gateway-error').hidden=true;
 const routes=[...$('#gateway-routes').children].map(row=>{
  const values={};for(const field of row.querySelectorAll('[data-gateway-field]'))values[field.dataset.gatewayField]=field.type==='checkbox'?field.checked:field.value;
  values.port=Number(values.port);return values;
 });
 const values={name:$('#gateway-name').value,host:'',port:null,relay_enabled:false,
  gateway:{enabled:true,key_id:$('#gateway-key').value,auto_sync:$('#gateway-auto-sync').checked,routes,
   tcp:{enabled:$('#gateway-tcp-enabled').checked,host:$('#gateway-tcp-host').value,port:Number($('#gateway-tcp-port').value)},
   minecraft:{enabled:$('#gateway-minecraft-enabled').checked,domain:$('#gateway-minecraft-domain').value,
    host:$('#gateway-minecraft-host').value,port:Number($('#gateway-minecraft-port').value),key_id:$('#gateway-minecraft-key').value}},start:$('#gateway-start').checked};
 try{
  if(editingGateway){await api(`/api/profiles/${editingGateway.id}/settings`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});}
  else{const body=new FormData();body.set('settings',JSON.stringify(values));body.set('config',$('#gateway-file').files[0]);await api('/api/gateways',{method:'POST',body});}
  $('#gateway-dialog').close();toast(editingGateway?'Gatewayindstillinger er gemt':'Gatewayen er oprettet');await refresh();
 }catch(e){$('#gateway-error').textContent=e.message;$('#gateway-error').hidden=false;}
 finally{$('#gateway-save').disabled=false;}
};
