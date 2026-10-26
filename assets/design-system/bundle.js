/* @ds-bundle: {"format":4,"namespace":"Hive","components":[{"name":"Button"},{"name":"Field"},{"name":"Badge"},{"name":"ChannelChip"},{"name":"Cell"},{"name":"MetricCard"},{"name":"BotCard"},{"name":"DataTable"},{"name":"CodeSnippet"},{"name":"ConfirmDestructive"},{"name":"LoadingState"},{"name":"SlideOver"},{"name":"Stepper"},{"name":"GoLive"},{"name":"Thread"},{"name":"GuardrailScore"},{"name":"ApprovalBanner"},{"name":"Tabs"},{"name":"TabBar"},{"name":"EmptyState"}]} */
/* Hive — components/bundle.js. Script clásico: asigna window.Hive. Requiere window.React y window.ReactDOM (18). */
(function () {
  var React = window.React;
  if (!React) { console.warn('Hive: window.React no está cargado'); return; }
  var h = React.createElement, useState = React.useState, useEffect = React.useEffect;

  function cx() { var out = []; for (var i = 0; i < arguments.length; i++) { var a = arguments[i]; if (!a) continue; if (typeof a === 'string') out.push(a); else if (typeof a === 'object') for (var k in a) if (a[k]) out.push(k); } return out.join(' '); }
  var uid = 0; function nextId(p) { uid += 1; return (p || 'hv') + '-' + uid; }

  /* Iconos Lucide (ISC). Trazo 1.5, caja 24. */
  var PATHS = {
    'thumbs-up': 'M7 10v12M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z',
    'thumbs-down': 'M17 14V2M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z',
    'triangle-alert': 'm21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3M12 9v4M12 17h.01',
    'shield-alert': 'M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1zM12 8v4M12 16h.01',
    'home': 'M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM9 22V12h6v10',
    'message-circle': 'M7.9 20A9 9 0 1 0 4 16.1L2 22Z',
    'bot': 'M12 8V4H8M2 14h2M20 14h2M15 13v2M9 13v2M6 8h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z',
    'sliders': 'M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4',
    'users': 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
    'key': 'M15.5 7.5 19 4M21 2l-2 2M7.5 15.5a5 5 0 1 0 7-7 5 5 0 0 0-7 7zM21 2l-9.6 9.6',
    'plus': 'M5 12h14M12 5v14',
    'check': 'M20 6 9 17l-5-5',
    'x': 'M18 6 6 18M6 6l12 12',
    'alert-circle': 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 8v4M12 16h.01',
    'arrow-right': 'M5 12h14m-7-7 7 7-7 7',
    'copy': 'M8 8h12v12H8zM16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2',
    'lock': 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4',
    'trash': 'M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6',
    'activity': 'M22 12h-4l-3 9L9 3l-3 9H2'
  };
  function Icon(p) {
    return h('svg', { className: p.className, width: p.size || 20, height: p.size || 20, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': 'true', focusable: 'false' }, h('path', { d: PATHS[p.name] || '' }));
  }

  /* ---------- Button ---------- */
  function Button(p) {
    var variant = p.variant || 'primary', size = p.size || 'md';
    return h('button', { type: p.type || 'button', className: cx('hv hv-btn', 'hv-btn--' + variant, size !== 'md' && 'hv-btn--' + size, p.className), disabled: p.disabled, onClick: p.onClick, 'aria-label': p.ariaLabel },
      p.icon ? h(Icon, { name: p.icon, className: 'hv-btn__icon', size: size === 'sm' ? 16 : 20 }) : null, p.children);
  }

  /* ---------- Field ---------- */
  function Field(p) {
    var st = useState(function () { return p.id || nextId('hv-f'); }); var id = st[0];
    var Tag = p.multiline ? 'textarea' : 'input';
    var secretSaved = p.secret && p.saved;
    return h('div', { className: cx('hv hv-field', p.error && 'hv-field--error', (p.mono || p.secret) && 'hv-field--mono', p.secret && 'hv-field--secret', p.className) },
      p.label ? h('label', { className: 'hv-field__label', htmlFor: id }, p.secret ? h(Icon, { name: 'lock', size: 12 }) : null, p.label, p.required ? h('span', { className: 'hv-field__req', 'aria-hidden': 'true' }, '*') : null) : null,
      h('div', { className: 'hv-field__wrap' },
        h(Tag, { id: id, className: 'hv-field__control', type: p.multiline ? undefined : (p.secret ? 'password' : (p.type || 'text')), placeholder: secretSaved ? '••••••••••••••••' : p.placeholder, value: secretSaved ? '' : p.value, defaultValue: secretSaved ? undefined : p.defaultValue, onChange: p.onChange, disabled: p.disabled || secretSaved, required: p.required, autoComplete: p.secret ? 'off' : undefined, 'aria-invalid': p.error ? 'true' : undefined, 'aria-describedby': (p.help || p.error) ? id + '-d' : undefined, rows: p.multiline ? (p.rows || 4) : undefined }),
        secretSaved ? h('span', { className: 'hv-field__secret' }, h(Icon, { name: 'check', size: 12 }), 'Guardado en Andvari') : null),
      p.error ? h('div', { className: 'hv-field__error', id: id + '-d' }, h(Icon, { name: 'alert-circle', size: 14 }), p.error)
        : (p.help ? h('div', { className: 'hv-field__help', id: id + '-d' }, p.help) : null));
  }

  /* ---------- Badge ---------- */
  var STATES = { REG: { tone: 'info', label: 'Registrado' }, ACT: { tone: 'success', label: 'Activo' }, ERR: { tone: 'error', label: 'Error' }, REV: { tone: 'muted', label: 'Revocado' } };
  function Badge(p) {
    var s = p.state ? STATES[p.state] : null;
    var tone = p.tone || (s ? s.tone : 'neutral');
    return h('span', { className: cx('hv hv-badge', tone !== 'neutral' && 'hv-badge--' + tone, p.className) },
      p.dot !== false ? h('span', { className: 'hv-badge__dot' }) : null,
      s ? h('span', { className: 'hv-badge__code' }, p.state) : null,
      s ? (p.short ? null : h('span', null, s.label)) : p.children);
  }

  /* ---------- ChannelChip ---------- */
  var CHANNELS = { messenger: { mark: 'M', label: 'Messenger', brand: 'meta' }, instagram: { mark: 'I', label: 'Instagram', brand: 'meta' }, whatsapp: { mark: 'W', label: 'WhatsApp', brand: 'whatsapp' } };
  function ChannelChip(p) {
    var c = CHANNELS[p.channel] || { mark: '?', label: p.channel, brand: '' };
    return h('span', { className: cx('hv hv-chip', c.brand && !p.disconnected && 'hv-chip--' + c.brand, p.size === 'lg' && 'hv-chip--lg', p.disconnected && 'hv-chip--off', p.className), title: p.disconnected ? c.label + ' · sin conectar' : c.label },
      h('span', { className: 'hv-chip__mark', 'aria-hidden': 'true' }, c.mark), p.label === false ? null : c.label, p.children);
  }

  /* ---------- Cell ---------- */
  function Cell(p) {
    var style = p.size ? { '--hv-cell': typeof p.size === 'number' ? p.size + 'px' : p.size } : undefined;
    var initials = (p.name || '').split(/\s+/).map(function (w) { return w[0]; }).join('').slice(0, 2);
    return h('span', { className: cx('hv hv-cell', p.tone && 'hv-cell--' + p.tone, p.className), style: style, role: 'img', 'aria-label': p.name || '' }, p.src ? h('img', { src: p.src, alt: '' }) : initials);
  }

  /* ---------- MetricCard ---------- */
  function MetricCard(p) {
    var dir = p.delta == null ? null : (p.delta > 0 ? 'up' : (p.delta < 0 ? 'down' : 'flat'));
    if (dir && p.invert) dir = dir === 'up' ? 'down' : (dir === 'down' ? 'up' : 'flat');
    return h('div', { className: cx('hv hv-metric', p.dense && 'hv-metric--dense', p.className) },
      h('div', { className: 'hv-metric__label' }, p.label),
      h('div', { className: 'hv-metric__value' }, p.value, p.unit ? h('span', { className: 'hv-metric__unit' }, p.unit) : null),
      (p.delta != null || p.hint) ? h('div', { className: 'hv-metric__foot' },
        p.delta != null ? h('span', { className: 'hv-metric__delta hv-metric__delta--' + dir }, (p.delta > 0 ? '+' : '') + p.delta + (p.deltaUnit || '%')) : null,
        p.hint ? h('span', null, p.hint) : null) : null);
  }

  /* ---------- BotCard ---------- */
  function BotCard(p) {
    var tone = p.state === 'ERR' ? 'error' : (p.state === 'REG' ? 'soft' : undefined);
    return h('div', { className: cx('hv hv-bot', p.selected && 'hv-bot--selected', p.className), role: 'button', tabIndex: 0, onClick: p.onClick, onKeyDown: function (e) { if ((e.key === 'Enter' || e.key === ' ') && p.onClick) { e.preventDefault(); p.onClick(); } } },
      h(Cell, { name: p.name, tone: tone, size: 36 }),
      h('div', { className: 'hv-bot__main' },
        h('div', { className: 'hv-bot__name' }, p.name, h(Badge, { state: p.state })),
        h('div', { className: 'hv-bot__meta' },
          p.env ? h(Badge, { tone: 'muted', dot: false }, p.env) : null,
          (p.channels || []).map(function (c) { return h(ChannelChip, { key: c, channel: c, label: false }); }),
          p.botId ? h('span', { className: 'hv-key' }, p.botId) : null)),
      h('div', { className: 'hv-bot__side' },
        h('span', null, 'Último sync'), h('span', { className: 'hv-key', style: { color: p.state === 'ERR' ? 'var(--error-text)' : 'var(--ink)' } }, p.lastSync || '—')));
  }

  /* ---------- DataTable ---------- */
  function DataTable(p) {
    var cols = p.columns || [], rows = p.rows || [];
    return h('div', { className: cx('hv hv-table-wrap', p.className) },
      h('table', { className: 'hv-table' },
        h('thead', null, h('tr', null, cols.map(function (c) { return h('th', { key: c.key, scope: 'col', className: cx(c.align === 'right' && 'is-num', c.key === 'actions' && 'is-actions'), style: c.width ? { width: c.width } : undefined }, c.label); }))),
        h('tbody', null, rows.length ? rows.map(function (r, i) {
          return h('tr', { key: r.id || i, className: cx(p.selectedId != null && r.id === p.selectedId && 'is-selected', r.error && 'is-error'), onClick: p.onRowClick ? function () { p.onRowClick(r); } : undefined },
            cols.map(function (c) { var v = c.render ? c.render(r) : r[c.key]; return h('td', { key: c.key, className: cx(c.mono && 'is-mono', c.align === 'right' && 'is-num', c.key === 'actions' && 'is-actions') }, v); }));
        }) : h('tr', null, h('td', { colSpan: cols.length, className: 'hv-table__empty' }, p.emptyText || 'Sin registros')))));
  }

  /* ---------- CodeSnippet ---------- */
  function highlight(src, lang) {
    var esc = src.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    var out = esc.replace(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g, '<span class="tk-s">$1</span>');
    if (lang === 'bash') { out = out.replace(/(^|\n)(\$ |#[^\n]*)/g, function (m, a, b) { return a + (b[0] === '#' ? '<span class="tk-c">' + b + '</span>' : '<span class="tk-a">' + b + '</span>'); }).replace(/\b(curl|-X|-H|-d|--data)\b/g, '<span class="tk-k">$1</span>'); }
    if (lang === 'python') { out = out.replace(/(^|\n)(#[^\n]*)/g, '$1<span class="tk-c">$2</span>').replace(/\b(import|from|def|return|print|as|if|else|with)\b/g, '<span class="tk-k">$1</span>'); }
    if (lang === 'json') { out = out.replace(/(<span class="tk-s">"[^"]*"<\/span>)(\s*:)/g, '<span class="tk-k">$1</span>$2'); }
    return out;
  }
  function CodeSnippet(p) {
    var variants = p.variants || [{ lang: p.lang || 'bash', label: p.lang || 'bash', code: p.code || '' }];
    var st = useState(0), idx = st[0], setIdx = st[1];
    var cp = useState(false), copied = cp[0], setCopied = cp[1];
    var v = variants[idx] || variants[0];
    function copy() {
      var done = function () { setCopied(true); setTimeout(function () { setCopied(false); }, 1600); if (p.onCopy) p.onCopy(v); };
      try { navigator.clipboard.writeText(v.code).then(done, function () { var r = document.createRange(); r.selectNodeContents(preRef.current); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); done(); }); } catch (e) { done(); }
    }
    var preRef = React.useRef(null);
    return h('div', { className: cx('hv hv-code', p.className) },
      h('div', { className: 'hv-code__bar', role: variants.length > 1 ? 'tablist' : undefined },
        p.title ? h('span', { className: 'hv-code__title' }, p.title) : null,
        variants.length > 1 ? variants.map(function (x, i) { return h('button', { key: x.lang, type: 'button', role: 'tab', className: 'hv-code__tab', 'aria-selected': i === idx ? 'true' : 'false', onClick: function () { setIdx(i); } }, x.label || x.lang); }) : null,
        h(Button, { variant: 'ghost', size: 'sm', icon: copied ? 'check' : 'copy', className: 'hv-code__copy', onClick: copy, ariaLabel: 'Copiar' }, copied ? 'Copiado' : 'Copiar')),
      h('pre', { ref: preRef }, h('code', { dangerouslySetInnerHTML: { __html: highlight(v.code, v.lang) } })));
  }

  /* ---------- ConfirmDestructive ---------- */
  function ConfirmDestructive(p) {
    var st = useState(''), typed = st[0], setTyped = st[1];
    var ok = typed.trim() === p.expected;
    var box = h('div', { className: 'hv hv-confirm', role: 'alertdialog', 'aria-modal': p.inline ? undefined : 'true', 'aria-labelledby': 'hv-cd-t' },
      h('div', { className: 'hv-confirm__head' }, h(Icon, { name: 'trash', size: 24 }), h('div', { className: 'hv-confirm__title', id: 'hv-cd-t' }, p.title)),
      h('div', { className: 'hv-confirm__text' }, p.children, ' Escribe ', h('code', null, p.expected), ' para confirmar.'),
      p.facts ? h('div', { className: 'hv-confirm__facts' }, p.facts.map(function (f) { return h('span', { key: f.label }, f.label + ' ', h('b', null, f.value)); })) : null,
      h(Field, { label: p.fieldLabel || 'Nombre o UUID del recurso', mono: true, value: typed, onChange: function (e) { setTyped(e.target.value); }, placeholder: p.expected, error: typed && !ok && typed.length >= p.expected.length ? 'No coincide con el recurso.' : undefined }),
      h('div', { className: 'hv-confirm__actions' },
        h(Button, { variant: 'ghost', onClick: p.onCancel }, 'Cancelar'),
        h(Button, { variant: 'danger', icon: 'trash', disabled: !ok, onClick: function () { if (p.onConfirm) p.onConfirm(); } }, p.confirmLabel || 'Borrar definitivamente')));
    return h('div', { className: cx('hv-scrim', p.inline && 'hv-scrim--inline') }, box);
  }

  /* ---------- LoadingState ---------- */
  function LoadingState(p) {
    var kind = p.kind || 'spinner', n = p.rows || 3;
    var msg = p.message ? h('div', { className: 'hv-loading__msg', role: 'status', 'aria-live': 'polite' }, kind === 'spinner' ? h('span', { className: 'hv-spin', 'aria-hidden': 'true' }) : null, h('span', null, p.message), p.detail ? h('code', null, p.detail) : null) : null;
    var rows = [];
    if (kind === 'rows' || kind === 'cards') for (var i = 0; i < n; i++) rows.push(h('div', { key: i, className: cx('hv-skel', kind === 'rows' ? 'hv-skel--row' : 'hv-skel--card'), 'aria-hidden': 'true' }));
    return h('div', { className: cx('hv hv-loading', p.className) }, msg, rows);
  }

  /* ---------- SlideOver ---------- */
  function SlideOver(p) {
    useEffect(function () { if (p.inline) return; function k(e) { if (e.key === 'Escape' && p.onClose) p.onClose(); } document.addEventListener('keydown', k); return function () { document.removeEventListener('keydown', k); }; }, [p.onClose, p.inline]);
    return h('aside', { className: cx('hv hv-slide', p.inline && 'hv-slide--inline', p.className), role: 'dialog', 'aria-modal': p.inline ? undefined : 'true', 'aria-label': p.title },
      h('div', { className: 'hv-slide__head' },
        h('div', null, h('h2', { className: 'hv-slide__title' }, p.title), p.subtitle ? h('div', { className: 'hv-slide__sub' }, p.subtitle) : null),
        h(Button, { variant: 'ghost', size: 'sm', icon: 'x', className: 'hv-slide__close', ariaLabel: 'Cerrar', onClick: p.onClose })),
      h('div', { className: 'hv-slide__body' }, p.children),
      p.footer ? h('div', { className: 'hv-slide__foot' }, p.footer) : null);
  }

  /* ---------- Stepper ---------- */
  var WIZARD = ['Identidad', 'Motor de reglas', 'Cifrado y credenciales', 'Go-Live'];
  function Stepper(p) {
    var steps = p.steps || WIZARD, cur = p.current || 0;
    return h('ol', { className: cx('hv hv-stepper', p.className), 'aria-label': 'Pasos' },
      steps.map(function (s, i) {
        var state = i < cur ? 'done' : (i === cur ? 'current' : 'todo');
        return h('li', { key: s, className: cx('hv-step', 'hv-step--' + state), 'aria-current': state === 'current' ? 'step' : undefined },
          h('span', { className: 'hv-step__n' }, (state === 'done' ? '✓ ' : '') + 'PASO ' + (i + 1)), h('span', { className: 'hv-step__t' }, s));
      }));
  }

  /* ---------- GoLive ---------- */
  function GoLive(p) {
    var live = !!p.live;
    return h('div', { className: cx('hv hv-golive', live && 'hv-golive--live', p.className) },
      h('div', { className: 'hv-golive__status', role: 'status', 'aria-live': 'polite' },
        h('span', { className: 'hv-golive__pulse', 'aria-hidden': 'true' }),
        live ? h('span', null, 'Meta verificó el webhook. El bot está ', h('b', null, 'ACT'), '.') : h('span', null, 'Escuchando el primer ', h('code', { className: 'hv-key' }, 'GET /chat/' + (p.botId || '{bot_id}')), ' desde Meta…')),
      h('div', { className: 'hv-golive__kv' },
        h('span', null, 'URL del webhook'), h('code', null, p.webhookUrl || ('https://hive-webhook.higerotech.com/chat/' + (p.botId || '{bot_id}'))),
        h('span', null, 'verifyToken'), h('code', null, p.verifyToken || '—'),
        p.lastSeen ? h('span', null, 'Última llamada') : null, p.lastSeen ? h('code', null, p.lastSeen) : null),
      p.children);
  }

  /* ---------- Thread ---------- */
  var ROLE_LABEL = { customer: 'Contacto', bot: 'Bot', operator: 'Operador' };
  function Thread(p) {
    var ratings = useState({}), rated = ratings[0], setRated = ratings[1];
    var msgs = p.messages || [];
    return h('div', { className: cx('hv hv-thread', p.className), role: 'log', 'aria-label': p.ariaLabel || 'Conversación' },
      msgs.map(function (m, i) {
        var r = rated[m.id] || m.rating;
        return h('div', { key: m.id || i, className: cx('hv-msg', 'hv-msg--' + m.role, m.flagged && 'hv-msg--flagged') },
          h('div', { className: 'hv-msg__bubble' }, m.text),
          h('div', { className: 'hv-msg__meta' },
            h('span', null, m.author || ROLE_LABEL[m.role]),
            m.time ? h('time', null, m.time) : null,
            m.eventId ? h('span', { className: 'hv-msg__id' }, m.eventId) : null,
            m.flagged ? h('span', { style: { color: 'var(--warn)' } }, 'fuera de guardrail') : null,
            (m.role === 'bot' && p.ratable !== false) ? h('span', { className: 'hv-msg__rate' },
              h('button', { type: 'button', className: 'is-up', 'aria-label': 'Respuesta correcta', 'aria-pressed': r === 'up' ? 'true' : 'false', onClick: function () { var n = Object.assign({}, rated); n[m.id] = 'up'; setRated(n); if (p.onRate) p.onRate(m, 'up'); } }, h(Icon, { name: 'thumbs-up', size: 16 })),
              h('button', { type: 'button', className: 'is-down', 'aria-label': 'Respuesta incorrecta', 'aria-pressed': r === 'down' ? 'true' : 'false', onClick: function () { var n = Object.assign({}, rated); n[m.id] = 'down'; setRated(n); if (p.onRate) p.onRate(m, 'down'); } }, h(Icon, { name: 'thumbs-down', size: 16 }))) : null));
      }));
  }

  /* ---------- GuardrailScore ---------- */
  function GuardrailScore(p) {
    var thr = p.threshold == null ? 0.7 : p.threshold, dims = p.dimensions || [];
    return h('div', { className: cx('hv hv-guard', p.className) },
      h('div', { className: 'hv-guard__head' }, h('div', { className: 'hv-guard__title' }, p.title || 'Evaluación de guardrails'), h('div', { className: 'hv-guard__thr' }, 'umbral ' + thr.toFixed(2))),
      dims.map(function (d) {
        var pass = d.score >= thr;
        return h('div', { key: d.name, className: cx('hv-guard__row', pass ? 'hv-guard__row--pass' : 'hv-guard__row--fail') },
          h('div', { className: 'hv-guard__name' }, d.name), h('div', { className: 'hv-guard__val' }, d.score.toFixed(2)),
          h('div', { className: 'hv-guard__bar', role: 'meter', 'aria-valuemin': 0, 'aria-valuemax': 1, 'aria-valuenow': d.score, 'aria-label': d.name },
            h('div', { className: 'hv-guard__fill', style: { width: Math.round(d.score * 100) + '%' } }),
            h('div', { className: 'hv-guard__mark', style: { left: 'calc(' + Math.round(thr * 100) + '% - 1px)' }, 'aria-hidden': 'true' })));
      }));
  }

  /* ---------- ApprovalBanner ---------- */
  function ApprovalBanner(p) {
    var st = useState(''), reason = st[0], setReason = st[1];
    var danger = p.level === 'danger';
    return h('div', { className: cx('hv hv-approve', danger && 'hv-approve--danger', p.className), role: 'region', 'aria-label': 'Aprobación requerida' },
      h(Icon, { name: danger ? 'shield-alert' : 'triangle-alert', className: 'hv-approve__icon', size: 24 }),
      h('div', { className: 'hv-approve__body' },
        h('div', { className: 'hv-approve__title' }, p.title || 'Esta acción requiere aprobación'),
        p.children ? h('div', { className: 'hv-approve__text' }, p.children) : null,
        p.facts ? h('div', { className: 'hv-approve__facts' }, p.facts.map(function (f) { return h('span', { key: f.label }, f.label + ' ', h('b', null, f.value)); })) : null,
        h(Field, { label: 'Motivo', required: true, placeholder: 'Ticket, incidente o razón operativa', value: reason, onChange: function (e) { setReason(e.target.value); } }),
        h('div', { className: 'hv-approve__actions' },
          h(Button, { variant: danger ? 'danger' : 'primary', disabled: !reason.trim(), onClick: function () { if (p.onApprove) p.onApprove(reason); }, icon: 'check' }, p.approveLabel || 'Aprobar y ejecutar'),
          h(Button, { variant: 'ghost', onClick: p.onReject, icon: 'x' }, 'Cancelar'),
          p.requestedBy ? h('span', { className: 'hv-approve__by' }, 'Solicitado por ' + p.requestedBy) : null)));
  }

  /* ---------- Tabs ---------- */
  function Tabs(p) {
    var st = useState(p.defaultValue || (p.items && p.items[0] && p.items[0].value));
    var value = p.value != null ? p.value : st[0];
    return h('div', { className: cx('hv hv-tabs', p.compact && 'hv-tabs--compact', p.className), role: 'tablist' },
      (p.items || []).map(function (it) {
        return h('button', { key: it.value, type: 'button', role: 'tab', className: 'hv-tab', 'aria-selected': value === it.value ? 'true' : 'false', onClick: function () { st[1](it.value); if (p.onChange) p.onChange(it.value); } }, it.label, it.count != null ? h('span', { className: 'hv-tab__count' }, it.count) : null);
      }));
  }

  /* ---------- TabBar ---------- */
  var TABBAR_DEFAULT = [
    { value: 'overview', label: 'Visión general', icon: 'activity' },
    { value: 'bots', label: 'Bots', icon: 'bot' },
    { value: 'contacts', label: 'Contactos', icon: 'users' },
    { value: 'keys', label: 'Credenciales', icon: 'key' }
  ];
  function TabBar(p) {
    var items = p.items || TABBAR_DEFAULT;
    var st = useState(p.defaultValue || items[0].value);
    var value = p.value != null ? p.value : st[0];
    return h('nav', { className: cx('hv hv-tabbar', p.className), 'aria-label': 'Navegación principal' },
      items.map(function (it) {
        return h('button', { key: it.value, type: 'button', className: 'hv-tabbar__item', 'aria-current': value === it.value ? 'page' : undefined, onClick: function () { st[1](it.value); if (p.onChange) p.onChange(it.value); } },
          h('span', { className: 'hv-tabbar__icon' }, h(Icon, { name: it.icon, size: 22 }), it.count ? h('span', { className: 'hv-tabbar__count' }, it.count) : null), h('span', null, it.label));
      }));
  }

  /* ---------- EmptyState ---------- */
  function EmptyState(p) {
    return h('div', { className: cx('hv hv-empty', p.className) },
      h('div', { className: 'hv-empty__cells', 'aria-hidden': 'true' }, h(Cell, { size: 20, tone: 'soft' }), h(Cell, { size: 20 }), h(Cell, { size: 20, tone: 'ash' })),
      h('div', { className: 'hv-empty__title' }, p.title),
      p.children ? h('div', { className: 'hv-empty__text' }, p.children) : null,
      (p.actionLabel || p.secondaryLabel) ? h('div', { className: 'hv-empty__actions' },
        p.actionLabel ? h(Button, { variant: 'primary', icon: 'plus', onClick: p.onAction }, p.actionLabel) : null,
        p.secondaryLabel ? h(Button, { variant: 'ghost', onClick: p.onSecondary }, p.secondaryLabel) : null) : null);
  }

  window.Hive = { Icon: Icon, Button: Button, Field: Field, Badge: Badge, ChannelChip: ChannelChip, Cell: Cell, MetricCard: MetricCard, BotCard: BotCard, DataTable: DataTable, CodeSnippet: CodeSnippet, ConfirmDestructive: ConfirmDestructive, LoadingState: LoadingState, SlideOver: SlideOver, Stepper: Stepper, GoLive: GoLive, Thread: Thread, GuardrailScore: GuardrailScore, ApprovalBanner: ApprovalBanner, Tabs: Tabs, TabBar: TabBar, EmptyState: EmptyState };
})();
