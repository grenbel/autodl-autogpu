// Skeleton extractor for the AutoDL console's instance list: a development tool, read only.
// This file is one function expression. Paste it as code, take the function's text with Function.prototype.toString,
// compare its SHA-256 and byte count with the values computed from this file, and only then call it as
// fn('<instance ID>'). It returns text describing the structure around that instance's row, never the page's values.
// Output is built only from: standard tag names (other tags become tag@N), reviewed Element UI classes (other classes
// become class@N), id and aria references renumbered as @N, a few attributes with fixed value sets, fixed UI words, and
// fixed sentences whose digits are masked as '#'. Everything else becomes <text>. Other instances' rows are never
// output, only counted. Full scans stop at a budget of elements read, so a huge page cannot stall the console.
// It reads the DOM only: no writes, no events, no network, no storage, no navigation.
// The second argument exists only for the offline decoy test (tests/console/skeleton_test.js) on https://example.com.
(function (targetId, mode) {
  'use strict';
  var ID_RE = /^[a-z0-9]{10}-[a-z0-9]{8}$/;
  var ID_ALL = /(?<![a-z0-9])[a-z0-9]{10}-[a-z0-9]{8}(?![a-z0-9])/g;
  var TEST_ID = 'abcd123456-1234abcd';
  if (typeof targetId !== 'string' || !ID_RE.test(targetId)) throw new Error('not an instance ID');
  if (mode === 'offline-decoy-test') {
    if (location.origin !== 'https://example.com' || targetId !== TEST_ID) {
      throw new Error('the test mode runs only on https://example.com with the fake ID');
    }
  } else if (mode !== undefined) {
    throw new Error('unknown mode');
  } else if (location.origin !== 'https://www.autodl.com' || location.pathname !== '/console/instance/list') {
    throw new Error('not the instance list');
  }
  var pw = document.querySelectorAll('input[type=password]');
  for (var p = 0; p < pw.length; p++) {
    if (pw[p].getClientRects().length) throw new Error('a password field is showing (a login page?)');
  }

  var FIXED = [
    '实例ID/名称', '状态', '规格详情', '本地磁盘', '健康状态', '付费方式', '释放时间/停机时间', 'SSH登录', '快捷工具', '操作',
    '创建中', '开机中', '运行中', '关机中', '重置中', '已关机', '无卡模式', 'GPU充足', 'GPU紧张', 'GPU不足',
    '开机', '关机', '更多', '设置定时关机', '修改', '关闭', '查看详情', 'JupyterLab', 'AutoPanel', '实例监控', '自定义服务',
    '登录指令', '密码', '复制', '无卡模式开机', '更换镜像', '保存镜像', '升降配置', '扩容数据盘', '缩容数据盘', '转包年包月',
    '克隆实例', '跨实例拷贝数据', '修改SSH密码', '重置系统', '释放实例', '主机名称', '可租用至', '数据盘可扩容', 'GPU空闲/总量',
    'GPU驱动', 'CUDA版本', '按量计费', '包日', '包周', '包月', '包年', '提示', '确定', '取消', '确定重置', '选择日期', '选择时间',
    '此刻', '设置成功', '预计消费', '确认开机吗？', '确认无卡模式开机吗？', '取消定时关机', '正常', '异常', '实例列表', '容器实例',
    '当前服务器时间', '定时关机时间', '账户余额', '账户代金券', '镜像', 'GPU', 'CPU', '内存', '硬盘', '附加磁盘', '端口映射', '网络',
    '计费方式', '费用', '单价'
  ];
  var TEMPLATES = [
    '#天#小时#分后释放', '关机#天后释放', '#机', '￥#.#/时', '￥#.#', '#.#', '#', '#/#', '#-#-# #:#:#', '#-#-# #:#', '#:#',
    '#-#-#', '#%', '#.#%',
    '确认开机吗？ 实例将按￥#.#/时整点扣费', '实例将按￥#.#/时整点扣费',
    '确认无卡模式开机吗？ 无卡模式配置为:#.#核心CPU，#GB内存，无GPU卡 配置费用:￥#.#/时，最低消费￥#.#',
    '无卡模式配置为:#.#核心CPU，#GB内存，无GPU卡', '配置费用:￥#.#/时，最低消费￥#.#',
    '按量计费实例关机后可使用无卡模式开机。无卡模式下CPU: #.#核，内存: #GB，GPU: #卡，计费: ￥#.#/时'
  ];
  var ROLES = ['button', 'dialog', 'alertdialog', 'menu', 'menuitem', 'tooltip', 'alert', 'listbox', 'option', 'tab',
               'tablist', 'tabpanel', 'row', 'cell', 'gridcell', 'columnheader', 'rowheader', 'grid', 'table', 'rowgroup',
               'presentation', 'none', 'group', 'radiogroup', 'radio', 'checkbox', 'switch', 'textbox', 'combobox', 'img',
               'link', 'navigation', 'separator', 'status', 'region', 'list', 'listitem', 'heading', 'document'];
  var TYPES = ['button', 'submit', 'reset', 'text', 'password', 'checkbox', 'radio', 'number', 'search', 'tel', 'email',
               'url', 'date', 'time', 'datetime-local', 'month', 'week', 'hidden', 'file', 'range', 'color'];
  var BOOLS = ['true', 'false'];
  var VALUE_SETS = { role: ROLES, type: TYPES, 'aria-hidden': BOOLS, 'aria-expanded': BOOLS, 'aria-disabled': BOOLS,
                     'aria-modal': BOOLS };
  // Element UI classes kept as they are, each one reviewed; any other class becomes class@N. Table column classes
  // (el-table_N_column_M, digits only) are the one pattern kept.
  var CLASSES = [
    'el-table', 'el-table--fit', 'el-table--striped', 'el-table--border', 'el-table--small', 'el-table--mini',
    'el-table--medium', 'el-table--enable-row-hover', 'el-table--enable-row-transition', 'el-table--scrollable-x',
    'el-table--scrollable-y', 'el-table--fluid-height', 'el-table--group', 'el-table__header-wrapper', 'el-table__header',
    'el-table__body-wrapper', 'el-table__body', 'el-table__footer-wrapper', 'el-table__footer', 'el-table__row',
    'el-table__row--striped', 'el-table__row--level-0', 'el-table__cell', 'el-table__expanded-cell', 'el-table__empty-block',
    'el-table__empty-text', 'el-table__fixed', 'el-table__fixed-right', 'el-table__fixed-header-wrapper',
    'el-table__fixed-body-wrapper', 'el-table__fixed-footer-wrapper', 'el-table__fixed-right-patch',
    'el-table__column-resize-proxy', 'el-table__append-wrapper', 'el-table-column--selection', 'el-table__expand-column',
    'el-table__expand-icon', 'el-table__placeholder', 'el-table__indent', 'gutter', 'cell', 'is-hidden', 'is-leaf', 'is-left',
    'is-center', 'is-right', 'is-sortable', 'is-group', 'hover-row', 'current-row', 'is-scrolling-none', 'is-scrolling-left',
    'is-scrolling-right', 'is-scrolling-middle', 'el-tooltip',
    'el-button', 'el-button--default', 'el-button--primary', 'el-button--success', 'el-button--warning', 'el-button--danger',
    'el-button--info', 'el-button--text', 'el-button--medium', 'el-button--small', 'el-button--mini', 'el-button-group',
    'is-plain', 'is-round', 'is-circle', 'is-disabled', 'is-loading', 'is-active',
    'el-dropdown', 'el-dropdown-link', 'el-dropdown-selfdefine', 'el-dropdown__caret-button', 'el-dropdown__icon',
    'el-dropdown-menu', 'el-dropdown-menu--medium', 'el-dropdown-menu--small', 'el-dropdown-menu--mini',
    'el-dropdown-menu__item', 'el-dropdown-menu__item--divided', 'el-popper', 'popper__arrow',
    'el-tooltip__popper', 'is-dark', 'is-light', 'el-popover', 'el-popover--plain', 'el-popover__title',
    'el-popover__reference', 'el-popover__reference-wrapper',
    'el-message-box__wrapper', 'el-message-box', 'el-message-box--center', 'el-message-box__header', 'el-message-box__title',
    'el-message-box__headerbtn', 'el-message-box__close', 'el-message-box__content', 'el-message-box__container',
    'el-message-box__status', 'el-message-box__message', 'el-message-box__input', 'el-message-box__errormsg',
    'el-message-box__btns', 'el-message-box__btns-reverse',
    'el-dialog__wrapper', 'el-dialog', 'el-dialog--center', 'el-dialog__header', 'el-dialog__title', 'el-dialog__headerbtn',
    'el-dialog__close', 'el-dialog__body', 'el-dialog__footer', 'dialog-footer', 'v-modal',
    'el-date-editor', 'el-date-editor--datetime', 'el-date-editor--date', 'el-picker-panel', 'el-date-picker', 'has-time',
    'has-sidebar', 'el-picker-panel__body-wrapper', 'el-picker-panel__body', 'el-picker-panel__content',
    'el-picker-panel__footer', 'el-picker-panel__link-btn', 'el-picker-panel__icon-btn', 'el-picker-panel__sidebar',
    'el-picker-panel__shortcut', 'el-date-picker__time-header', 'el-date-picker__editor-wrap', 'el-date-picker__header',
    'el-date-picker__header--bordered', 'el-date-picker__header-label', 'el-date-picker__prev-btn',
    'el-date-picker__next-btn', 'el-date-table', 'el-date-table__row', 'available', 'today', 'current', 'prev-month',
    'next-month', 'el-time-panel', 'el-time-panel__content', 'el-time-panel__footer', 'el-time-panel__btn', 'cancel',
    'confirm', 'el-time-spinner', 'el-time-spinner__wrapper', 'el-time-spinner__list', 'el-time-spinner__item',
    'el-scrollbar', 'el-scrollbar__wrap', 'el-scrollbar__view', 'el-scrollbar__bar', 'el-scrollbar__thumb', 'is-horizontal',
    'is-vertical', 'has-seconds',
    'el-input', 'el-input--small', 'el-input--mini', 'el-input--medium', 'el-input--prefix', 'el-input--suffix',
    'el-input__inner', 'el-input__prefix', 'el-input__suffix', 'el-input__suffix-inner', 'el-input__icon', 'el-input__clear',
    'el-input-group', 'el-input-group__append', 'el-input-group__prepend', 'el-input__count',
    'el-form', 'el-form-item', 'el-form-item__label', 'el-form-item__content', 'el-form-item__error', 'el-form--label-left',
    'el-form--label-right', 'el-form--label-top', 'el-form--inline', 'is-required', 'is-error', 'is-success',
    'el-form-item--small', 'el-form-item--mini', 'el-form-item--medium',
    'el-icon-more', 'el-icon-arrow-down', 'el-icon-arrow-up', 'el-icon-arrow-left', 'el-icon-arrow-right',
    'el-icon-d-arrow-left', 'el-icon-d-arrow-right', 'el-icon-copy-document', 'el-icon-time', 'el-icon-date',
    'el-icon-close', 'el-icon-circle-close', 'el-icon-warning', 'el-icon-warning-outline', 'el-icon-info', 'el-icon-success',
    'el-icon-error', 'el-icon-question', 'el-icon-loading', 'el-icon--right', 'el-icon--left', 'el-icon-caret-bottom',
    'el-icon-caret-top', 'el-icon-search', 'el-icon-refresh', 'el-icon-setting',
    'el-message', 'el-message--success', 'el-message--warning', 'el-message--info', 'el-message--error', 'el-message__icon',
    'el-message__content', 'el-message__closeBtn', 'el-notification', 'el-notification__group', 'el-notification__title',
    'el-notification__content', 'el-notification__closeBtn', 'right', 'left',
    'el-tag', 'el-tag--success', 'el-tag--warning', 'el-tag--danger', 'el-tag--info', 'el-tag--light', 'el-tag--dark',
    'el-tag--plain', 'el-tag--small', 'el-tag--mini', 'el-tag--medium', 'el-loading-mask', 'el-loading-spinner',
    'el-loading-text', 'circular', 'path', 'el-badge', 'el-badge__content',
    'el-zoom-in-top-enter', 'el-zoom-in-top-enter-active', 'el-zoom-in-top-enter-to', 'el-zoom-in-top-leave',
    'el-zoom-in-top-leave-active', 'el-zoom-in-top-leave-to', 'dialog-fade-enter-active', 'dialog-fade-leave-active',
    'msgbox-fade-enter-active', 'msgbox-fade-leave-active', 'el-fade-in-linear-enter-active', 'el-fade-in-linear-leave-active',
    'v-modal-enter', 'v-modal-leave', 'el-message-fade-enter', 'el-message-fade-leave-active', 'el-fade-in-enter-active',
    'el-fade-in-leave-active'
  ];
  // Standard HTML and SVG tags kept as they are; any other tag (a custom element) becomes tag@N.
  var TAGS = [
    'html', 'head', 'body', 'div', 'span', 'p', 'a', 'b', 'i', 'em', 'strong', 'small', 'sub', 'sup', 'br', 'hr', 'pre', 'code',
    'label', 'button', 'input', 'textarea', 'select', 'option', 'optgroup', 'form', 'fieldset', 'legend', 'table', 'caption',
    'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'colgroup', 'col', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'img', 'picture',
    'figure', 'figcaption', 'iframe', 'script', 'style', 'noscript', 'template', 'canvas', 'object', 'embed', 'video', 'audio',
    'source', 'track', 'svg', 'g', 'path', 'use', 'circle', 'rect', 'line', 'polyline', 'polygon', 'ellipse', 'defs', 'symbol',
    'clippath', 'lineargradient', 'stop', 'mask', 'text', 'tspan', 'title', 'desc', 'header', 'footer', 'main', 'nav',
    'section', 'article', 'aside', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'details', 'summary', 'dialog', 'slot', 'area', 'map',
    'meta', 'link', 'center', 'font'
  ];
  var SKIP = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1, IFRAME: 1, CANVAS: 1, OBJECT: 1, EMBED: 1 };
  var POPPER = ['el-dropdown-menu', 'el-tooltip__popper', 'el-popover', 'el-popper', 'el-picker-panel'];
  var GLOBAL = ['el-message-box__wrapper', 'el-dialog__wrapper', 'el-picker-panel', 'v-modal', 'el-message', 'el-notification'];
  var OVERLAY = '.' + POPPER.concat(GLOBAL).join(', .');
  var LEVELS = 8;
  var MAX_VISITS = 3000;
  var SCAN_BUDGET = 40000;

  // Elements read in full scans (the whole page, idsIn). Running out stops the extractor (the try block before main).
  var scanned = 0;
  function spend(n) {
    scanned += n;
    if (scanned > SCAN_BUDGET) throw new RangeError('autodl-skeleton: scan budget');
  }
  var refs = new Map();
  var classes = new Map();
  var tags = new Map();
  function ref(v) {
    if (!refs.has(v)) refs.set(v, '@' + (refs.size + 1));
    return refs.get(v);
  }
  function cls(c) {
    if (CLASSES.indexOf(c) >= 0 || /^el-table_\d{1,4}_column_\d{1,4}$/.test(c)) return '.' + c;
    if (!classes.has(c)) classes.set(c, 'class@' + (classes.size + 1));
    return '.' + classes.get(c);
  }
  function tagOf(el) {
    var t = el.tagName.toLowerCase();
    if (TAGS.indexOf(t) >= 0) return t;
    if (!tags.has(t)) tags.set(t, 'tag@' + (tags.size + 1));
    return tags.get(t);
  }
  function classTokens(el) {
    var raw = el.getAttribute('class');
    return raw ? raw.split(/\s+/).filter(Boolean).map(cls) : [];
  }
  function norm(s) { return String(s).replace(/\s+/g, ' ').trim(); }
  function word(text) {
    var t = norm(text);
    if (!t) return '';
    if (FIXED.indexOf(t) >= 0) return '"' + t + '"';
    if (ID_RE.test(t)) return t === targetId ? '<target-id>' : '<instance-id>';
    var masked = t.replace(/\d+/g, '#');
    if (TEMPLATES.indexOf(masked) >= 0) return '~"' + masked + '"';
    return '<text>';
  }
  function ownText(el) {
    var s = '';
    for (var i = 0; i < el.childNodes.length; i++) {
      if (el.childNodes[i].nodeType === 3) s += ' ' + el.childNodes[i].nodeValue;
    }
    return norm(s);
  }
  // Every instance-ID-shaped string in the own texts of el and its descendants.
  function idsIn(el) {
    var found = [];
    var list = el.getElementsByTagName('*');
    spend(list.length + 1);
    var nodes = [el].concat(Array.prototype.slice.call(list));
    nodes.forEach(function (n) { found = found.concat(ownText(n).match(ID_ALL) || []); });
    return found;
  }

  var lines = [];
  var visits = 0;
  var truncated = false;
  function emit(s) { lines.push(s); }
  function pad(depth) { return new Array(depth + 1).join('  '); }
  function describe(el) {
    var parts = [tagOf(el) + classTokens(el).join('')];
    if (el.id) parts.push('#' + ref(el.id));
    ['aria-controls', 'aria-describedby', 'aria-labelledby', 'aria-owns'].forEach(function (a) {
      var v = el.getAttribute(a);
      if (v && norm(v)) parts.push('[' + a + '=' + norm(v).split(' ').map(function (x) { return ref(x); }).join(' ') + ']');
    });
    Object.keys(VALUE_SETS).forEach(function (a) {
      var v = el.getAttribute(a);
      if (v !== null) parts.push('[' + a + '=' + (VALUE_SETS[a].indexOf(norm(v).toLowerCase()) >= 0 ? norm(v).toLowerCase() : '<v>') + ']');
    });
    ['disabled', 'readonly', 'hidden', 'inert'].forEach(function (a) {
      if (el.hasAttribute(a)) parts.push('[' + a + ']');
    });
    var ph = el.getAttribute('placeholder');
    if (ph !== null) parts.push('[placeholder=' + (word(ph) || '""') + ']');
    var cs = window.getComputedStyle(el);
    if (cs.display === 'none') parts.push('{none}');
    if (cs.visibility === 'hidden') parts.push('{hidden}');
    if (cs.opacity === '0') parts.push('{opacity0}');
    if (cs.pointerEvents === 'none') parts.push('{nopointer}');
    for (var i = 0; i < el.childNodes.length; i++) {
      if (el.childNodes[i].nodeType === 3) {
        var w = word(el.childNodes[i].nodeValue);
        if (w) parts.push(w);
      }
    }
    return parts.join(' ');
  }
  // limit: how many levels below the start to output (undefined: no level limit). Every call stops at MAX_VISITS.
  function walk(el, depth, limit, level) {
    level = level || 0;
    if (truncated) return;
    if (visits >= MAX_VISITS) { truncated = true; return; }
    visits++;
    if (SKIP[el.tagName]) { emit(pad(depth) + tagOf(el) + ' {skipped}'); return; }
    if (limit !== undefined && level > limit) { emit(pad(depth) + '{more elements}'); return; }
    emit(pad(depth) + describe(el));
    if (el.tagName.toLowerCase() === 'svg') return;
    for (var i = 0; i < el.children.length; i++) walk(el.children[i], depth + 1, limit, level + 1);
  }
  function chain(el) {
    var names = [];
    for (var e = el; e && e !== document.documentElement; e = e.parentElement) {
      names.unshift(tagOf(e) + classTokens(e).join(''));
    }
    return names.join(' > ');
  }
  function fixedSection(el) {
    if (el.closest('.el-table__fixed-right')) return 'right';
    if (el.closest('.el-table__fixed')) return 'left';
    return 'main';
  }
  function trSiblings(tr) {
    return Array.prototype.filter.call(tr.parentElement.children, function (c) { return c.tagName === 'TR'; });
  }
  function finish() {
    if (truncated) lines.push('== truncated: visit limit reached ==');
    return lines.join('\n');
  }

  try {
    return main();
  } catch (e) {
    if (!(e instanceof RangeError) || e.message !== 'autodl-skeleton: scan budget') throw e;
    emit('stopped: the scan budget ran out');
    return finish();
  }

  function main() {
    // The target row: exactly one row of the main table holds the id, and it holds no other instance id.
    var all = document.body ? document.body.getElementsByTagName('*') : [];
    if (mode) emit('== TEST MODE (offline decoy test) ==');
    emit('== skeleton v3 ==');
    if (all.length > SCAN_BUDGET) { emit('stopped: the page is too large'); return finish(); }
    spend(all.length);
    var exact = [];
    var partial = [];
    for (var i = 0; i < all.length; i++) {
      var t = ownText(all[i]);
      if (t === targetId) exact.push(all[i]);
      else if (t.indexOf(targetId) >= 0) partial.push(all[i]);
    }
    var outside = exact.filter(function (h) { return !h.closest('tr'); });
    var mainRows = [];
    exact.forEach(function (h) {
      var tr = h.closest('tr');
      if (tr && fixedSection(tr) === 'main' && mainRows.indexOf(tr) < 0) mainRows.push(tr);
    });

    emit('== summary ==');
    emit('elements whose own text is the id: ' + exact.length + ' (in rows ' + (exact.length - outside.length) +
         ', outside rows ' + outside.length + ')');
    emit('elements whose own text contains the id: ' + partial.length);
    emit('main rows holding the id: ' + mainRows.length);
    outside.forEach(function (h) { emit('outside a row: ' + chain(h)); });
    partial.forEach(function (h) { emit('contains the id: ' + chain(h)); });
    var stop = null;
    if (mainRows.length === 0) stop = 'the id is in no row';
    else if (mainRows.length > 1) stop = 'the id is in more than one row';
    else if (idsIn(mainRows[0]).some(function (x) { return x !== targetId; })) stop = 'a candidate row holds another instance id';
    if (stop) { emit('stopped: ' + stop); return finish(); }

    var mainRow = mainRows[0];
    var root = mainRow.closest('.el-table');
    emit('== table chain ==');
    emit('chain: ' + chain(mainRow));
    var header = root && root.querySelector('.el-table__header-wrapper');
    if (header) { emit('== header =='); walk(header, 0); }
    emit('== row main ==');
    walk(mainRow, 0);

    // Fixed-column copies: the row at the same position in each fixed body. Row counts must agree and a copy may not
    // hold a different instance id; otherwise the copy is reported as a mismatch and not output.
    var index = trSiblings(mainRow).indexOf(mainRow);
    var count = trSiblings(mainRow).length;
    var copies = { left: null, right: null };
    var notes = { left: '0', right: '0' };
    ['left', 'right'].forEach(function (side) {
      var bodies = root ? root.querySelectorAll((side === 'left' ? '.el-table__fixed' : '.el-table__fixed-right') + ' tbody') : [];
      if (!bodies.length) return;
      var trs = bodies.length === 1 ? Array.prototype.filter.call(bodies[0].children, function (c) { return c.tagName === 'TR'; }) : [];
      if (trs.length !== count || idsIn(trs[index]).some(function (x) { return x !== targetId; })) { notes[side] = 'mismatch'; return; }
      copies[side] = trs[index];
      notes[side] = '1';
    });
    emit('fixed copies: left ' + notes.left + ', right ' + notes.right);
    ['left', 'right'].forEach(function (side) {
      if (copies[side]) { emit('== row fixed-' + side + ' =='); walk(copies[side], 0); }
    });
    var rows = [mainRow].concat([copies.left, copies.right].filter(Boolean));
    function inRows(el) { return rows.some(function (r) { return r.contains(el); }); }

    // What the target rows point to (the "更多" menu, tooltips). Only poppers that hold no rows and no other id.
    var referenced = [];
    rows.forEach(function (r) {
      [r].concat(Array.prototype.slice.call(r.querySelectorAll('[aria-controls],[aria-describedby],[aria-owns]')))
        .forEach(function (n) {
          ['aria-controls', 'aria-describedby', 'aria-owns'].forEach(function (a) {
            var v = n.getAttribute(a);
            if (v) norm(v).split(' ').forEach(function (id) { if (id && referenced.indexOf(id) < 0) referenced.push(id); });
          });
        });
    });
    var handled = [];
    emit('== referenced ==');
    referenced.forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) { emit(ref(id) + ': missing'); return; }
      if (inRows(el)) { emit(ref(id) + ': inside the row'); return; }
      var popper = POPPER.some(function (c) { return el.classList.contains(c); });
      if (!popper || el === document.body || el === document.documentElement || el.contains(mainRow)) {
        emit(ref(id) + ': skipped (not an overlay)');
        return;
      }
      handled.push(el);
      if (el.querySelector('tr, table') || idsIn(el).some(function (x) { return x !== targetId; })) {
        emit(ref(id) + ': skipped (contains rows or ids)');
        return;
      }
      emit(ref(id) + ':');
      walk(el, 1, LEVELS);
    });

    // Page-level overlays (dialogs, message boxes, pickers) that hold no rows and no ids. The rest is only counted.
    var inOtherRows = 0;
    var unreferenced = 0;
    var skipped = 0;
    emit('== overlays ==');
    document.querySelectorAll(OVERLAY).forEach(function (ov) {
      if (inRows(ov) || handled.some(function (s) { return s === ov || s.contains(ov); })) return;
      if (ov.closest('tr')) { inOtherRows++; return; }
      if (!GLOBAL.some(function (c) { return ov.classList.contains(c); })) { unreferenced++; return; }
      handled.push(ov);
      if (ov.contains(mainRow) || ov.querySelector('tr, table') || idsIn(ov).length) { skipped++; return; }
      walk(ov, 0, LEVELS);
    });
    emit('overlays in other rows: ' + inOtherRows + ', other poppers not referenced by the row: ' + unreferenced +
         ', skipped page-level overlays: ' + skipped);
    return finish();
  }
})
