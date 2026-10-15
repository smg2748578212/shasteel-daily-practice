(()=>{
  const $=id=>document.getElementById(id),day=new URLSearchParams(location.search).get('date');
  const labels={completed:'已完成',pending:'未完成',failed:'查询失败',unknown:'尚未查询',checking:'正在查询',paused:'已暂停'};
  let data=null,loading=false;
  function time(value){
    if(!value)return '—';const parsed=new Date(value);if(Number.isNaN(parsed.getTime()))return '—';
    return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(parsed);
  }
  function empty(message){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=4;cell.className='daily-empty';cell.textContent=message;row.append(cell);$('rosterBody').replaceChildren(row);}
  function render(){
    if(!data)return;
    const query=$('rosterSearch').value.trim().toLowerCase(),status=$('rosterFilter').value;
    const people=data.people.filter(person=>(person.name+' '+person.team).toLowerCase().includes(query)&&(status==='all'||person.status===status));
    $('rosterCount').textContent=`显示 ${people.length} / ${data.people.length} 人`;
    if(!people.length){empty(data.people.length?'未找到符合条件的人员。':'暂无接入人员。');return;}
    const fragment=document.createDocumentFragment();
    for(const person of people){
      const row=document.createElement('tr');
      for(const text of [person.name,person.team||'—']){const cell=document.createElement('td');cell.textContent=text;row.append(cell);}
      const cell=document.createElement('td'),badge=document.createElement('span');
      const safeStatus=Object.hasOwn(labels,person.status)?person.status:'unknown';
      badge.className='roster-badge roster-badge-'+safeStatus;badge.textContent=labels[safeStatus];cell.append(badge);row.append(cell);
      const checked=document.createElement('td');checked.textContent=time(person.checked_at);row.append(checked);fragment.append(row);
    }
    $('rosterBody').replaceChildren(fragment);
  }
  async function refresh(){
    if(loading)return;loading=true;$('refreshRoster').disabled=true;$('rosterError').hidden=true;
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
    try{
      const response=await fetch('/api/daily-practice/public/roster'+(day!==null?'?date='+encodeURIComponent(day):''),{credentials:'omit',cache:'no-store',signal:controller.signal});
      const body=await response.json().catch(()=>({msg:'名单服务暂时不可用，请稍后重试'}));
      if(!response.ok||body.code!==200)throw new Error(body.msg||'名单加载失败');
      data=body.data;const c=data.counts;
      $('countTotal').textContent=c.total;$('countComplete').textContent=c.completed;$('countPending').textContent=c.pending;
      $('countAttention').textContent=c.failed+c.unknown+c.checking;
      $('enabledCount').textContent=`启用 ${c.total-c.paused} 人 · 暂停 ${c.paused} 人`;
      $('attentionDetail').textContent=`失败 ${c.failed} · 未查询 ${c.unknown} · 查询中 ${c.checking}`;
      $('rosterTitle').textContent=data.date+' · 全部名单';
      $('rosterDescription').textContent=data.source==='snapshot'?'通知当日的完整名单快照，包含已完成和未完成人员。':'今日实时名单，每天北京时间 16:00 查询后更新完成情况。';
      $('rosterUpdated').textContent=(data.source==='snapshot'?'快照生成：':'名单更新：')+time(data.generated_at)+' · 北京时间';
      $('todayLink').hidden=day===null;render();
    }catch(error){
      $('rosterError').textContent=error.name==='AbortError'?'名单加载超时，请稍后刷新':error instanceof TypeError?'暂时无法连接名单服务，请稍后刷新':error.message;
      $('rosterError').hidden=false;$('todayLink').hidden=day===null;
      if(!data)empty('暂时无法加载名单，请刷新重试。');
    }finally{clearTimeout(timer);loading=false;$('refreshRoster').disabled=false;}
  }
  $('rosterSearch').addEventListener('input',render);$('rosterFilter').addEventListener('change',render);$('refreshRoster').addEventListener('click',refresh);
  refresh();setInterval(()=>{if(!document.hidden&&data?.source==='live')refresh();},30000);
})();
