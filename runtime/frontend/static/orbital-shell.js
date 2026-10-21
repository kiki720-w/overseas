/* Visual navigation only. Business forms, handlers and data remain owned by each page. */
(() => {
  const body = document.body;
  // Current documents include the complete rail and wordmark; retain compatibility with old documents.
  if (body.dataset.orbitalShell === 'static') return;
  if (!body.classList.contains('orbital-workspace')) return;
  const rail = document.querySelector('.workspace-sidebar');
  const brand = rail?.querySelector('.brand');
  if (!rail || !brand) return;
  const img = brand.querySelector('img');
  if (img) { img.src = '/assets/orbital/brand-orbit.png'; img.width = 34; img.height = 34; }
  const word = brand.querySelector('.brand-wordmark');
  if (word) word.hidden = true;
  const icons = {'/research':'search','/':'file-description','/deals':'chart-bar','/knowledge':'notebook','/settings':'settings'};
  rail.querySelectorAll('nav a').forEach(link => {
    const label = link.textContent.trim();
    link.setAttribute('aria-label', label);
    link.title = label;
    link.replaceChildren();
    const icon = document.createElement('img');
    icon.src = '/assets/orbital/icon-' + icons[link.getAttribute('href')] + '.svg';
    icon.alt = ''; icon.width = 24; icon.height = 24;
    const text = document.createElement('span'); text.className = 'orbital-nav-label'; text.textContent = label;
    link.append(icon, text);
  });
  const main = document.querySelector('.workspace-main');
  const mark = document.createElement('a'); mark.className = 'orbital-shell-wordmark';
  mark.href = '/research'; mark.textContent = 'overseas'; mark.setAttribute('aria-label','Overseas · 发现客户');
  main?.prepend(mark);
})();
