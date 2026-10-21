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
})();
