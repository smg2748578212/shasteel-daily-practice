(()=>{
  const form=document.getElementById('enrollForm'),button=document.getElementById('submitButton'),result=document.getElementById('result');
  let blockedUntil=0;
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(button.disabled||Date.now()<blockedUntil)return;
    button.disabled=true;button.textContent='正在验证并接入…';result.hidden=true;
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),25000);
    try{
      const response=await fetch('/api/daily-practice/public/enroll',{
        method:'POST',credentials:'omit',cache:'no-store',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({user:form.elements.user.value.trim(),password:form.elements.password.value}),signal:controller.signal,
      });
      const data=await response.json();
      if(response.status===429&&data.data?.retry_after){
        const retry=Number(response.headers.get('Retry-After'));
        if(Number.isFinite(retry)&&retry>0)blockedUntil=Date.now()+retry*1000;
      }
      if(!response.ok||data.code!==200)throw new Error(data.msg||'接入暂时失败，请稍后重试');
      const next=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(data.data.next_run));
      result.className='daily-public-result';result.textContent=`接入成功。每天北京时间 16:00 仅查询进度，下次查询：${next}。`;
      form.elements.password.value='';
    }catch(error){
      result.className='daily-public-result error';result.textContent=error.name==='AbortError'?'验证等待超时，请稍后重试':error instanceof TypeError?'暂时无法连接，请稍后重试':error.message;
    }finally{
      clearTimeout(timer);button.disabled=Date.now()<blockedUntil;button.textContent='接入每日查询';result.hidden=false;
    }
  });
  window.addEventListener('pagehide',()=>{form.elements.password.value='';});
})();
