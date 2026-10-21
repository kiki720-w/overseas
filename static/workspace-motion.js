/* Motion is decorative: forms, navigation and saved data keep their existing owners. */
(() => {
  'use strict';
  if (!document.body.classList.contains('coastal-workspace')) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const animations = new Set();
  const seen = new WeakSet();
  const observers = [];
  let frame = 0;
  const active = () => !document.hidden && !reduced.matches;
  function cancel() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    for (const animation of animations) animation.cancel();
    animations.clear();
  }
  function enter(element, index) {
    if (seen.has(element) || !element.getClientRects().length) return;
    seen.add(element);
    if (!active() || typeof element.animate !== 'function') return;
    const animation = element.animate(
      [{opacity:.4,transform:'translateY(8px)'},{opacity:1,transform:'translateY(0)'}],
      {duration:230,delay:Math.min(index,4)*22,easing:'cubic-bezier(.2,.75,.3,1)'}
    );
    animations.add(animation);
    animation.finished.then(() => animations.delete(animation), () => animations.delete(animation));
  }
  function refresh() {
    frame = 0;
    document.querySelectorAll('.coast-page-heading,.hero-copy,.coast-map-panel,.coast-metrics>div,.customer-pool,.coast-library-content,.picker-panel,.candidate-row,#customers tr,#sources>*,.timeline>*').forEach(enter);
  }
  function schedule() { if (active() && !frame) frame = requestAnimationFrame(refresh); }
  for (const id of ['candidate-list','customers','sources','records']) {
    const host = document.getElementById(id);
    if (!host) continue;
    const observer = new MutationObserver(schedule);
    observer.observe(host,{childList:true,subtree:true});
    observers.push(observer);
  }
  const visibility = () => document.hidden ? cancel() : schedule();
  const motion = () => {cancel(); if (active()) schedule();};
  const restore = () => {cancel(); schedule();};
  const hide = event => {
    cancel();
    if (!event.persisted) {
      for (const observer of observers) observer.disconnect();
      document.removeEventListener('visibilitychange',visibility);
      reduced.removeEventListener('change',motion);
      window.removeEventListener('pageshow',restore);
    }
  };
  document.addEventListener('visibilitychange',visibility);
  reduced.addEventListener('change',motion);
  window.addEventListener('pagehide',hide);
  window.addEventListener('pageshow',restore);
  const menus = [...document.querySelectorAll('details.more-tools,details.more-filters')];
  document.addEventListener('pointerdown',event => {
    for (const menu of menus) if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener('click',event => {
    if (!event.target.closest('button,a')) return;
    for (const menu of menus) if (menu.open && menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener('keydown',event => {
    if (event.key !== 'Escape') return;
    for (const menu of menus) if (menu.open) {
      menu.open = false;
      menu.querySelector('summary')?.focus();
    }
  });
  document.querySelector('[data-open-search-connection]')?.addEventListener('click',event => {
    const connection = document.getElementById('search-connection');
    if (!connection) return;
    event.preventDefault(); connection.open = true;
    connection.scrollIntoView({block:'nearest',behavior:reduced.matches?'instant':'smooth'});
    connection.querySelector('input')?.focus({preventScroll:true});
  });
  window.overseasWorkspaceMotion = {get activeAnimations(){return animations.size;},get reduced(){return reduced.matches;}};
  schedule();
})();
