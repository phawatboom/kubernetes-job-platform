let token = sessionStorage.getItem('dispatch-token');
const el = id => document.getElementById(id);
let lastRender = '';
async function api(path, body) {
  const response = await fetch(path, {method: body ? 'POST' : 'GET', headers: {'Content-Type':'application/json', Authorization:`Bearer ${token || ''}`}, body: body ? JSON.stringify(body) : undefined});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
  return data;
}
function node(tag, text, className) { const item = document.createElement(tag); item.textContent = text; if(className) item.className=className; return item; }
async function refresh() {
  const data = await api('/api/reports');
  el('connection').textContent='● Cluster connected';
  el('revision').textContent=`revision ${data.revision}`;
  if(JSON.stringify(data.reports) === lastRender) return;
  lastRender=JSON.stringify(data.reports);
  el('count').textContent=`${data.reports.length} reports`;
  el('reports').replaceChildren();
  if(!data.reports.length) el('reports').append(node('p','Your next report starts here.','empty'));
  for(const report of data.reports) {
    const card=node('article', '', 'report');
    const title=node('div',''); title.append(node('h3',`Report ${report.id.slice(0,8)}`),node('p',new Date(report.created_at).toLocaleTimeString()));
    const info=node('div','','info'); info.append(node('p',`${report.mode} / ${report.attempts} worker attempt${report.attempts===1?'':'s'}`));
    if(report.last_error) info.append(node('p',report.last_error));
    card.append(title,info,node('span',report.status,`badge ${report.status}`));
    if(report.result) {const details=node('details',''); details.append(node('summary','Inspect durable result'),node('pre',JSON.stringify(report.result,null,2))); card.append(details);}
    el('reports').append(card);
  }
}
el('submit').addEventListener('submit',async event=>{
  event.preventDefault(); const button=event.target.querySelector('button'); button.disabled=true;
  const body={request_key:crypto.randomUUID(),mode:new FormData(event.target).get('mode')};
  try {await api('/api/reports',body); el('notice').textContent='Request persisted. Waiting for a Kubernetes worker.'; await refresh();}
  catch(error) {el('notice').textContent=error.message;}
  finally {button.disabled=false;}
});
async function start(){
  try {if(!token){token=(await api('/api/sessions',{})).token;sessionStorage.setItem('dispatch-token',token);}await refresh();}
  catch(error){el('connection').textContent='Connection unavailable';el('notice').textContent=error.message;}
}
start(); setInterval(()=>{if(token) refresh().catch(()=>{el('connection').textContent='Reconnecting…';});},1500);
