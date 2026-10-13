let editingGateway=null;
function gatewayRoute(row={}){
 const card=el('div','gateway-route');
 const top=el('div','gateway-route-heading'),title=el('strong','','Subdomæne og lokalt mål'),remove=el('button','text-button','Fjern mål');
 remove.type='button';remove.onclick=()=>card.remove();top.append(title,remove);card.append(top);
 function field(label,key,placeholder,value,type='text'){
  const wrapper=el('label','',label),input=el('input');input.type=type;input.dataset.gatewayField=key;input.placeholder=placeholder;input.value=value??'';wrapper.append(input);return [wrapper,input];
 }
 const [domain,d]=field('Subdomæne','domain','app.ditdomæne.dk',row.domain);d.required=true;d.maxLength=253;
 const fields=el('div','gateway-target-fields');
 const protocolLabel=el('label','','Forbindelse til målet'),protocol=el('select');protocol.dataset.gatewayField='protocol';
 for(const value of ['http','https']){const option=el('option','',value.toUpperCase());option.value=value;protocol.append(option);}protocol.value=row.protocol||'http';protocolLabel.append(protocol);
 const [host,h]=field('Lokal IP','host','192.168.1.110',row.host);h.required=true;h.inputMode='decimal';
 const [port,p]=field('Lokal port','port','80',row.port??80,'number');p.min=1;p.max=65535;p.required=true;
 fields.append(protocolLabel,host,port);card.append(domain,fields);
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
 $('#gateway-key').replaceChildren(el('option','','Vælg en nøgle'),...cloudflareKeys.map(key=>{const option=el('option','',key.name);option.value=key.id;return option;}));
 $('#gateway-key').firstChild.value='';$('#gateway-key').value=profile?.gateway?.key_id||'';
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
  const values={};for(const field of row.querySelectorAll('[data-gateway-field]'))values[field.dataset.gatewayField]=field.value;
  values.port=Number(values.port);return values;
 });
 const values={name:$('#gateway-name').value,host:'',port:null,relay_enabled:false,
  gateway:{enabled:true,key_id:$('#gateway-key').value,routes},start:$('#gateway-start').checked};
 try{
  if(editingGateway){await api(`/api/profiles/${editingGateway.id}/settings`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});}
  else{const body=new FormData();body.set('settings',JSON.stringify(values));body.set('config',$('#gateway-file').files[0]);await api('/api/gateways',{method:'POST',body});}
  $('#gateway-dialog').close();toast(editingGateway?'Gatewayindstillinger er gemt':'Gatewayen er oprettet');await refresh();
 }catch(e){$('#gateway-error').textContent=e.message;$('#gateway-error').hidden=false;}
 finally{$('#gateway-save').disabled=false;}
};
