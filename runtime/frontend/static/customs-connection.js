(() => {
  'use strict';
  const $=id=>document.getElementById(id),form=$('customs-connection-form');
  let saved=null,csrf='',busy=false,baseline='';
  const fields={provider_name:'customs-provider',endpoint:'customs-endpoint',auth_type:'customs-auth',header_name:'customs-header',documentation_url:'customs-documentation'};
  const profile=()=>Object.fromEntries(Object.entries(fields).map(([key,id])=>[key,$(id).value.trim()]));
  const fingerprint=()=>JSON.stringify([profile(),$('customs-key').value]);
  const dirty=()=>!form.hidden&&fingerprint()!==baseline;
  function auth(){
    const mode=$('customs-auth').value;
    $('customs-header-field').hidden=mode!=='api_key_header';
    $('customs-key-field').hidden=mode==='none';
    $('customs-key').disabled=mode==='none'||busy;
    if(mode==='none')$('customs-key').value='';
  }
  function restore(){
    for(const [key,id]of Object.entries(fields))$(id).value=saved[key];
    $('customs-key').value='';
    $('customs-key-status').textContent=saved.key_configured?'当前运行已有密钥；留空保留同一服务商、地址和认证范围的密钥。':'本次运行尚未输入密钥。';
    auth();baseline=fingerprint();
  }
  function display(value){
    saved=value;restore();
    $('customs-connection-status').textContent=value.profile_saved?'已保存 · 供应商协议待适配 · 未验证连接':'尚未配置 · 没有发起查询';
    $('customs-connection-summary').textContent=value.profile_saved?value.provider_name+' · '+value.endpoint+' · '+(value.auth_type==='none'?'无需认证':value.key_configured?'本次会话已输入密钥':'本次会话未输入密钥'):'';
    $('customs-edit').disabled=false;
    $('customs-edit').textContent=value.profile_saved?'编辑配置':'填写配置';
    document.dispatchEvent(new CustomEvent('customs:connection-changed',{detail:value}));
  }
  function edit(){restore();form.hidden=false;$('customs-edit').hidden=true;$('customs-provider').focus();}
  function cancel(){restore();form.hidden=true;$('customs-edit').hidden=false;$('customs-edit').focus();}
  $('customs-auth').addEventListener('change',auth);
  $('customs-edit').onclick=edit;$('customs-cancel').onclick=cancel;
  form.onsubmit=async event=>{
    event.preventDefault();if(busy||!saved)return;
    busy=true;$('customs-connection-fields').disabled=true;$('customs-save').disabled=true;$('customs-cancel').disabled=true;
    $('customs-connection-status').textContent='正在保存配置；不会访问供应商…';
    try{
      const response=await fetch('/api/customs/settings',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({revision:saved.revision,profile:profile(),api_key:$('customs-key').value})});
      const value=await response.json();if(!response.ok)throw Error(value.error||'配置未保存');
      display(value);form.hidden=true;$('customs-edit').hidden=false;$('customs-edit').focus();
    }catch(error){$('customs-connection-status').textContent=error.message+'；输入仍保留，可修改后重试或取消。';}
    finally{busy=false;$('customs-connection-fields').disabled=false;$('customs-save').disabled=false;$('customs-cancel').disabled=false;auth();}
  };
  const guard=$('customs-leave-guard');let destination=null;
  function leave(next=null){if(busy)return;destination=next;if(dirty()){guard.showModal();$('customs-stay').focus();}else (destination||window.overseasCustomsReturn)();}
  $('customs-back').onclick=()=>leave();
  $('customs-stay').onclick=()=>{guard.close();$('customs-provider').focus();};
  $('customs-discard').onclick=()=>{guard.close();cancel();(destination||window.overseasCustomsReturn)();};
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape'||event.defaultPrevented||guard.open)return;
    event.preventDefault();
    if(!form.hidden){if(!busy)cancel();}else leave();
  });
  window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[href]');
    if(link&&dirty()&&new URL(link.href).origin===location.origin){event.preventDefault();leave(()=>location.assign(link.href));}
  });
  Promise.all([fetch('/api/meta').then(r=>{if(!r.ok)throw Error('工作区状态不可用');return r.json();}),fetch('/api/customs/settings').then(r=>{if(!r.ok)throw Error('配置读取失败；请刷新后重试');return r.json();})]).then(([meta,value])=>{
    csrf=meta.csrf;display(value);if(!value.profile_saved)edit();
  }).catch(error=>{$('customs-connection-status').textContent=error.message;});
})();
