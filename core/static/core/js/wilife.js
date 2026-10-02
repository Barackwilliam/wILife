/* wILife — shell behaviour: theme, sidebar, toasts, count-up, meters, table search. */
(function () {
  const root = document.documentElement;
  const css = (name) => getComputedStyle(root).getPropertyValue(name).trim();

  // ---- Theme -------------------------------------------------------------
  function setTheme(theme) {
    root.setAttribute('data-bs-theme', theme);
    document.cookie = 'theme=' + theme + ';path=/;max-age=' + 365 * 24 * 3600 + ';samesite=lax';
    document.dispatchEvent(new CustomEvent('wilife:theme', { detail: theme }));
  }
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-theme-toggle]');
    if (!btn) return;
    setTheme(root.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark');
  });

  // ---- Sidebar (mobile) --------------------------------------------------
  const sidebar = document.querySelector('.sidebar');
  const scrim = document.querySelector('.side-scrim');
  function toggleSidebar(open) {
    if (!sidebar) return;
    sidebar.classList.toggle('open', open);
    scrim && scrim.classList.toggle('open', open);
  }
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-menu-toggle]')) toggleSidebar(!sidebar.classList.contains('open'));
    else if (e.target.closest('.side-scrim')) toggleSidebar(false);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') toggleSidebar(false); });

  // ---- Topbar shadow on scroll ------------------------------------------
  const topbar = document.querySelector('.topbar');
  if (topbar) {
    const onScroll = () => topbar.classList.toggle('scrolled', window.scrollY > 4);
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  // ---- Toasts ------------------------------------------------------------
  document.querySelectorAll('.toast-x').forEach((t, i) => {
    const close = () => { t.classList.add('hide'); setTimeout(() => t.remove(), 320); };
    t.querySelector('button')?.addEventListener('click', close);
    setTimeout(close, 5000 + i * 400);
  });

  // ---- Count-up numbers --------------------------------------------------
  const fmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
  const countUps = document.querySelectorAll('[data-count]');
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  countUps.forEach((el) => {
    const target = parseFloat(el.dataset.count);
    if (!isFinite(target)) return;
    const decimals = parseInt(el.dataset.decimals || '0', 10);
    const f = decimals ? new Intl.NumberFormat('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : fmt;
    if (reduce) { el.textContent = f.format(target); return; }
    const dur = 1100, t0 = performance.now();
    const step = (t) => {
      const p = Math.min(1, (t - t0) / dur);
      const eased = 1 - Math.pow(1 - p, 4);
      el.textContent = f.format(target * eased);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });

  // ---- Meters animate in -------------------------------------------------
  requestAnimationFrame(() => {
    document.querySelectorAll('.meter > span[data-w]').forEach((s) => {
      s.style.width = Math.max(0, Math.min(100, parseFloat(s.dataset.w) || 0)) + '%';
    });
  });

  // ---- Client-side table filter -----------------------------------------
  document.querySelectorAll('[data-filter-target]').forEach((input) => {
    const rows = () => document.querySelectorAll(input.dataset.filterTarget);
    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      rows().forEach((r) => { r.style.display = !q || r.textContent.toLowerCase().includes(q) ? '' : 'none'; });
    });
  });

  // ---- Password visibility ----------------------------------------------
  document.addEventListener('click', (e) => {
    const b = e.target.closest('.pw-toggle');
    if (!b) return;
    const input = b.parentElement.querySelector('input');
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    b.innerHTML = show ? '<i class="bi bi-eye-slash"></i>' : '<i class="bi bi-eye"></i>';
  });

  // ---- Chart.js theme ----------------------------------------------------
  window.wilifeChartTheme = function () {
    if (!window.Chart) return;
    Chart.defaults.font.family = css('--font-sans') || 'sans-serif';
    Chart.defaults.font.weight = 600;
    Chart.defaults.color = css('--muted');
    Chart.defaults.borderColor = css('--line');
    Chart.defaults.plugins.tooltip.backgroundColor = css('--side-bg');
    Chart.defaults.plugins.tooltip.padding = 12;
    Chart.defaults.plugins.tooltip.cornerRadius = 10;
    Chart.defaults.plugins.tooltip.titleFont = { weight: 700 };
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.boxWidth = 8;
  };
  window.wilifeColor = css;
  window.wilifeChartTheme();
  document.addEventListener('wilife:theme', () => {
    window.wilifeChartTheme();
    if (window.Chart) Object.values(Chart.instances).forEach((c) => c.update());
  });
})();
