const PRESETS = [
  { k: 'today', label: 'hoje' },
  { k: '7d', label: '7d' },
  { k: '30d', label: '30d' },
  { k: '90d', label: '90d' },
  { k: '180d', label: '180d' },
  { k: '365d', label: '365d' },
  { k: 'mtd', label: 'MTD' },
  { k: 'ytd', label: 'YTD' },
  { k: 'all', label: 'tudo' },
  { k: 'custom', label: 'custom' },
];

function readParams() {
  const p = new URLSearchParams(location.search);
  return {
    preset: p.get('preset') || (p.get('from') || p.get('to') ? 'custom' : '90d'),
    from: p.get('from') || '',
    to: p.get('to') || '',
    accounts: (p.get('accounts') || '').split(',').filter(Boolean),
    megas: (p.get('megas') || '').split(',').filter(Boolean),
    categories: (p.get('categories') || '').split(',').filter(Boolean),
    account_types: (p.get('account_types') || '').split(',').filter(Boolean),
    currencies: (p.get('currencies') || '').split(',').filter(Boolean),
    q: p.get('q') || '',
    include_internal: p.get('internal') === '1',
    include_sweep: p.get('sweep') === '1',
    compare: p.get('compare') || 'none',
    display_currency: p.get('display_currency') || 'BRL',
  };
}

function buildQuery(f) {
  const p = new URLSearchParams();
  if (f.preset && f.preset !== '90d' && f.preset !== 'custom') p.set('preset', f.preset);
  if (f.preset === 'custom') {
    if (f.from) p.set('from', f.from);
    if (f.to) p.set('to', f.to);
  }
  if (f.accounts.length) p.set('accounts', f.accounts.join(','));
  if (f.megas.length) p.set('megas', f.megas.join(','));
  if (f.categories.length) p.set('categories', f.categories.join(','));
  if (f.account_types.length) p.set('account_types', f.account_types.join(','));
  if (f.currencies.length) p.set('currencies', f.currencies.join(','));
  if (f.q) p.set('q', f.q);
  if (f.include_internal) p.set('internal', '1');
  if (f.include_sweep) p.set('sweep', '1');
  if (f.compare && f.compare !== 'none') p.set('compare', f.compare);
  if (f.display_currency && f.display_currency !== 'BRL') p.set('display_currency', f.display_currency);
  if (f.sort) p.set('sort', f.sort);
  return p.toString();
}

window.ofinFilterQuery = function () { return buildQuery(readParams()); };

window.ofinNavigate = function (overrides) {
  const f = { ...readParams(), ...overrides };
  const qs = buildQuery(f);
  const url = location.pathname + (qs ? '?' + qs : '');
  location.href = url;
};

window.ofinDrillTo = function (path, overrides) {
  const f = { ...readParams(), ...overrides };
  const qs = buildQuery(f);
  location.href = path + (qs ? '?' + qs : '');
};

window.ofinMoney = function (v, ccy) {
  if (!window.OFIN_AUTHED) return '•••';
  if (v === null || v === undefined) return '—';
  const n = parseFloat(v);
  if (Number.isNaN(n)) return String(v);
  const c = ccy || 'BRL';
  try {
    return new Intl.NumberFormat(c === 'BRL' ? 'pt-BR' : 'en-US', {
      style: 'currency', currency: c, maximumFractionDigits: 2,
    }).format(n);
  } catch {
    return n.toFixed(2);
  }
};

function ofinShell() {
  return {
    filterOpen: false,
    filters: readParams(),
    presets: PRESETS,
    accounts: [],
    megas: [],
    drawerOpen: false,
    drawerLoading: false,
    drawerTx: null,
    drawerRule: null,
    drawerOverride: null,
    drawerForm: {
      mode: 'once',
      mega: '',
      category: '',
      is_internal: false,
      pattern_type: 'contains',
      pattern: '',
      priority: 50,
      note: '',
    },
    kbdBuffer: '',
    kbdTimer: null,
    savedViews: [],
    selectedView: '',

    init() {
      this.loadMeta();
      this.loadViews();
      document.addEventListener('click', (ev) => this.handleTxClick(ev));
      window.addEventListener('popstate', () => { this.filters = readParams(); });
    },

    loadViews() {
      try {
        this.savedViews = JSON.parse(localStorage.getItem('ofin:views') || '[]');
      } catch { this.savedViews = []; }
    },

    saveCurrentView() {
      const name = prompt('nome da visão:');
      if (!name) return;
      const v = { name, params: buildQuery(this.filters), path: location.pathname };
      const next = this.savedViews.filter((x) => x.name !== name);
      next.push(v);
      this.savedViews = next;
      localStorage.setItem('ofin:views', JSON.stringify(next));
      this.selectedView = name;
    },

    loadView(name) {
      const v = this.savedViews.find((x) => x.name === name);
      if (!v) return;
      const path = v.path || location.pathname;
      location.href = path + (v.params ? '?' + v.params : '');
    },

    deleteView(name) {
      if (!name || !confirm(`apagar visão "${name}"?`)) return;
      this.savedViews = this.savedViews.filter((x) => x.name !== name);
      localStorage.setItem('ofin:views', JSON.stringify(this.savedViews));
      this.selectedView = '';
    },

    moneyFmt(v, c) { return window.ofinMoney(v, c); },

    async loadMeta() {
      try {
        const [accRes, megaRes] = await Promise.all([
          fetch('/api/accounts'), fetch('/api/megas'),
        ]);
        if (accRes.ok) {
          const accs = await accRes.json();
          this.accounts = accs.map((a) => ({ id: a.id, label: (a.name || a.id).slice(0, 18) + (a.type ? ' · ' + a.type : '') }));
        }
        if (megaRes.ok) this.megas = await megaRes.json();
      } catch {}
    },

    setPreset(k) {
      this.filters.preset = k;
      if (k !== 'custom') { this.filters.from = ''; this.filters.to = ''; }
    },

    toggle(field, value) {
      const arr = this.filters[field];
      const i = arr.indexOf(value);
      if (i === -1) arr.push(value); else arr.splice(i, 1);
    },

    applyFilters() {
      const qs = buildQuery(this.filters);
      const url = location.pathname + (qs ? '?' + qs : '');
      location.href = url;
    },

    clearFilters() {
      this.filters = {
        preset: '90d', from: '', to: '',
        accounts: [], megas: [], categories: [], account_types: [], currencies: [],
        q: '', include_internal: false, include_sweep: false,
        compare: 'none', display_currency: 'BRL',
      };
      this.applyFilters();
    },

    filterSummary() {
      const f = this.filters;
      const parts = [];
      const presetLabel = (PRESETS.find((p) => p.k === f.preset) || { label: f.preset }).label;
      parts.push(presetLabel);
      if (f.accounts.length) parts.push(`${f.accounts.length} contas`);
      if (f.megas.length) parts.push(`${f.megas.length} megas`);
      if (f.compare !== 'none') parts.push(`vs ${f.compare === 'yoy' ? 'YoY' : 'prev'}`);
      if (f.q) parts.push(`"${f.q}"`);
      return 'filtros: ' + parts.join(' · ');
    },

    handleTxClick(ev) {
      // Row → drawer is wired via Alpine (@click $dispatch 'ofin-open-tx');
      // this document-level handler only covers legacy [data-drill] elements.
      const drill = ev.target.closest('[data-drill]');
      if (drill) {
        ev.preventDefault();
        const path = drill.getAttribute('data-drill') || '/transactions';
        let overrides = {};
        try { overrides = JSON.parse(drill.getAttribute('data-filter') || '{}'); } catch {}
        const merged = { ...readParams() };
        for (const k of Object.keys(overrides)) {
          if (Array.isArray(overrides[k])) merged[k] = overrides[k];
          else if (k === 'preset' && overrides[k] === 'custom') {
            merged.preset = 'custom';
            if (overrides.from) merged.from = overrides.from;
            if (overrides.to) merged.to = overrides.to;
          } else {
            merged[k] = overrides[k];
          }
        }
        const qs = buildQuery(merged);
        location.href = path + (qs ? '?' + qs : '');
      }
    },

    async openDrawer(txId) {
      this.drawerOpen = true;
      this.drawerLoading = true;
      this.drawerTx = null;
      this.drawerRule = null;
      this.drawerOverride = null;
      try {
        const res = await fetch(`/api/transactions/${txId}/explain`);
        if (!res.ok) throw new Error('explain failed');
        const data = await res.json();
        this.drawerTx = data.transaction;
        this.drawerRule = data.matched_rule;
        this.drawerOverride = data.override;
        this.drawerForm.mega = (data.override?.mega) || data.transaction.mega || '';
        this.drawerForm.category = (data.override?.category) || data.transaction.category || '';
        this.drawerForm.is_internal = !!(data.override?.is_internal ?? data.transaction.is_internal);
        this.drawerForm.mode = 'once';
        this.drawerForm.pattern = (data.transaction.description || '').toLowerCase();
        this.drawerForm.pattern_type = 'contains';
        this.drawerForm.priority = 50;
        this.drawerForm.note = data.override?.note || '';
      } catch (e) {
        alert('falha ao carregar transação');
        this.drawerOpen = false;
      } finally {
        this.drawerLoading = false;
      }
    },

    closeDrawer() { this.drawerOpen = false; },

    async clearOverride() {
      if (!this.drawerTx) return;
      const res = await fetch(`/api/transactions/${this.drawerTx.id}/categorize`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'clear' }),
      });
      if (res.ok) location.reload();
    },

    async submitDrawer() {
      if (!this.drawerTx) return;
      if (window.OFIN_READ_ONLY) { alert('somente leitura — instância pública. mutações desabilitadas.'); return; }
      const body = {
        mode: this.drawerForm.mode,
        mega: this.drawerForm.mega || null,
        category: this.drawerForm.category || null,
        is_internal: !!this.drawerForm.is_internal,
        note: this.drawerForm.note || null,
      };
      if (this.drawerForm.mode === 'rule') {
        body.pattern_type = this.drawerForm.pattern_type;
        body.pattern = this.drawerForm.pattern;
        body.priority = this.drawerForm.priority;
      }
      const res = await fetch(`/api/transactions/${this.drawerTx.id}/categorize`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) { alert('falhou'); return; }
      const data = await res.json();
      if (data.mode === 'rule') {
        alert(`regra criada · ${data.affected} transações reclassificadas`);
      }
      location.reload();
    },

    handleKey(ev) {
      if (ev.target.matches('input,textarea,select')) {
        if (ev.key === 'Escape') ev.target.blur();
        return;
      }
      if (ev.key === '/') {
        ev.preventDefault();
        this.filterOpen = true;
        this.$nextTick(() => this.$refs.searchInput?.focus());
        return;
      }
      if (ev.key === 'f') { this.filterOpen = !this.filterOpen; return; }
      if (ev.key === 'Escape') { this.drawerOpen = false; this.filterOpen = false; return; }
      if (ev.key === 'g') { this.startKbd('g'); return; }
      if (this.kbdBuffer === 'g') {
        const map = { d: '/', s: '/sankey', t: '/transactions', p: '/savings', r: '/rules' };
        if (map[ev.key]) {
          const qs = buildQuery(readParams());
          location.href = map[ev.key] + (qs ? '?' + qs : '');
        }
        this.kbdBuffer = '';
      }
    },

    startKbd(c) {
      this.kbdBuffer = c;
      clearTimeout(this.kbdTimer);
      this.kbdTimer = setTimeout(() => { this.kbdBuffer = ''; }, 1200);
    },
  };
}

window.ofinShell = ofinShell;

window.ofinBulkSelect = function () {
  return {
    selected: new Set(),
    bulkMega: '', bulkCategory: '', bulkInternal: false,
    toggle(id) {
      if (this.selected.has(id)) this.selected.delete(id); else this.selected.add(id);
      this.selected = new Set(this.selected);
    },
    toggleAll(ids) {
      if (this.selected.size === ids.length) this.selected = new Set();
      else this.selected = new Set(ids);
    },
    has(id) { return this.selected.has(id); },
    count() { return this.selected.size; },
    async apply() {
      if (!this.selected.size) return;
      if (window.OFIN_READ_ONLY) { alert('somente leitura — instância pública.'); return; }
      if (!this.bulkMega && !this.bulkCategory && !this.bulkInternal) {
        alert('escolha mega, categoria, ou interno'); return;
      }
      const res = await fetch('/api/transactions/bulk_categorize', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ids: [...this.selected],
          mega: this.bulkMega || null,
          category: this.bulkCategory || null,
          is_internal: this.bulkInternal,
        }),
      });
      if (res.ok) location.reload();
    },
  };
};
