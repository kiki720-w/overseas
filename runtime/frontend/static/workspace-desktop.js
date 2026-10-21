/* Native title-bar controls own minimize/close; form guards retain unsaved edits. */
(() => {
  const status = document.querySelector('.coast-local-status');
  if (!status) return;
  async function refresh() {
    try {
      const response = await fetch('/api/desktop/status', {cache: 'no-store'});
      const value = await response.json();
      status.textContent = value.qa_window ? '界面验收 · 合成资料' : '本机工作区';
      status.dataset.state = value.state || 'running';
      if (value.qa_window && !document.title.startsWith('Overseas QA')) {
        document.title = 'Overseas QA · ' + document.title;
      }
    } catch {
      status.textContent = '服务已停止 · 请从桌面重新打开';
      status.dataset.state = 'stopped';
    }
  }
  window.addEventListener('focus', refresh);
  document.addEventListener('visibilitychange', () => {if (!document.hidden) refresh();});
  refresh();
  async function offerShortcut() {
    const response=await fetch('/api/desktop/setup',{cache:'no-store'});
    if(!response.ok)return;
    const setup=await response.json();
    if(!setup.prompt)return;
    const dialog=document.createElement('dialog');
    dialog.id='desktop-setup-dialog';
    dialog.setAttribute('aria-labelledby','desktop-setup-title');
    const heading=document.createElement('h2');heading.id='desktop-setup-title';heading.textContent='创建桌面快捷方式？';
    const text=document.createElement('p');text.textContent='使用 Overseas 品牌图标，固定打开当前工作区。可跳过，以后在“本地备份与恢复”中创建。不会替换已有入口。';
    const create=document.createElement('button');create.id='desktop-setup-create';create.type='button';create.textContent='创建快捷方式';
    const skip=document.createElement('button');skip.id='desktop-setup-skip';skip.type='button';skip.className='secondary';skip.textContent='暂时跳过';
    const note=document.createElement('p');note.setAttribute('role','status');
    dialog.append(heading,text,create,document.createTextNode(' '),skip,note);document.body.append(dialog);
    async function save(action){create.disabled=skip.disabled=true;try{const r=await fetch('/api/desktop/setup/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':setup.csrf},body:'{}'});const value=await r.json();if(!r.ok)throw Error(value.error||'快捷方式设置未保存');dialog.close();dialog.remove();}catch(e){note.textContent=e.message;create.disabled=skip.disabled=false;}}
    create.addEventListener('click',()=>save('create'));skip.addEventListener('click',()=>save('skip'));
    dialog.addEventListener('cancel',event=>{event.preventDefault();save('skip');});dialog.showModal();
  }
  offerShortcut().catch(()=>{});
})();
