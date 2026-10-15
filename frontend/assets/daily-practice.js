(async()=>{
  const context=await Site.ready;if(!context)return;
  const $=id=>document.getElementById(id),e=Site.escape;
  const api='/api/daily-practice';let accounts=[],editing=null,loading=false,historyGeneration=0;
  const weekdays=['周一','周二','周三','周四','周五','周六','周日'];
  const stages={queued:'排队等待',starting:'正在启动',authenticating:'正在登录门户',checking:'检查完成状态',answering:'正在答题',verifying:'复核完成结果'};
  const labels={completed:'已完成',pending:'待完成',failed:'执行失败',interrupted:'执行中断',queued:'排队中',running:'执行中',unknown:'尚未检查'};
  function date(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?'—':new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d);}
  function duration(job){if(!job.started_at)return '尚未开始';const seconds=Math.max(0,Math.round((new Date(job.finished_at||Date.now())-new Date(job.started_at))/1000));return seconds<60?`${seconds} 秒`:`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;}
  function badge(status){return `<span class="site-badge ${status==='completed'?'on':['failed','interrupted'].includes(status)?'daily-badge-error':['running','queued'].includes(status)?'daily-badge-active':''}">${e(labels[status]||status)}</span>`;}
  const active=a=>a.latest_job&&['queued','running'].includes(a.latest_job.status);
  const attention=a=>a.latest_job&&['failed','interrupted'].includes(a.latest_job.status);
  function render(){
    const q=$('search').value.trim().toLowerCase(),filter=$('statusFilter').value;
    $('statPeople').textContent=accounts.length;$('statEnabled').textContent=`${accounts.filter(a=>a.enabled).length} 人已启用定时任务`;
    $('statComplete').textContent=accounts.filter(a=>a.today_status==='completed').length;$('statActive').textContent=accounts.filter(active).length;$('statAttention').textContent=accounts.filter(attention).length;
    const visible=accounts.filter(a=>(a.employee_name+a.employee_id+a.team).toLowerCase().includes(q)).filter(a=>filter==='all'||filter==='completed'&&a.today_status==='completed'||filter==='pending'&&a.today_status==='pending'||filter==='active'&&active(a)||filter==='attention'&&attention(a)||filter==='paused'&&!a.enabled);
    $('peopleCount').textContent=`${visible.length} 位人员`;
    $('peopleBody').innerHTML=visible.length?visible.map(a=>{
      const j=a.latest_job,isActive=active(a),status=isActive?j.status:a.today_status;
      const days=a.run_days.length===7?'每天':a.run_days.map(d=>weekdays[d]).join('、');
      const progress=isActive?`${stages[j.stage]||'准备执行'}${j.total>0?` · ${Math.min(j.current,j.total)} / ${j.total} 题`:''}`:(attention(a)?j.message:'');
      const ratio=j?.total>0?Math.min(95,Math.round(j.current/j.total*95)):0;
      return `<tr><td><strong>${e(a.employee_name)}</strong><small>工号 ${e(a.employee_id)}${a.team?' · '+e(a.team):''}</small></td><td><strong>${e(a.run_time)}</strong> ${a.enabled?'':'<span class="site-badge">已暂停</span>'}<small>${a.execution_mode==='check'?'仅查看进度':'完成每日一练'} · ${e(days)}${a.enrollment_source==='public'?' · 自助接入':''}</small></td><td>${badge(status)}${progress?`<div class="daily-progress">${e(progress)}</div>`:''}${isActive&&j.total>0?`<div class="daily-progress-track"><span style="width:${ratio}%"></span></div>`:''}</td><td>${date(a.checked_at)}<small>下次：${a.enabled?date(a.next_run):'已暂停'}</small></td><td><div class="site-inline-actions"><button class="site-button secondary small" data-action="check" data-id="${a.id}" ${isActive||!a.enabled?'disabled':''}>检查状态</button><button class="site-button secondary small" data-action="run" data-id="${a.id}" ${isActive||!a.enabled||a.execution_mode==='check'?'disabled':''}>完成每日一练</button><button class="site-button secondary small" data-action="edit" data-id="${a.id}">编辑</button><button class="site-button secondary small" data-action="toggle" data-id="${a.id}">${a.enabled?'暂停':'启用'}</button><button class="site-button danger small" data-action="delete" data-id="${a.id}" ${isActive?'disabled':''}>移除</button></div></td></tr>`;
    }).join(''):`<tr><td colspan="5" class="daily-empty">${accounts.length?'未找到符合条件的人员。':'还没有人员，添加后即可设置自动执行计划。'}</td></tr>`;
    const selected=$('historyPerson').value;
    $('historyPerson').innerHTML='<option value="">全部人员</option>'+accounts.map(a=>`<option value="${a.id}">${e(a.employee_name)} · ${e(a.employee_id)}</option>`).join('');
    if(accounts.some(a=>String(a.id)===selected))$('historyPerson').value=selected;
  }
  async function history(){
    const generation=++historyGeneration;
    const jobs=await Site.api(api+'/history'+($('historyPerson').value?'?account_id='+encodeURIComponent($('historyPerson').value):''));if(generation!==historyGeneration)return;
    $('historyBody').innerHTML=jobs.length?jobs.map(j=>`<tr><td><strong>${e(j.employee_name)}</strong><small>${e(j.employee_id)}</small></td><td>${j.mode==='check'?'状态检查':'自动答题'}<small>${j.trigger==='schedule'?'定时计划':j.trigger==='migration'?'迁移记录':'手动触发'}</small></td><td>${date(j.started_at||j.created_at)}<small>${duration(j)}</small></td><td>${badge(j.status)}</td><td>${e(j.message||stages[j.stage]||'')}</td></tr>`).join(''):'<tr><td colspan="5" class="daily-empty">暂无执行记录。</td></tr>';
  }
  async function refresh(notify=false){
    if(loading)return;loading=true;$('refreshButton').disabled=true;
    try{
      const data=await Site.api(api+'/overview');accounts=data.accounts;render();await history();
      $('serviceState').textContent=data.scheduler?'自动执行服务运行中':'自动执行服务未就绪';$('serviceState').className='site-badge '+(data.scheduler?'on':'daily-badge-error');
      $('updatedAt').textContent=`最近更新 ${date(new Date().toISOString())} · 北京时间 · 仅管理员可维护，密码不回显`;
      if(notify)Site.notice('数据已刷新');
    }catch(error){if(error.status===401){location.replace('/?next='+encodeURIComponent(location.pathname));return;}Site.notice(error.message,true);$('serviceState').textContent='连接暂时不可用';$('serviceState').className='site-badge daily-badge-error';}
    finally{loading=false;$('refreshButton').disabled=false;}
  }
  function open(account=null){
    editing=account?.id??null;const form=$('personForm');form.reset();$('formError').hidden=true;
    $('dialogTitle').textContent=account?'编辑人员':'添加人员';form.elements.employee_id.readOnly=!!account;
    for(const key of ['employee_name','employee_id','team','run_time'])form.elements[key].value=account?.[key]??(key==='run_time'?'07:00':'');
    const selfService=account?.enrollment_source==='public';$('publicEnrollmentHint').hidden=!selfService;
    form.elements.execution_mode.disabled=selfService;form.elements.run_time.readOnly=selfService;
    form.elements.execution_mode.value=account?.execution_mode??'check';
    form.elements.enabled.value=account?.enabled===false?'0':'1';form.elements.password.required=!account;
    form.elements.password.value='';form.elements.password.placeholder=account?'留空保留现有密码':'门户登录密码';
    $('passwordHint').textContent=account?'留空不修改。已保存的密码不会回显。':'仅用于该人员的门户登录，保存后不回显。';
    form.querySelectorAll('[name=run_days]').forEach(input=>{input.disabled=selfService;input.checked=(account?.run_days??[0,1,2,3,4,5,6]).includes(Number(input.value));});
    $('personDialog').showModal();
  }
  $('addButton').onclick=()=>open();$('refreshButton').onclick=()=>refresh(true);
  $('search').oninput=render;$('statusFilter').onchange=render;$('historyPerson').onchange=()=>history().catch(error=>Site.notice(error.message,true));
  $('closeDialog').onclick=$('cancelDialog').onclick=()=>$('personDialog').close();
  $('personDialog').addEventListener('close',()=>{$('personForm').elements.password.value='';});
  $('personForm').onsubmit=async event=>{
    event.preventDefault();const form=event.currentTarget,button=$('savePerson');button.disabled=true;$('formError').hidden=true;
    const data={employee_name:form.elements.employee_name.value.trim(),employee_id:form.elements.employee_id.value.trim(),team:form.elements.team.value.trim(),password:form.elements.password.value,execution_mode:form.elements.execution_mode.value,enabled:form.elements.enabled.value==='1',run_time:form.elements.run_time.value,run_days:[...form.querySelectorAll('[name=run_days]:checked')].map(input=>Number(input.value))};
    try{await Site.send(editing?'PUT':'POST',api+'/accounts'+(editing?'/'+editing:''),data);$('personDialog').close();Site.notice('人员信息已保存');await refresh();}
    catch(error){$('formError').textContent=error.message;$('formError').hidden=false;}finally{button.disabled=false;}
  };
  $('peopleBody').onclick=async event=>{
    const button=event.target.closest('button[data-action]');if(!button)return;const account=accounts.find(a=>a.id===Number(button.dataset.id));if(!account)return;
    const action=button.dataset.action;if(action==='edit'){open(account);return;}
    if(action==='delete'&&!window.confirm(`移除 ${account.employee_name}（${account.employee_id}）？执行历史会保留。`))return;
    button.disabled=true;
    try{
      if(action==='delete')await Site.api(api+'/accounts/'+account.id,{method:'DELETE'});
      else if(action==='toggle')await Site.send('PUT',api+'/accounts/'+account.id,{enabled:!account.enabled});
      else await Site.send('POST',api+'/accounts/'+account.id+'/run',{mode:action==='check'?'check':'run'});
      Site.notice(action==='delete'?'人员已移除':action==='toggle'?'执行计划已更新':'已加入执行队列');await refresh();
    }catch(error){Site.notice(error.message,true);}finally{button.disabled=false;}
  };
  await refresh();setInterval(()=>{if(!document.hidden)refresh();},5000);
})();
