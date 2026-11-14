/* Rates measured on the VPN tunnel; TCP delivery counts acknowledged bytes. */
function trafficPanel(profile) {
 const box=el('section','traffic-panel');box.setAttribute('aria-label','Live trafik for '+profile.name);
 const heading=el('div','traffic-heading');heading.append(el('p','label','Forbindelse lige nu'),el('span','small muted','Mbit/s'));box.append(heading);
 const traffic=profile.traffic, fresh=traffic?.state==='ready' && Number.isFinite(traffic.age_seconds) && traffic.age_seconds>=0 && traffic.age_seconds<20;
 if(!profile.desired || !fresh){box.append(el('p','small muted',!profile.desired?'VPN er slukket.':traffic?.state==='warming'?'Indsamler måling…':'Afventer en frisk trafikmåling.'));return box;}
 const values=el('div','traffic-values');
 const format=value=>Number.isFinite(value)?value.toLocaleString('da-DK',{minimumFractionDigits:1,maximumFractionDigits:1}):'—';
 for(const [label,key,cls] of [['↑ Upload','upload_mbps','traffic-upload'],['↓ Download','download_mbps','traffic-download'],['Leveret via TCP','delivered_mbps','traffic-delivered']]){
  const item=el('div',cls);item.append(el('span','small',label),el('strong','',format(traffic[key])));values.append(item);
 }
 box.append(values);
 const points=(traffic.history||[]).filter(p=>Number.isFinite(p.at)&&traffic.at-p.at<=300);
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('traffic-chart');svg.setAttribute('viewBox','0 0 300 72');svg.setAttribute('role','img');svg.setAttribute('aria-label','Upload, download og leveret TCP-trafik de seneste fem minutter');
 const maximum=Math.max(1,...points.flatMap(p=>[p.upload_mbps||0,p.download_mbps||0,p.delivered_mbps||0]));
 for(const [key,cls] of [['download_mbps','traffic-download'],['upload_mbps','traffic-upload'],['delivered_mbps','traffic-delivered']]){
  let segment=[];
  const draw=()=>{if(segment.length){const line=document.createElementNS(svg.namespaceURI,'polyline');line.setAttribute('points',segment.join(' '));line.setAttribute('class',cls);svg.append(line);segment=[];}};
  for(const point of points){if(!Number.isFinite(point[key])){draw();continue;}segment.push(`${300-Math.min(300,traffic.at-point.at)},${68-64*point[key]/maximum}`);}draw();
 }
 box.append(svg);
 const recent=points.filter(p=>traffic.at-p.at<=30&&Number.isFinite(p.delivered_mbps));
 const seconds=recent.length>1?recent.at(-1).at-recent[0].at:0;
 // Time weighting keeps the average correct when a sample takes longer.
 let total=0;
 for(let i=1;i<recent.length;i++)total+=recent[i].delivered_mbps*(recent[i].at-recent[i-1].at);
 const active=seconds>=10&&total/seconds>=0.1;
 box.append(el('p','small muted',active?`Leveret i snit: ${format(total/seconds)} Mbit/s · seneste ${Math.round(seconds)} sek.`:'For lidt aktiv TCP-trafik til at vurdere leveringshastigheden.'));
 box.append(el('p','traffic-explanation small muted','Upload og download set fra VPN’en. Leveret tæller TCP-data, som modtageren har bekræftet. Graf: op til 5 min. Aktiv trafik viser ikke forbindelsens maksimale kapacitet.'));
 return box;
}
