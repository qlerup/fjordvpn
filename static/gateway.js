// Reuse authentication, CSRF and DOM helpers from app.js.
let gateway = null, gatewayDraft = null, routeEditing = null, deleteIndex = null;
let gatewayBusy = false;
const gatewayStates = {off:'Slukket', running:'Container kører', connecting:'Anvender', stopping:'Stopper', error:'Fejl', unknown:'Ukendt'};
function gatewayError(selector, error) {
  $(selector).textContent = error.message;
  $(selector).hidden = false;
}
function renderGateway() {
  $('#gateway-state').textContent = gatewayStates[gateway.state] || 'Afventer';
  $('#gateway-state').className = 'badge ' + (gateway.state === 'error' ? 'error' : '');
  $('#gateway-message').textContent = gateway.message || 'Ændringerne anvendes automatisk.';
  $('#gateway-settings').textContent = gateway.configured ? 'Skift tunnel' : 'Opsæt tunnel';
  $('#gateway-dns').hidden = !gateway.configured;
  $('#gateway-dns-target').textContent = gateway.dns_target;
  $('#gateway-power').textContent = gateway.enabled ? 'Stop tunnel' : 'Start tunnel';
  $('#gateway-power').disabled = gatewayBusy || (!gateway.enabled && (!gateway.configured || !gateway.routes.some(r => r.enabled)));
  $('#gateway-empty').hidden = gateway.routes.length > 0;
  $('#gateway-routes').replaceChildren(...gateway.routes.map((route, index) => {
    const row = el('div', 'gateway-route'), text = el('div', 'gateway-route-text');
    text.append(el('strong', '', route.hostname), el('p', 'muted small', `${route.scheme}://${route.host}:${route.port} · ${route.enabled ? 'Aktiv regel' : 'Deaktiveret'}`));
    const actions = el('div', 'card-actions');
    const edit = el('button', '', 'Redigér'); edit.type = 'button';
    edit.setAttribute('aria-label', `Redigér ${route.hostname}`);
    edit.addEventListener('click', () => openRoute(index));
    const remove = el('button', 'danger', 'Fjern'); remove.type = 'button';
    remove.setAttribute('aria-label', `Fjern ${route.hostname}`);
    remove.addEventListener('click', () => {
      gatewayDraft = structuredClone(gateway); deleteIndex = index;
      $('#route-delete-name').textContent = route.hostname;
      $('#route-delete-error').hidden = true;
      $('#route-delete-dialog').showModal();
    });
    actions.append(edit, remove); row.append(text, actions); return row;
  }));
}
async function refreshGateway() {
  if (gatewayBusy) return;
  try {
    gateway = await api('/api/gateway'); renderGateway();
    $('#gateway-error').hidden = true;
  } catch (error) { gatewayError('#gateway-error', error); }
}
async function saveGateway(draft, credentials) {
  gatewayBusy = true;
  try {
    gateway = await api('/api/gateway', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision:draft.revision, enabled:draft.enabled, routes:draft.routes, ...(credentials ? {credentials} : {})})});
    renderGateway(); toast('Gemt. Tunnelændringer anvendes automatisk.');
  } finally { gatewayBusy = false; }
  await refreshGateway();
}
function openRoute(index = null) {
  if (!gateway || gatewayBusy) return;
  gatewayDraft = structuredClone(gateway); routeEditing = index;
  const route = index === null ? {} : gatewayDraft.routes[index];
  $('#route-form').reset(); $('#route-form-error').hidden = true;
  $('#route-title').textContent = index === null ? 'Tilføj subdomæne' : 'Redigér subdomæne';
  $('#route-hostname').value = route.hostname || '';
  $('#route-host').value = route.host || '';
  $('#route-port').value = route.port || '';
  $('#route-scheme').value = route.scheme || 'http';
  $('#route-tls').value = route.origin_server_name || '';
  $('#route-tls-field').hidden = $('#route-scheme').value !== 'https';
  $('#route-enabled').checked = route.enabled !== false;
  $('#route-dialog').showModal(); $('#route-hostname').focus();
}
$('#route-new').addEventListener('click', () => openRoute());
$('#route-scheme').addEventListener('change', () => { $('#route-tls-field').hidden = $('#route-scheme').value !== 'https'; });
$('#gateway-settings').addEventListener('click', () => {
  if (!gateway || gatewayBusy) return;
  gatewayDraft = structuredClone(gateway);
  $('#gateway-form').reset(); $('#gateway-form-error').hidden = true;
  $('#gateway-dialog').showModal();
});
$('#gateway-form').addEventListener('submit', async event => {
  event.preventDefault(); $('#gateway-save').disabled = true; $('#gateway-form-error').hidden = true;
  try {
    const file = $('#gateway-file').files[0];
    if (!file || file.size > 16384) throw Error('Vælg tunnelens JSON-fil på højst 16 KB.');
    let credentials;
    try { credentials = JSON.parse(await file.text()); }
    catch { throw Error('Filen indeholder ikke gyldig JSON.'); }
    await saveGateway(gatewayDraft, credentials); $('#gateway-dialog').close(); $('#gateway-form').reset();
  } catch (error) { gatewayError('#gateway-form-error', error); }
  finally { $('#gateway-save').disabled = false; }
});
$('#route-form').addEventListener('submit', async event => {
  event.preventDefault(); $('#route-save').disabled = true; $('#route-form-error').hidden = true;
  try {
    const draft = structuredClone(gatewayDraft);
    const route = {hostname:$('#route-hostname').value, host:$('#route-host').value, port:Number($('#route-port').value),
      scheme:$('#route-scheme').value, enabled:$('#route-enabled').checked,
      origin_server_name:$('#route-scheme').value === 'https' ? $('#route-tls').value : ''};
    if (routeEditing === null) draft.routes.push(route); else draft.routes[routeEditing] = route;
    await saveGateway(draft); $('#route-dialog').close();
  } catch (error) { gatewayError('#route-form-error', error); }
  finally { $('#route-save').disabled = false; }
});
$('#route-delete-form').addEventListener('submit', async event => {
  event.preventDefault(); $('#route-delete-confirm').disabled = true; $('#route-delete-error').hidden = true;
  try {
    const draft = structuredClone(gatewayDraft); draft.routes.splice(deleteIndex, 1);
    if (!draft.routes.some(r => r.enabled)) draft.enabled = false;
    await saveGateway(draft); $('#route-delete-dialog').close();
  } catch (error) { gatewayError('#route-delete-error', error); }
  finally { $('#route-delete-confirm').disabled = false; }
});
$('#gateway-power').addEventListener('click', async () => {
  if (!gateway || gatewayBusy) return;
  const draft = structuredClone(gateway); draft.enabled = !draft.enabled;
  $('#gateway-power').disabled = true;
  try { await saveGateway(draft); }
  catch (error) { gatewayError('#gateway-error', error); renderGateway(); }
});
refreshGateway(); setInterval(refreshGateway, 5000);
