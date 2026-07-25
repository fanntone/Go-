/* Go 底層原理筆記 — 前端互動：主題切換、行動版目錄、程式碼複製、本頁目錄高亮、全站搜尋 */
(() => {
  'use strict';

  /* ---------- 主題 ---------- */
  const root = document.documentElement;
  document.querySelector('.themetoggle')?.addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    localStorage.setItem('theme', next);
  });

  /* ---------- 行動版側邊欄 ---------- */
  const scrim = document.querySelector('.scrim');
  const navBtn = document.querySelector('.navtoggle');
  const setNav = (open) => {
    document.body.classList.toggle('nav-open', open);
    navBtn?.setAttribute('aria-expanded', String(open));
    if (scrim) scrim.hidden = !open;
  };
  navBtn?.addEventListener('click', () => setNav(!document.body.classList.contains('nav-open')));
  scrim?.addEventListener('click', () => setNav(false));

  /* ---------- 側邊欄捲動到目前章節 ---------- */
  const active = document.querySelector('.booknav li.active');
  if (active) {
    const side = document.querySelector('.sidebar');
    const top = active.offsetTop - side.clientHeight / 2;
    if (top > 0) side.scrollTop = top;
  }

  /* ---------- 程式碼區塊：語言標籤 + 複製 ---------- */
  const LANG = {
    go: 'Go', text: '輸出', bash: 'Shell', shell: 'Shell', console: 'Shell',
    asm: '組合語言', nasm: '組合語言', gas: '組合語言', diff: 'Diff',
    json: 'JSON', yaml: 'YAML', sql: 'SQL', c: 'C', ebnf: 'EBNF', html: 'HTML',
  };

  document.querySelectorAll('.prose .hl').forEach((block) => {
    const pre = block.querySelector('pre');
    if (!pre) return;

    const lang = block.dataset.lang || '';
    const label = LANG[lang] || (lang ? lang.toUpperCase() : 'Code');

    const wrap = document.createElement('div');
    wrap.className = 'codewrap';
    block.parentNode.insertBefore(wrap, block);

    const bar = document.createElement('div');
    bar.className = 'codebar';
    bar.innerHTML = `<span>${label}</span>`;

    const btn = document.createElement('button');
    btn.className = 'copybtn';
    btn.type = 'button';
    btn.textContent = '複製';
    btn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(pre.innerText);
        btn.textContent = '已複製 ✓';
        btn.classList.add('done');
      } catch {
        btn.textContent = '複製失敗';
      }
      setTimeout(() => { btn.textContent = '複製'; btn.classList.remove('done'); }, 1600);
    });

    bar.appendChild(btn);
    wrap.append(bar, block);
  });

  /* ---------- 本頁目錄高亮 ---------- */
  const tocLinks = [...document.querySelectorAll('.pagetoc a')];
  if (tocLinks.length) {
    const map = new Map();
    tocLinks.forEach((a) => {
      const el = document.getElementById(decodeURIComponent(a.hash.slice(1)));
      if (el) map.set(el, a);
    });
    const seen = new Set();
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((e) => e.isIntersecting ? seen.add(e.target) : seen.delete(e.target));
      const first = [...map.keys()].find((el) => seen.has(el));
      tocLinks.forEach((a) => a.classList.remove('current'));
      if (first) map.get(first).classList.add('current');
    }, { rootMargin: '-80px 0px -70% 0px' });
    map.forEach((_, el) => obs.observe(el));
  }

  /* ---------- 搜尋 ---------- */
  const dlg = document.getElementById('searchbox');
  const q = document.getElementById('q');
  const results = document.getElementById('results');
  let index = null;

  const openSearch = async () => {
    if (!dlg.open) dlg.showModal();
    q.focus();
    q.select();
    if (!index) {
      try {
        index = await (await fetch('search-index.json')).json();
      } catch {
        results.innerHTML = '<p style="padding:1.5rem;text-align:center">搜尋索引載入失敗，請用本機伺服器開啟（python build.py --serve）。</p>';
      }
    }
  };

  document.querySelector('.searchbtn')?.addEventListener('click', openSearch);
  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
    if ((e.key === '/' && !typing) || (e.key === 'k' && (e.metaKey || e.ctrlKey))) {
      e.preventDefault();
      openSearch();
    }
  });

  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  const snippet = (body, term) => {
    const i = body.toLowerCase().indexOf(term);
    if (i < 0) return esc(body.slice(0, 90)) + '…';
    const s = Math.max(0, i - 35);
    const raw = (s ? '…' : '') + body.slice(s, i + term.length + 70) + '…';
    return esc(raw).replace(new RegExp(esc(term).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), (m) => `<mark>${m}</mark>`);
  };

  const run = () => {
    const term = q.value.trim().toLowerCase();
    if (!index || term.length < 1) { results.innerHTML = ''; return; }

    const hits = index
      .map((p) => {
        const t = p.t.toLowerCase(), s = p.s.toLowerCase(), b = p.b.toLowerCase();
        const h = p.h.join(' ').toLowerCase();
        let score = 0;
        if (t.includes(term)) score += 100;
        if (h.includes(term)) score += 40;
        if (s.includes(term)) score += 25;
        const n = b.split(term).length - 1;
        score += Math.min(n, 12) * 3;
        return { p, score };
      })
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 12);

    results.innerHTML = hits.length
      ? hits.map(({ p }) => `<a class="sr" href="${p.u}"><b>${esc(p.t)}</b><small>${snippet(p.b, term)}</small></a>`).join('')
      : '<p style="padding:2rem;text-align:center;color:var(--fg-faint);font-size:.88rem">找不到符合的內容</p>';
  };

  q?.addEventListener('input', run);

  q?.addEventListener('keydown', (e) => {
    const items = [...results.querySelectorAll('.sr')];
    if (!items.length) return;
    const cur = items.findIndex((el) => el.classList.contains('sel'));
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const next = e.key === 'ArrowDown'
        ? (cur + 1) % items.length
        : (cur - 1 + items.length) % items.length;
      items.forEach((el) => el.classList.remove('sel'));
      items[next].classList.add('sel');
      items[next].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      (items[cur] || items[0]).click();
    }
  });
})();
