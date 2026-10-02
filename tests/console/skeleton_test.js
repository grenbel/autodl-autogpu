// Offline test for tests/console/skeleton.js, run on https://example.com (a static page without scripts).
// It builds fake console pages with decoy secrets wherever the extractor must not read, runs the extractor with
// network, storage, navigation, dynamic code, events and DOM writes trapped and DOM mutations watched, and checks
// every output line against a closed grammar. Use it in three calls:
//   1. paste this file;
//   2. __skel.trapsOn(); var fn = <the text of skeleton.js, pasted as code>;
//   3. await __skel.run(fn)
// run() returns one JSON string: {pass, digest: {bytes, sha256}, csp, total, failed: [{name, ok, detail}]}. The digest
// is of fn.toString(); compare it with: python tests/console/fn_digest.py tests/console/skeleton.js
// For mutation checks, __skel.runSource(sourceText) evaluates a (mutated) source with the traps already on.
// __skel.sample(fn) returns the main page's output, to read it line by line.
window.__skel = (function () {
  'use strict';
  var realEval = window.eval;
  var TARGET = 'abcd123456-1234abcd';
  var OTHER = 'wxyz987654-9876wxyz';
  var THIRD = 'mnop135790-1357mnop';
  var MODE = 'offline-decoy-test';
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
  var SETS = { role: ROLES, type: TYPES, 'aria-hidden': BOOLS, 'aria-expanded': BOOLS, 'aria-disabled': BOOLS, 'aria-modal': BOOLS };
  // Copies of the extractor's reviewed lists, for the closed grammar.
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
  // ('decoy.example', not 'decoy': the test-mode header line itself says "offline decoy test".)
  var DECOYS = ['DECOY', 'decoy.example', OTHER, THIRD, TARGET, '40022', '123.45', '0.98', '0.10', '0.03', '2026-09-29', '2026-10-01',
                '11:33', '04:30', '329', 'user-alice', 'alice', 'Alice', 'private-name', 'secretname', 'gpu-ok', 'region',
                'instance-id', 'shadow-host', 'dropdown-menu-', 'el-tooltip-', 'el-popover-', 'el-private-alice',
                'is-project-secret', 'has-account-name', 'user-alice-tag'];

  var checks = [];
  function check(name, ok, detail) { checks.push({ name: name, ok: !!ok, detail: ok ? undefined : String(detail) }); }

  // Traps: each entry the extractor must not touch throws and is recorded in hits.
  var hits = [];
  var undo = null;
  function trapsOn() {
    if (undo) return;
    hits = [];
    undo = [];
    function deny(label) { return function () { hits.push(label); throw new Error('forbidden: ' + label); }; }
    function fn(obj, name, label) {
      try {
        if (!obj || !(name in obj)) return;
        var own = Object.getOwnPropertyDescriptor(obj, name);
        Object.defineProperty(obj, name, { configurable: true, writable: true, value: deny(label || name) });
        undo.push(function () { if (own) Object.defineProperty(obj, name, own); else delete obj[name]; });
      } catch (e) { hits.push('could not trap ' + (label || name)); }
    }
    function accessor(obj, name, label, keepGetter) {
      try {
        if (!obj) return;
        var own = Object.getOwnPropertyDescriptor(obj, name);
        var get = keepGetter && own && own.get ? own.get : deny(label || name);
        Object.defineProperty(obj, name, { configurable: true, get: get, set: deny(label || name) });
        undo.push(function () { if (own) Object.defineProperty(obj, name, own); else delete obj[name]; });
      } catch (e) { hits.push('could not trap ' + (label || name)); }
    }
    ['fetch', 'open', 'postMessage', 'WebSocket', 'EventSource', 'Worker', 'SharedWorker', 'Image', 'Audio', 'Option',
     'eval', 'Function', 'scrollTo', 'scrollBy', 'scroll', 'alert', 'confirm', 'prompt', 'print']
      .forEach(function (n) { fn(window, n, 'window.' + n); });
    fn(XMLHttpRequest.prototype, 'open', 'XMLHttpRequest');
    fn(Navigator.prototype, 'sendBeacon');
    ['pushState', 'replaceState', 'back', 'forward', 'go'].forEach(function (n) { fn(History.prototype, n, 'history.' + n); });
    ['getItem', 'setItem', 'removeItem', 'clear', 'key'].forEach(function (n) { fn(Storage.prototype, n, 'storage.' + n); });
    ['createElement', 'createElementNS', 'write', 'writeln', 'open', 'execCommand']
      .forEach(function (n) { fn(Document.prototype, n, 'document.' + n); });
    ['setAttribute', 'setAttributeNS', 'removeAttribute', 'toggleAttribute', 'insertAdjacentHTML', 'insertAdjacentElement',
     'insertAdjacentText', 'attachShadow', 'remove', 'append', 'prepend', 'replaceWith', 'after', 'before', 'scrollIntoView',
     'scrollTo', 'scrollBy', 'scroll', 'requestFullscreen', 'requestPointerLock']
      .forEach(function (n) { fn(Element.prototype, n, 'element.' + n); });
    ['appendChild', 'insertBefore', 'removeChild', 'replaceChild'].forEach(function (n) { fn(Node.prototype, n, 'node.' + n); });
    ['click', 'focus', 'blur'].forEach(function (n) { fn(HTMLElement.prototype, n, 'element.' + n); });
    ['dispatchEvent', 'addEventListener', 'removeEventListener'].forEach(function (n) { fn(EventTarget.prototype, n, n); });
    ['submit', 'requestSubmit', 'reset'].forEach(function (n) { fn(HTMLFormElement.prototype, n, 'form.' + n); });
    accessor(document, 'cookie', 'cookie');
    ['localStorage', 'sessionStorage', 'indexedDB'].forEach(function (n) { accessor(window, n, n); });
    accessor(Element.prototype, 'innerHTML', 'innerHTML');
    accessor(Element.prototype, 'outerHTML', 'outerHTML');
    accessor(Node.prototype, 'textContent', 'textContent set', true);
    accessor(Node.prototype, 'nodeValue', 'nodeValue set', true);
    accessor(HTMLElement.prototype, 'innerText', 'innerText');
    accessor(HTMLElement.prototype, 'dataset', 'dataset');
    accessor(HTMLElement.prototype, 'title', 'title');
    [[HTMLImageElement, ['src', 'srcset']], [HTMLScriptElement, ['src']], [HTMLLinkElement, ['href']],
     [HTMLAnchorElement, ['href', 'ping']], [HTMLAreaElement, ['href', 'ping']], [HTMLIFrameElement, ['src', 'srcdoc']],
     [HTMLMediaElement, ['src']], [HTMLSourceElement, ['src', 'srcset']], [HTMLFormElement, ['action']],
     [HTMLInputElement, ['formAction', 'value']], [HTMLButtonElement, ['formAction']], [HTMLObjectElement, ['data']],
     [HTMLEmbedElement, ['src']], [HTMLTextAreaElement, ['value']], [HTMLSelectElement, ['value']]]
      .forEach(function (pair) {
        pair[1].forEach(function (n) { accessor(pair[0].prototype, n, pair[0].name + '.' + n); });
      });
    var timers = [['setTimeout', window.setTimeout], ['setInterval', window.setInterval]];
    timers.forEach(function (t) {
      window[t[0]] = function (f) {
        if (typeof f === 'string') { hits.push(t[0] + '(string)'); throw new Error('forbidden: ' + t[0] + '(string)'); }
        return t[1].apply(window, arguments);
      };
      undo.push(function () { window[t[0]] = t[1]; });
    });
  }
  function trapsOff() {
    if (!undo) return [];
    undo.reverse().forEach(function (u) { u(); });
    undo = null;
    return hits.slice();
  }

  // A CSP that blocks network, frames, media and forms, if the page accepts one added by script. Probe it with a
  // data: image, which the policy should block without any network.
  function installCsp() {
    return new Promise(function (resolve) {
      var meta = document.createElement('meta');
      meta.httpEquiv = 'Content-Security-Policy';
      meta.content = "default-src 'none'; img-src 'none'; media-src 'none'; frame-src 'none'; connect-src 'none'; " +
                     "form-action 'none'; font-src 'none'; object-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; " +
                     "style-src 'unsafe-inline'";
      document.head.appendChild(meta);
      var done = false;
      function finish(active) { if (!done) { done = true; resolve(active); } }
      document.addEventListener('securitypolicyviolation', function () { finish(true); }, { once: true });
      var img = document.createElement('img');
      img.src = 'data:,csp-probe';
      setTimeout(function () { finish(false); }, 1500);
    });
  }

  function sha256(text) {
    var data = new TextEncoder().encode(text);
    return crypto.subtle.digest('SHA-256', data).then(function (buf) {
      var hex = Array.prototype.map.call(new Uint8Array(buf), function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
      return { bytes: data.length, sha256: hex };
    });
  }

  // Page builders. Element UI-like structure; every value is fake.
  function header() {
    return '<div class="el-table__header-wrapper"><table class="el-table__header"><thead><tr>' +
      ['实例ID/名称', '状态', '释放时间/停机时间', '操作'].map(function (w, i) {
        return '<th class="el-table_1_column_' + (i + 1) + '"><div class="cell">' + w + '</div></th>';
      }).join('') + '</tr></thead></table></div>';
  }
  function row(id, extra) {
    extra = extra || {};
    return '<tr class="el-table__row">' +
      '<td><div class="cell"><div class="region user-alice el-private-alice is-project-secret has-account-name" ' +
      'data-secret="DECOY-DELTA" title="DECOY-EPSILON">DECOY-KAPPA 329机</div>' +
      '<div class="instance-id">' + id + '<i class="el-icon-copy-document"></i></div>' + (extra.name || '') + '<!-- DECOY-ETA -->' +
      '<user-alice-tag>DECOY-CT</user-alice-tag>' +
      '<input class="el-input__inner" value="DECOY-ALPHA" placeholder="DECOY-XI" aria-label="DECOY-OMICRON" ' +
      'role="private-name" aria-expanded="Alice" type="secretname">' +
      '<a href="https://decoy.example/DECOY-BETA">查看详情</a><img src="data:,DECOY-GAMMA" alt="DECOY-ZETA">' +
      '<iframe srcdoc="DECOY-RHO"></iframe><script>var DECOY_THETA = 1;</script><style>.DECOY-IOTA{}</style>' +
      '<template><span>DECOY-TPL</span></template><span class="shadow-host"></span>' + (extra.inner || '') + '</div></td>' +
      '<td><div class="cell"><span>已关机</span> <span class="gpu-ok">GPU充足</span> <span>40022</span> <span>￥123.45</span></div></td>' +
      '<td><div class="cell"><span>2026-09-29 11:33:00</span><button class="el-button el-button--text"><span>修改</span></button>' +
      '<button class="el-button el-button--text"><span>关闭</span></button><span>14天23小时58分后释放</span></div></td>' +
      '<td><div class="cell"><button class="el-button"><span>开机</span></button><div class="el-dropdown">' +
      '<button class="el-button" aria-controls="' + (extra.controls || 'dropdown-menu-x') + '"' +
      (extra.describedby ? ' aria-describedby="' + extra.describedby + '"' : '') +
      (extra.owns ? ' aria-owns="' + extra.owns + '"' : '') + '><span>更多</span></button></div></div></td></tr>';
  }
  function opsCopy(controls) {
    return '<tr class="el-table__row"><td class="is-hidden"></td><td class="is-hidden"></td><td class="is-hidden"></td>' +
      '<td><div class="cell"><button class="el-button"><span>开机</span></button><div class="el-dropdown">' +
      '<button class="el-button" aria-controls="' + controls + '"><span>更多</span></button></div></div></td></tr>';
  }
  function menu(id, items) {
    return '<ul class="el-dropdown-menu el-popper" id="' + id + '" style="display:none">' + items.map(function (t) {
      return '<li class="el-dropdown-menu__item">' + t + '</li>';
    }).join('') + '</ul>';
  }
  function table(rowsHtml, fixedRows) {
    return '<div class="el-table el-table--fit">' + header() +
      '<div class="el-table__body-wrapper"><table class="el-table__body"><tbody>' + rowsHtml.join('') + '</tbody></table></div>' +
      (fixedRows ? '<div class="el-table__fixed-right"><div class="el-table__fixed-body-wrapper"><table class="el-table__body">' +
        '<tbody>' + fixedRows.join('') + '</tbody></table></div></div>' : '') + '</div>';
  }
  function deep(n, inner) { return n ? '<div>' + deep(n - 1, inner) + '</div>' : inner; }
  var OVERLAYS =
    menu('dropdown-menu-1', ['无卡模式开机', 'DECOY-SIGMA', '释放实例']) + menu('dropdown-menu-11', ['无卡模式开机', '释放实例']) +
    menu('dropdown-menu-2', ['DECOY-MU']) + menu('dropdown-menu-3', ['DECOY-MU3']) +
    menu('dropdown-menu-12', ['DECOY-MU12']) + menu('dropdown-menu-13', ['DECOY-MU13']) +
    '<div class="el-tooltip__popper" id="el-tooltip-7" style="display:none">DECOY-PI ' + TARGET + '</div>' +
    '<div class="el-tooltip__popper" id="el-tooltip-8" style="display:none">DECOY-NU</div>' +
    '<div class="el-popover el-popper" id="el-popover-9" style="display:none">' + deep(12, '<span>DECOY-DEEP</span>') + '</div>' +
    '<div class="el-popover el-popper" id="el-popover-10" style="display:none"><span>' + THIRD + '</span></div>' +
    '<div class="el-message-box__wrapper" style="display:none"><div class="el-message-box"><div class="el-message-box__message">' +
    '<p><span>确认开机吗？</span><span>实例将按￥0.98/时整点扣费</span></p></div><div class="el-message-box__btns">' +
    '<button class="el-button"><span>取消</span></button><button class="el-button el-button--primary"><span>确定</span></button>' +
    '</div></div></div>' +
    '<div class="el-message-box__wrapper" style="display:none"><div class="el-message-box"><div class="el-message-box__message">' +
    '<p><span>确认无卡模式开机吗？</span><span>无卡模式配置为:0.5核心CPU，2GB内存，无GPU卡</span>' +
    '<span>配置费用:￥0.10/时，最低消费￥0.01</span></p></div></div></div>' +
    '<div class="el-dialog__wrapper" style="display:none"><div class="el-dialog"><div class="el-dialog__body">' +
    '<div><span>当前服务器时间</span><span>2026-10-01 04:30:00</span></div>' +
    '<div><span>定时关机时间</span><div class="el-date-editor"><input class="el-input__inner" value="DECOY-UPSILON" ' +
    'placeholder="DECOY-PH"></div></div><div><span>预计消费</span><span>￥0.03</span></div>' +
    '<div><span>账户余额</span><span>￥123.45</span></div><div><span>账户代金券</span><span>￥0.00</span></div></div>' +
    '<div class="el-dialog__footer"><button class="el-button"><span>取消</span></button>' +
    '<button class="el-button el-button--primary"><span>确定</span></button></div></div></div>' +
    '<div class="el-dialog__wrapper" style="display:none"><div class="el-dialog">' + table([row(OTHER)], null) + '</div></div>' +
    '<div class="el-picker-panel el-date-picker el-popper has-time" style="display:none"><div class="el-date-picker__time-header">' +
    '<input class="el-input__inner" placeholder="选择日期"><input class="el-input__inner" placeholder="选择时间"></div>' +
    '<div class="el-picker-panel__footer"><button class="el-button el-picker-panel__link-btn el-button--text"><span>此刻</span>' +
    '</button><button class="el-button el-picker-panel__link-btn"><span>确定</span></button></div></div>';
  var TARGET_ROW = { controls: 'dropdown-menu-1 el-popover-10', describedby: 'el-tooltip-7 el-popover-9', owns: 'app' };
  function page(rowsHtml, fixedRows, extra) {
    return '<div id="app"><div class="instance-list">' + table(rowsHtml, fixedRows) +
      '<span class="copy-hidden" style="display:none">' + TARGET + '</span>' +
      '<input type="password" style="display:none" value="DECOY-PW">' + (extra || '') + '</div></div>' + OVERLAYS;
  }
  var THREE = [row(OTHER, { controls: 'dropdown-menu-2' }), row(TARGET, TARGET_ROW), row(THIRD, { controls: 'dropdown-menu-3' })];
  var COPIES = [opsCopy('dropdown-menu-12'), opsCopy('dropdown-menu-11'), opsCopy('dropdown-menu-13')];

  // The closed grammar of the extractor's output.
  var WORD = '(?:"[^"]+"|~"[^"]+"|<text>|<target-id>|<instance-id>)';
  function alt(list) { return list.map(function (s) { return s.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&'); }).join('|'); }
  var CLS = '(?:\\.(?:' + alt(CLASSES) + '|el-table_\\d{1,4}_column_\\d{1,4}|class@\\d+))';
  var TAG = '(?:' + alt(TAGS) + '|tag@\\d+)';
  var ELEMENT = new RegExp('^(?:  )*' + TAG + CLS + '*(?: #@\\d+)?' +
    '(?: \\[aria-(?:controls|describedby|labelledby|owns)=@\\d+(?: @\\d+)*\\])*' +
    '(?: \\[(?:role|type|aria-hidden|aria-expanded|aria-disabled|aria-modal)=(?:[a-z-]+|<v>)\\])*' +
    '(?: \\[(?:disabled|readonly|hidden|inert)\\])*(?: \\[placeholder=(?:' + WORD + '|"")\\])?' +
    '(?: \\{(?:none|hidden|opacity0|nopointer)\\})*(?: ' + WORD + ')*$');
  var CHAIN = new RegExp('^(?:outside a row|contains the id|chain): ' + TAG + CLS + '*(?: > ' + TAG + CLS + '*)*$');
  var LINES = [
    /^== TEST MODE \(offline decoy test\) ==$/, /^== skeleton v3 ==$/,
    /^== (?:summary|table chain|header|row main|row fixed-left|row fixed-right|referenced|overlays) ==$/,
    /^elements whose own text is the id: \d+ \(in rows \d+, outside rows \d+\)$/,
    /^elements whose own text contains the id: \d+$/, /^main rows holding the id: \d+$/,
    /^stopped: (?:the id is in no row|the id is in more than one row|a candidate row holds another instance id|the page is too large|the scan budget ran out)$/,
    /^fixed copies: left (?:0|1|mismatch), right (?:0|1|mismatch)$/,
    /^@\d+:(?: missing| inside the row| skipped \(not an overlay\)| skipped \(contains rows or ids\))?$/,
    /^overlays in other rows: \d+, other poppers not referenced by the row: \d+, skipped page-level overlays: \d+$/,
    /^== truncated: visit limit reached ==$/, /^(?:  )*[a-z][a-z0-9-]* \{skipped\}$/, /^(?:  )*\{more elements\}$/
  ];
  function grammar(name, text) {
    var bad = [];
    text.split('\n').forEach(function (line, i) {
      if (!(ELEMENT.test(line) || CHAIN.test(line) || LINES.some(function (re) { return re.test(line); }))) {
        bad.push((i + 1) + ': ' + line);
        return;
      }
      (line.match(/~?"[^"]*"/g) || []).forEach(function (q) {
        if (q === '""') return;
        var tpl = q.charAt(0) === '~';
        if ((tpl ? TEMPLATES : FIXED).indexOf(q.slice(tpl ? 2 : 1, -1)) < 0) bad.push((i + 1) + ': word ' + q);
      });
      (line.match(/\[(?:role|type|aria-hidden|aria-expanded|aria-disabled|aria-modal)=[^\]]*\]/g) || []).forEach(function (a) {
        var m = /^\[([a-z-]+)=(.*)\]$/.exec(a);
        if (m[2] !== '<v>' && SETS[m[1]].indexOf(m[2]) < 0) bad.push((i + 1) + ': value ' + a);
      });
    });
    check(name + ': every line fits the grammar', bad.length === 0, bad.slice(0, 5).join(' || '));
  }

  // Build a page, run one call with the traps on and mutations watched, then check what came back.
  function scenario(name, html, call, expect) {
    document.body.innerHTML = html;
    document.querySelectorAll('.shadow-host').forEach(function (h) {
      h.attachShadow({ mode: 'open' }).innerHTML = '<span>DECOY-SHADOW</span>';
    });
    var before = document.documentElement.outerHTML;
    var mo = new MutationObserver(function () {});
    mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
    var out = null;
    var err = null;
    trapsOn();
    try { out = call(); } catch (e) { err = e; }
    var got = trapsOff();
    var records = mo.takeRecords();
    mo.disconnect();
    check(name + ': changes nothing', records.length === 0 && document.documentElement.outerHTML === before,
          records.length + ' mutation records');
    check(name + ': touches no forbidden entry', got.length === 0, got.join(', '));
    if (typeof out === 'string') {
      grammar(name, out);
      var found = DECOYS.filter(function (d) { return out.indexOf(d) >= 0; });
      check(name + ': no decoy in the output', found.length === 0, found.join(', '));
    }
    expect(out, err);
  }
  function has(name, out, s) { check(name + ': has ' + s, typeof out === 'string' && out.indexOf(s) >= 0, 'missing'); }
  function lacks(name, out, s) { check(name + ': lacks ' + s, typeof out === 'string' && out.indexOf(s) < 0, 'present'); }
  function fits(name, out, re) { check(name + ': matches ' + re, typeof out === 'string' && re.test(out), 'no match'); }
  function threw(name, err, re) { check(name + ': throws ' + re, err && re.test(err.message), err ? err.message : 'no error'); }

  var cspActive = null;
  async function run(fn) {
    var evalHits = trapsOff();
    checks = [];
    check('evaluating the pasted source touched nothing', evalHits.length === 0, evalHits.join(', '));
    check('the pasted source is a function', typeof fn === 'function', typeof fn);
    if (typeof fn !== 'function') return JSON.stringify({ pass: false, checks: checks });
    var digest = await sha256(fn.toString());
    if (cspActive === null) cspActive = await installCsp();

    scenario('main', page(THREE, COPIES), function () { return fn(TARGET, MODE); }, function (out, err) {
      check('main: no error', !err, err && err.message);
      ['== TEST MODE (offline decoy test) ==\n== skeleton v3 ==', 'tag@',
       'elements whose own text is the id: 2 (in rows 1, outside rows 1)', 'elements whose own text contains the id: 1',
       'main rows holding the id: 1', 'fixed copies: left 0, right 1', '== row fixed-right ==', '<target-id>',
       '"实例ID/名称"', '"开机"', '"更多"', '"已关机"', '"GPU充足"', '"修改"', '"关闭"', '"查看详情"', '"无卡模式开机"', '"释放实例"',
       '~"#天#小时#分后释放"', '~"#-#-# #:#:#"', '~"￥#.#"', '"确认开机吗？"', '~"实例将按￥#.#/时整点扣费"', '"确认无卡模式开机吗？"',
       '~"无卡模式配置为:#.#核心CPU，#GB内存，无GPU卡"', '~"配置费用:￥#.#/时，最低消费￥#.#"', '"当前服务器时间"', '"定时关机时间"',
       '"预计消费"', '"账户余额"', '"账户代金券"', '[placeholder="选择日期"]', '[placeholder="选择时间"]', '"此刻"',
       '.el-picker-panel__footer', '.el-dialog__footer', '.class@', '[role=<v>]', '[aria-expanded=<v>]', '[type=<v>]',
       '[aria-controls=@', '[aria-describedby=@', '{none}', 'template {skipped}', 'iframe {skipped}', 'script {skipped}',
       'style {skipped}', '{more elements}',
       'overlays in other rows: 0, other poppers not referenced by the row: 5, skipped page-level overlays: 1']
        .forEach(function (s) { has('main', out, s); });
      fits('main', out, /\n@\d+: skipped \(not an overlay\)\n/);
      fits('main', out, /\n@\d+: skipped \(contains rows or ids\)\n/);
      lacks('main', out, '== truncated');
    });
    scenario('two rows hold the id', page([row(OTHER, { name: '<div>' + TARGET + '</div>' }), row(TARGET, TARGET_ROW)]),
      function () { return fn(TARGET, MODE); }, function (out, err) {
        check('two rows hold the id: no error', !err, err && err.message);
        has('two rows hold the id', out, 'stopped: the id is in more than one row');
        lacks('two rows hold the id', out, '== row');
      });
    scenario('row holds another id', page([row(TARGET, { inner: '<span style="display:none">' + THIRD + '</span>' })]),
      function () { return fn(TARGET, MODE); }, function (out, err) {
        check('row holds another id: no error', !err, err && err.message);
        has('row holds another id', out, 'stopped: a candidate row holds another instance id');
        lacks('row holds another id', out, '== row');
      });
    scenario('fixed copies short', page(THREE, COPIES.slice(0, 2)), function () { return fn(TARGET, MODE); },
      function (out) {
        has('fixed copies short', out, 'fixed copies: left 0, right mismatch');
        lacks('fixed copies short', out, '== row fixed-right ==');
      });
    var wrongCopy = '<tr class="el-table__row"><td><div class="cell">' + OTHER + '</div></td></tr>';
    scenario('fixed copy of another row', page(THREE, [COPIES[0], wrongCopy, COPIES[2]]), function () { return fn(TARGET, MODE); },
      function (out) {
        has('fixed copy of another row', out, 'fixed copies: left 0, right mismatch');
        lacks('fixed copy of another row', out, '== row fixed-right ==');
      });
    scenario('id absent', page([row(OTHER), row(THIRD)]), function () { return fn(TARGET, MODE); }, function (out) {
      has('id absent', out, 'stopped: the id is in no row');
      lacks('id absent', out, '== row');
    });
    var many = new Array(3101).join('<span>DECOY-N</span>');
    scenario('visit limit', page([row(TARGET, { inner: many })]), function () { return fn(TARGET, MODE); }, function (out) {
      has('visit limit', out, '== truncated: visit limit reached ==');
      check('visit limit: output stays short', out && out.split('\n').length < 3200, out && out.split('\n').length);
    });
    scenario('page too large', page(THREE, COPIES, new Array(40001).join('<i></i>')), function () { return fn(TARGET, MODE); },
      function (out, err) {
        check('page too large: no error', !err, err && err.message);
        has('page too large', out, 'stopped: the page is too large');
        lacks('page too large', out, '== summary ==');
      });
    var big = '<div class="el-dialog__wrapper" style="display:none">' + new Array(30001).join('<i></i>') + '</div>';
    scenario('scan budget runs out', page(THREE, COPIES, big), function () { return fn(TARGET, MODE); }, function (out, err) {
      check('scan budget runs out: no error', !err, err && err.message);
      has('scan budget runs out', out, '== row main ==');
      has('scan budget runs out', out, 'stopped: the scan budget ran out');
      lacks('scan budget runs out', out, 'overlays in other rows:');
    });
    scenario('visible password field', page(THREE, COPIES, '<input type="password">'), function () { return fn(TARGET, MODE); },
      function (out, err) { threw('visible password field', err, /a password field is showing/); });
    [['no test mode on example.com', function () { return fn(TARGET); }, /not the instance list/],
     ['test mode with another id', function () { return fn(OTHER, MODE); }, /only on https:\/\/example\.com with the fake ID/],
     ['unknown mode', function () { return fn(TARGET, 'x'); }, /unknown mode/],
     ['not an id', function () { return fn('nope', MODE); }, /not an instance ID/]].forEach(function (g) {
      scenario(g[0], page(THREE, COPIES), g[1], function (out, err) { threw(g[0], err, g[2]); });
    });

    document.body.innerHTML = '';
    var failed = checks.filter(function (c) { return !c.ok; });
    return JSON.stringify({ pass: failed.length === 0, digest: digest, csp: cspActive, total: checks.length, failed: failed });
  }

  // For mutation checks only: evaluate a source text with the traps on, then run().
  async function runSource(src) {
    trapsOn();
    var fn;
    try { fn = realEval(src); } catch (e) { fn = null; }
    return run(fn);
  }

  // The main page's output itself, to read it line by line (the traps are on during the call).
  function sample(fn) {
    var out = null;
    scenario('sample', page(THREE, COPIES), function () { return fn(TARGET, MODE); }, function (o) { out = o; });
    document.body.innerHTML = '';
    return out;
  }

  return { trapsOn: trapsOn, run: run, runSource: runSource, sample: sample };
})();
