// Offline test for reference/console.js on the fake pages of tests/console/fixtures.js. The short way is
// python tests/console/run_headless.py, which runs it in headless Edge or Chrome, once focused and once as in a hidden
// pane. By hand it runs on https://example.com (or a file:// page such as tests/console/run.html), in three calls:
//   1. paste tests/console/fixtures.js, then this file;
//   2. __con.trapsOn(); var fn = <the text of reference/console.js, pasted as code>;
//   3. await __con.run(fn)
// run() returns one JSON string: {pass, digest: {bytes, sha256}, csp, clone, total, failed: [{name, ok, detail}]}. The
// digest is of fn.toString(); compare it with: python tests/console/fn_digest.py reference/console.js
// clone says which copy was tested: true for the source and for reference/console-clone.min.js, which have the functions
// for cloning an instance and run the tests of those as well; false for the everyday copy reference/console.min.js.
// While the script under test runs, network, storage, navigation and dynamic code are trapped, and DOM-writing entries
// are trapped unless the fixture's own handlers are running; DOM changes outside those handlers are reported too. Every
// click, focus and event the script makes is logged with its element and compared with what that call should do.
// For mutation checks, __con.runSource(sourceText) evaluates a (mutated) source with the traps already on.
window.__con = (function () {
  'use strict';
  var realEval = window.eval;
  var A = 'abcd123456-1234abcd';
  var B = 'wxyz987654-9876wxyz';
  var C = 'mnop135790-1357mnop';
  var D = 'qrst246802-2468qrst';
  var E = 'efgh112233-4455efgh';
  var checks = [];
  function check(name, ok, detail) { checks.push({ name: name, ok: !!ok, detail: ok ? undefined : String(detail) }); }

  var hits = [];
  var undo = null;
  var effects = [];
  var calls = [];
  function trapsOn() {
    if (undo) return;
    hits = [];
    undo = [];
    function busy() { return window.__fx && window.__fx.busy(); }
    function deny(label, orig, writes) {
      return function () {
        if (writes && busy()) return orig.apply(this, arguments);
        hits.push(label);
        throw new Error('forbidden: ' + label);
      };
    }
    function fn(obj, name, label, writes) {
      try {
        if (!obj || !(name in obj)) return;
        var own = Object.getOwnPropertyDescriptor(obj, name);
        Object.defineProperty(obj, name, { configurable: true, writable: true, value: deny(label, obj[name], writes) });
        undo.push(function () { if (own) Object.defineProperty(obj, name, own); else delete obj[name]; });
      } catch (e) { hits.push('could not trap ' + label); }
    }
    // get: 'keep' keeps the getter; otherwise it is denied unless a fixture handler runs. The setter is denied the same
    // way. The trap goes where the property is defined along the prototype chain; a property not found is reported.
    function accessor(start, name, label, get) {
      try {
        var obj = start;
        var own = null;
        for (; obj; obj = Object.getPrototypeOf(obj)) {
          own = Object.getOwnPropertyDescriptor(obj, name);
          if (own) break;
        }
        if (!own || !own.get) { hits.push('could not trap ' + label); return; }
        Object.defineProperty(obj, name, {
          configurable: true,
          get: get === 'keep' ? own.get : function () {
            if (busy()) return own.get.call(this);
            hits.push(label + ' read');
            throw new Error('forbidden: ' + label + ' read');
          },
          set: function (v) {
            if (busy() && own.set) return own.set.call(this, v);
            hits.push(label + ' write');
            throw new Error('forbidden: ' + label + ' write');
          }
        });
        var where = obj;
        undo.push(function () { Object.defineProperty(where, name, own); });
      } catch (e) { hits.push('could not trap ' + label); }
    }
    ['fetch', 'open', 'postMessage', 'WebSocket', 'EventSource', 'Worker', 'SharedWorker', 'Image', 'Audio', 'Option',
     'eval', 'Function', 'scrollTo', 'scrollBy', 'scroll', 'alert', 'confirm', 'prompt', 'print']
      .forEach(function (n) { fn(window, n, 'window.' + n); });
    fn(XMLHttpRequest.prototype, 'open', 'XMLHttpRequest');
    fn(Navigator.prototype, 'sendBeacon', 'sendBeacon');
    ['pushState', 'replaceState', 'back', 'forward', 'go'].forEach(function (n) { fn(History.prototype, n, 'history.' + n); });
    ['getItem', 'setItem', 'removeItem', 'clear', 'key'].forEach(function (n) { fn(Storage.prototype, n, 'storage.' + n); });
    ['createElement', 'createElementNS', 'write', 'writeln', 'open', 'execCommand']
      .forEach(function (n) { fn(Document.prototype, n, 'document.' + n, true); });
    ['setAttribute', 'setAttributeNS', 'removeAttribute', 'toggleAttribute', 'insertAdjacentHTML', 'insertAdjacentElement',
     'insertAdjacentText', 'attachShadow', 'remove', 'append', 'prepend', 'replaceWith', 'after', 'before']
      .forEach(function (n) { fn(Element.prototype, n, 'element.' + n, true); });
    ['scrollIntoView', 'scrollTo', 'scrollBy', 'scroll', 'requestFullscreen', 'requestPointerLock']
      .forEach(function (n) { fn(Element.prototype, n, 'element.' + n); });
    ['appendChild', 'insertBefore', 'removeChild', 'replaceChild'].forEach(function (n) { fn(Node.prototype, n, 'node.' + n, true); });
    ['add', 'remove', 'toggle', 'replace'].forEach(function (n) { fn(DOMTokenList.prototype, n, 'classList.' + n, true); });
    ['submit', 'requestSubmit', 'reset'].forEach(function (n) { fn(HTMLFormElement.prototype, n, 'form.' + n); });
    accessor(document, 'cookie', 'cookie');
    ['localStorage', 'sessionStorage', 'indexedDB'].forEach(function (n) { accessor(window, n, n); });
    accessor(Element.prototype, 'innerHTML', 'innerHTML');
    accessor(Element.prototype, 'outerHTML', 'outerHTML');
    accessor(Node.prototype, 'textContent', 'textContent', 'keep');
    accessor(Node.prototype, 'nodeValue', 'nodeValue', 'keep');
    accessor(HTMLElement.prototype, 'innerText', 'innerText');
    accessor(HTMLElement.prototype, 'dataset', 'dataset');
    accessor(HTMLElement.prototype, 'title', 'title');
    accessor(HTMLInputElement.prototype, 'value', 'input value', 'keep');
    [[HTMLImageElement, ['src', 'srcset']], [HTMLScriptElement, ['src']], [HTMLLinkElement, ['href']],
     [HTMLAnchorElement, ['href', 'ping']], [HTMLAreaElement, ['href', 'ping']], [HTMLIFrameElement, ['src', 'srcdoc']],
     [HTMLMediaElement, ['src']], [HTMLSourceElement, ['src', 'srcset']], [HTMLFormElement, ['action']],
     [HTMLInputElement, ['formAction']], [HTMLButtonElement, ['formAction']], [HTMLObjectElement, ['data']],
     [HTMLEmbedElement, ['src']], [HTMLTextAreaElement, ['value']], [HTMLSelectElement, ['value']]]
      .forEach(function (pair) { pair[1].forEach(function (n) { accessor(pair[0].prototype, n, pair[0].name + '.' + n); }); });
    // The effects the script may have: click a button, a menu item or the label of a checkbox in a dialog, focus an input
    // without scrolling (and send it a focus event once it is the active element), and send mouseenter or mouseleave to
    // a menu trigger. Anything else through these entries is a hit. Each effect let through is logged with its element,
    // and attempt() compares the log with the effects the call should have had.
    function only(obj, name, label, allow, kind) {
      try {
        var own = Object.getOwnPropertyDescriptor(obj, name);
        var orig = obj[name];
        Object.defineProperty(obj, name, { configurable: true, writable: true, value: function () {
          if (busy()) return orig.apply(this, arguments);
          if (allow(this, arguments)) { effects.push({ kind: kind(arguments), el: this }); return orig.apply(this, arguments); }
          hits.push(label);
          throw new Error('forbidden: ' + label);
        } });
        undo.push(function () { if (own) Object.defineProperty(obj, name, own); else delete obj[name]; });
      } catch (e) { hits.push('could not trap ' + label); }
    }
    only(HTMLElement.prototype, 'click', 'click on something other than a button, a menu item or a checkbox label in a dialog',
      function (el) {
        return el.tagName === 'BUTTON' || (el.tagName === 'LI' && el.classList.contains('el-dropdown-menu__item')) ||
               (el.tagName === 'LABEL' && el.classList.contains('el-checkbox') && !!el.closest('.el-dialog'));
      }, function () { return 'click'; });
    only(HTMLElement.prototype, 'focus', 'focus on something other than an input, or without preventScroll', function (el, a) {
      return el.tagName === 'INPUT' && !!a[0] && a[0].preventScroll === true;
    }, function () { return 'focus'; });
    only(HTMLElement.prototype, 'blur', 'blur', function () { return false; }, null);
    only(EventTarget.prototype, 'dispatchEvent', 'an event other than a hover on a menu trigger or a focus on the active input',
      function (el, a) {
        var e = a[0];
        return (e instanceof MouseEvent && (e.type === 'mouseenter' || e.type === 'mouseleave') && !!el.matches &&
                el.matches('button[aria-controls]')) ||
               (e instanceof FocusEvent && e.type === 'focus' && el.tagName === 'INPUT' && document.activeElement === el);
      }, function (a) { return 'event:' + a[0].type; });
    [['setTimeout', window.setTimeout], ['setInterval', window.setInterval]].forEach(function (t) {
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
  function sha256(text) {
    var data = new TextEncoder().encode(text);
    return crypto.subtle.digest('SHA-256', data).then(function (buf) {
      var hex = Array.prototype.map.call(new Uint8Array(buf), function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
      return { bytes: data.length, sha256: hex };
    });
  }

  // One call into the script under test: traps on, no DOM change of its own, no decoy in the result or the error, and
  // exactly the effects that call should have.
  function attempt(label, f) {
    effects = [];
    calls = [];
    trapsOn();
    var out;
    var err = null;
    try { out = f(); } catch (e) { err = e; }
    var got = trapsOff();
    var v = __fx.violations();
    check(label + ': touches no forbidden entry', got.length === 0, got.join(', '));
    check(label + ': changes nothing in the page itself', v.length === 0, v.length + ' mutation records');
    var j = (JSON.stringify(out) || '') + (err ? ' ' + err.message : '');
    check(label + ': no decoy in the result', j.indexOf('DECOY') < 0, j.slice(0, 300));
    var did = JSON.stringify(effects.map(function (e) { return e.kind + ' ' + __fx.describe(e.el); }));
    var want = JSON.stringify(calls.length === 1 ? expected(calls[0], out, err) : []);
    check(label + ': exactly the expected effects', did === want, did + ' instead of ' + want);
    return { out: out, err: err };
  }
  // The effects a call should have had, from the method, its arguments and the page just before it: none when it was
  // refused or threw; a focus is followed by a focus event only while the page has no focus of its own.
  function expected(c, out, err) {
    if (err || !out || out.ok !== true) return [];
    var k = c.key;
    function focus(what) { return ['focus ' + what].concat(c.focused ? [] : ['event:focus ' + what]); }
    switch (c.m) {
      case 'startPowerOnGpu': return ['click ' + k + ':开机'];
      case 'startPowerOff': return ['click ' + k + ':关机'];
      case 'startCancelTimer': return ['click ' + k + ':关闭'];
      case 'startSetTimer': return ['click ' + k + ':' + (c.pre && c.pre.timer ? '修改' : '设置定时关机')];
      case 'startPowerOnNoGpu': return ['click ' + k + ':menu:无卡模式开机'];
      case 'menu': return c.menuShown ? [] : ['event:mouseenter ' + k + ':更多'];
      case 'leaveMenu': return ['event:mouseleave ' + k + ':更多'];
      case 'confirm': return ['click box:确定'];
      case 'dismiss': return ['click ' + c.shownKind + ':取消'];
      case 'openTimerPicker': return focus('dialog:editor');
      case 'focusTimerInput': return focus('picker:' + (c.args[1] === 'date' ? '选择日期' : '选择时间'));
      case 'confirmTimerPicker': return ['click picker:确定'];
      case 'confirmTimerDialog': return ['click dialog:确定'];
      case 'startClone': return ['click ' + k + ':menu:克隆实例新'];
      case 'tickCloneDataDisk': return ['click clone:数据盘'];
      case 'continueClone': return ['click clone:继续'];
      default: return [];
    }
  }
  // The API as the tests use it: every method call is noted, with what the page showed just before, for expected().
  function wrap(raw) {
    if (!raw || typeof raw !== 'object') return raw;
    var api = {};
    Object.keys(raw).forEach(function (m) {
      if (typeof raw[m] !== 'function') { api[m] = raw[m]; return; }
      api[m] = function () {
        var args = Array.prototype.slice.call(arguments);
        var id = typeof args[0] === 'string' ? args[0] : null;
        calls.push({ m: m, args: args, key: id && __fx.keyOf(id), pre: id && __fx.state(id), menuShown: !!id && __fx.menuShown(id),
                     shownKind: __fx.shownKind(), focused: document.hasFocus() });
        return raw[m].apply(raw, arguments);
      };
    });
    return api;
  }
  // The same, and the call must not throw.
  function call(label, f) {
    var a = attempt(label, f);
    check(label + ': no exception', !a.err, a.err && a.err.message);
    return a.out;
  }
  function pick(o, keys) { var r = {}; keys.forEach(function (k) { r[k] = o ? o[k] : undefined; }); return JSON.stringify(r); }
  function eq(name, got, want) { check(name, got === want, got); }
  function isOk(name, r) { check(name + ' is ok', !!r && r.ok === true, JSON.stringify(r)); return r || {}; }
  function isRefused(name, r, re) {
    check(name + ' is refused' + (re ? ' (' + re + ')' : ''),
          !!r && r.ok === false && typeof r.refused === 'string' && (!re || re.test(r.refused)), JSON.stringify(r));
  }
  function clicks(name, want) { eq(name + ': the buttons pressed', JSON.stringify(__fx.counts()), JSON.stringify(want)); }
  function std(extra) {
    var spec = { rows: [
      { id: A, state: '已关机', gpuFree: true },
      { id: B, state: '运行中', mode: 'gpu', spec: 'vGPU-00GB-000W * 2卡', idle: '0/16' },
      { id: C, state: '运行中', mode: 'nogpu', release: '关机15天后释放' },
      { id: D, state: '开机中', mode: 'nogpu' },
      { id: E, state: '已关机', timer: '2026-10-02 01:00:00' }
    ] };
    Object.keys(extra || {}).forEach(function (k) { spec[k] = extra[k]; });
    return spec;
  }
  var make = null;
  // A fresh page: a reload drops window.__autodl, where the script registers itself.
  function newApi() {
    delete window.__autodl;
    return wrap(call('make the api', function () { return make('offline-test'); }));
  }
  var ITEMS = ['无卡模式开机', '更换镜像', '保存镜像', '升降配置', '扩容数据盘', '缩容数据盘', '转包年包月', '克隆实例新',
               '跨实例拷贝数据', '修改SSH密码', '重置系统', '释放实例'];
  var ROW = ['ok', 'id', 'state', 'mode', 'gpuFree', 'gpuIdle', 'timer', 'release', 'spec', 'gpus', 'buttons'];
  function res(name, f) { return pick(call(name, f), ['result']); }

  function reads() {
    __fx.mount(std());
    var api = newApi();
    eq('version', String(api && api.version), '10');
    eq('the script registers itself', pick(window.__autodl, ['brand', 'version', 'mode']),
       JSON.stringify({ brand: 'autodl-autogpu console.js', version: 10, mode: 'offline-test' }));
    eq('page() on the list', pick(call('page', function () { return api.page(); }), ['list', 'login']),
       '{"list":true,"login":false}');
    eq('row(A)', pick(call('row(A)', function () { return api.row(A); }), ROW), JSON.stringify({ ok: true, id: A,
       state: '已关机', mode: null, gpuFree: true, gpuIdle: '1/8', timer: null, release: '14天23小时58分后释放', spec: 'RTX 0000 * 1卡',
       gpus: 1, buttons: ['设置定时关机', '开机', '更多'] }));
    eq('row(B)', pick(call('row(B)', function () { return api.row(B); }), ROW), JSON.stringify({ ok: true, id: B,
       state: '运行中', mode: 'gpu', gpuFree: false, gpuIdle: '0/16', timer: null, release: '14天23小时58分后释放',
       spec: 'vGPU-00GB-000W * 2卡', gpus: 2, buttons: ['设置定时关机', '关机', '更多'] }));
    eq('row(C)', pick(call('row(C)', function () { return api.row(C); }), ['state', 'mode', 'release']),
       JSON.stringify({ state: '运行中', mode: 'nogpu', release: '关机15天后释放' }));
    eq('row(D)', pick(call('row(D)', function () { return api.row(D); }), ['state', 'mode']), '{"state":"开机中","mode":"nogpu"}');
    eq('row(E)', pick(call('row(E)', function () { return api.row(E); }), ROW), JSON.stringify({ ok: true, id: E,
       state: '已关机', mode: null, gpuFree: false, gpuIdle: '1/8', timer: '2026-10-02 01:00:00', release: null, spec: 'RTX 0000 * 1卡',
       gpus: 1, buttons: ['修改', '关闭', '开机', '更多'] }));
    // where a row is, as its first cell shows it (the region and the host): what a user can find the row by
    eq('row(A) tells its place', pick(call('row(A) place', function () { return api.row(A); }), ['place']),
       '{"place":"测试乙区 / 123机"}');
    var all = call('rows()', function () { return api.rows(); }) || [];
    eq('rows() lists every row', JSON.stringify(all.map(function (r) { return r.id; })), JSON.stringify([A, B, C, D, E]));
    eq('rows() tells every row\'s place', JSON.stringify(all.map(function (r) { return r.place; })),
       JSON.stringify([A, B, C, D, E].map(function () { return '测试乙区 / 123机'; })));
    var snap = call('snapshot()', function () { return api.snapshot(); }) || [{}];
    eq('snapshot() holds the stable fields only', JSON.stringify(Object.keys(snap[0])), '["id","state","mode","timer","buttons"]');
    isRefused('row(not an id)', call('row(nope)', function () { return api.row('nope'); }), /instance ID/);
    isRefused('row(absent)', call('row(absent)', function () { return api.row('efgh999999-9999efgh'); }), /no row/);
    eq('dialog() with none open', pick(call('dialog', function () { return api.dialog(); }), ['visible']), '{"visible":0}');
    clicks('reads', {});
  }
  // The spec and the host's free GPUs (Task 6.3): the one text of the 规格详情 cell outside its buttons, and the GPU line
  // of the hidden host popover that the row's region names. Null whenever a link is missing, doubled or names two
  // things, and nothing else of the popover is ever returned (its other lines are decoys).
  function hostInfo() {
    var F = ['spec', 'gpus', 'gpuIdle'];
    __fx.mount({ rows: [
      { id: A, gpuFree: true, spec: 'vGPU-00GB-000W * 2卡', idle: '3/8', hostRef: 'hyphen' },
      { id: B, state: '运行中', hostRef: 'both' },
      { id: C, hostRef: 'differ' },
      { id: D, hostRef: 'none', spec: 'CPU 0核' },
      { id: E, hostRef: 'missing', spec: '' }
    ] });
    var api = newApi();
    eq('row(A): the standard spelling', pick(call('row(A)', function () { return api.row(A); }), F),
       JSON.stringify({ spec: 'vGPU-00GB-000W * 2卡', gpus: 2, gpuIdle: '3/8' }));
    eq('row(B): both spellings naming one popover', pick(call('row(B)', function () { return api.row(B); }), F),
       JSON.stringify({ spec: 'RTX 0000 * 1卡', gpus: 1, gpuIdle: '1/8' }));
    eq('row(C): the two spellings naming two popovers', pick(call('row(C)', function () { return api.row(C); }), F),
       JSON.stringify({ spec: 'RTX 0000 * 1卡', gpus: 1, gpuIdle: null }));
    eq('row(D): no link, and a spec without a GPU count', pick(call('row(D)', function () { return api.row(D); }), F),
       JSON.stringify({ spec: 'CPU 0核', gpus: null, gpuIdle: null }));
    eq('row(E): a link to nothing, and an empty spec cell', pick(call('row(E)', function () { return api.row(E); }), F),
       JSON.stringify({ spec: null, gpus: null, gpuIdle: null }));
    var all = call('rows()', function () { return api.rows(); }) || [];
    eq('rows() carries the same fields', JSON.stringify(all.map(function (r) { return [r.gpus, r.gpuIdle]; })),
       JSON.stringify([[2, '3/8'], [1, '1/8'], [1, null], [null, null], [null, null]]));
    __fx.mount({ rows: [
      { id: A, hostRef: 'dup' },
      { id: B, hostRef: 'nolabel' },
      { id: C, hostRef: 'twolabels' },
      { id: D, hostRef: 'notpopper' },
      { id: E, idle: '1/8卡', specExtra: true }
    ] });
    api = newApi();
    [[A, 'a popover id twice'], [B, 'no GPU line'], [C, 'two GPU lines'], [D, 'not a popper'], [E, 'a value other than free/total']]
      .forEach(function (p) {
        eq('row with ' + p[1], pick(call('row with ' + p[1], function () { return api.row(p[0]); }), ['gpuIdle']), '{"gpuIdle":null}');
      });
    eq('a spec cell with two texts', pick(call('row(E) spec', function () { return api.row(E); }), ['spec', 'gpus']),
       '{"spec":null,"gpus":null}');
    __fx.mount(std({ noSpecCol: true }));
    api = newApi();
    eq('without a 规格详情 header the row still reads', pick(call('row(A) without the spec column', function () { return api.row(A); }),
       ['ok', 'spec', 'gpus', 'gpuIdle']), JSON.stringify({ ok: true, spec: null, gpus: null, gpuIdle: '1/8' }));
    __fx.mount({ rows: [{ id: A, extra: '<div class="region">DECOY-SECOND</div>' }, { id: B }] });
    api = newApi();
    eq('two regions in the first cell: no place, no host', pick(call('row(A) with two regions', function () { return api.row(A); }),
       ['ok', 'place', 'gpuIdle']), '{"ok":true,"place":null,"gpuIdle":null}');
    eq('the row next to it is read as ever', pick(call('row(B) with one region', function () { return api.row(B); }), ['place']),
       '{"place":"测试乙区 / 123机"}');
    clicks('host info', {});
  }
  function gpuOn() {
    __fx.mount(std({ delay: 'later' }));
    var api = newApi();
    var s = isOk('startPowerOnGpu(A)', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }));
    var bd = isOk('bindDialog', call('bindDialog', function () { return api.bindDialog(s.ctx); }));
    eq('the bound box, its price and the GPU count read at the start', pick(bd, ['kind', 'price', 'spec', 'gpus']),
       '{"kind":"box","price":"0.98","spec":"RTX 0000 * 1卡","gpus":1}');
    eq('the prepared copy', pick(bd.copy, ['v', 'prepared', 'op', 'text', 'expect']),
       JSON.stringify({ v: 2, prepared: true, op: 'gpu-on', text: '确认开机吗？ 实例将按￥0.98/时整点扣费', expect: null }));
    var copy = JSON.stringify(bd.copy || {});
    check('the copy names no other row', [B, C, D, E].every(function (id) { return copy.indexOf(id) < 0; }), copy);
    eq('dialog() sees the box', pick(call('dialog', function () { return api.dialog(); }), ['visible']), '{"visible":1}');
    __fx.set(B, { gpuFree: true });
    __fx.set(C, { release: '关机14天后释放' });
    __fx.set(D, { idle: '0/8' });
    isOk('confirm', call('confirm', function () { return api.confirm(s.ctx); }));
    eq('settle before the row changes', res('settle', function () { return api.settle(s.ctx); }), '{"result":"pending"}');
    __fx.tick();
    eq('settle after the row changed', res('settle', function () { return api.settle(s.ctx); }), '{"result":"confirmed"}');
    isRefused('confirm again', call('confirm again', function () { return api.confirm(s.ctx); }), /already/);
    eq('settle with the prepared copy', res('settle(copy)', function () { return api.settle(bd.copy); }), '{"result":"confirmed"}');
    clicks('gpu on', { 'r0:开机': 1, 'box:确定': 1 });
  }
  function noGpuOn() {
    __fx.mount(std({ menuDelay: 'later' }));
    var api = newApi();
    eq('menu(A) before it opens', pick(call('menu(A)', function () { return api.menu(A); }), ['ok', 'open']),
       '{"ok":true,"open":false}');
    isRefused('startPowerOnNoGpu before the menu opens',
              call('startPowerOnNoGpu(A)', function () { return api.startPowerOnNoGpu(A); }), /menu/);
    __fx.tick();
    eq('menu(A) once open', pick(call('menu(A)', function () { return api.menu(A); }), ['open', 'items']),
       JSON.stringify({ open: true, items: ITEMS }));
    var s = isOk('startPowerOnNoGpu(A)', call('startPowerOnNoGpu(A)', function () { return api.startPowerOnNoGpu(A); }));
    var bd = isOk('bindDialog', call('bindDialog', function () { return api.bindDialog(s.ctx); }));
    eq('the bound box and its price', pick(bd, ['kind', 'price']), '{"kind":"box","price":"0.10"}');
    isOk('confirm', call('confirm', function () { return api.confirm(s.ctx); }));
    eq('settle', res('settle', function () { return api.settle(s.ctx); }), '{"result":"confirmed"}');
    eq('row A', JSON.stringify(__fx.state(A)), JSON.stringify({ state: '开机中', mode: 'nogpu', timer: null }));
    isOk('leaveMenu(A)', call('leaveMenu(A)', function () { return api.leaveMenu(A); }));
    clicks('no-GPU on', { 'r0:hover': 1, 'r0:menu:无卡模式开机': 1, 'box:确定': 1, 'r0:leave': 1 });
  }
  function noGpuLeft() {
    __fx.mount(std());
    var api = newApi();
    isRefused('startPowerOnGpu(E) without GPU充足', call('startPowerOnGpu(E)', function () { return api.startPowerOnGpu(E); }), /GPU/);
    isRefused('startPowerOnGpu(B) while running', call('startPowerOnGpu(B)', function () { return api.startPowerOnGpu(B); }), /state/);
    isRefused('startCancelTimer(A) without a timer', call('startCancelTimer(A)', function () { return api.startCancelTimer(A); }), /timer/);
    clicks('refused starts', {});
  }
  function cancelTimer() {
    __fx.mount(std());
    var api = newApi();
    var s = isOk('startCancelTimer(E)', call('startCancelTimer(E)', function () { return api.startCancelTimer(E); }));
    eq('the bound box', pick(call('bindDialog', function () { return api.bindDialog(s.ctx); }), ['ok', 'kind', 'text']),
       '{"ok":true,"kind":"box","text":"取消定时关机"}');
    isOk('confirm', call('confirm', function () { return api.confirm(s.ctx); }));
    eq('settle', res('settle', function () { return api.settle(s.ctx); }), '{"result":"confirmed"}');
    clicks('cancel timer', { 'r4:关闭': 1, 'box:确定': 1 });
  }
  function typeTimer(date, time) {
    __fx.type(__fx.picker().querySelectorAll('input')[0], date);
    __fx.type(__fx.picker().querySelectorAll('input')[1], time);
  }
  // A timer context up to a bound picker; each step is checked on the way.
  function toPicker(api, id) {
    var s = isOk('startSetTimer', call('startSetTimer', function () { return api.startSetTimer(id); }));
    isOk('bindDialog', call('bindDialog', function () { return api.bindDialog(s.ctx); }));
    isOk('openTimerPicker', call('openTimerPicker', function () { return api.openTimerPicker(s.ctx); }));
    isOk('bindTimerPicker', call('bindTimerPicker', function () { return api.bindTimerPicker(s.ctx); }));
    return s;
  }
  function ctxCall(api, s, m, label, arg) { return call(label || m, function () { return api[m](s.ctx, arg); }); }
  function setTimer() {
    __fx.mount(std());
    var api = newApi();
    var s = isOk('startSetTimer(A)', call('startSetTimer(A)', function () { return api.startSetTimer(A); }));
    eq('the bound dialog, with no copy yet', pick(ctxCall(api, s, 'bindDialog'), ['ok', 'kind', 'text', 'copy']),
       '{"ok":true,"kind":"dialog","text":"定时关机"}');
    isOk('openTimerPicker', ctxCall(api, s, 'openTimerPicker'));
    isOk('bindTimerPicker', ctxCall(api, s, 'bindTimerPicker'));
    isOk('focus the date', ctxCall(api, s, 'focusTimerInput', 'focusTimerInput(date)', 'date'));
    isOk('focus the time', ctxCall(api, s, 'focusTimerInput', 'focusTimerInput(time)', 'time'));
    typeTimer('2026-10-02', '00:30');
    eq('readTimer before the picker', pick(ctxCall(api, s, 'readTimer'), ['date', 'time', 'serverTime', 'cost']),
       JSON.stringify({ date: '2026-10-02', time: '00:30', serverTime: '2026-10-01 23:58', cost: '选择时间后计算费用' }));
    isRefused('confirmTimerDialog before the picker', ctxCall(api, s, 'confirmTimerDialog'), /picker/);
    isRefused('prepareTimer before the picker', ctxCall(api, s, 'prepareTimer'), /step/);
    isOk('confirmTimerPicker', ctxCall(api, s, 'confirmTimerPicker'));
    // The console's editor shows the time to the minute; the prepared time is the one the row will show, with seconds.
    eq('readTimer after the picker', pick(ctxCall(api, s, 'readTimer'), ['editor', 'cost']),
       JSON.stringify({ editor: '2026-10-02 00:30', cost: '￥0.03' }));
    isRefused('confirmTimerDialog before prepareTimer', ctxCall(api, s, 'confirmTimerDialog'), /prepareTimer/);
    var p = isOk('prepareTimer', ctxCall(api, s, 'prepareTimer'));
    eq('the prepared time', pick(p, ['expect', 'cost']), JSON.stringify({ expect: '2026-10-02 00:30:00', cost: '￥0.03' }));
    eq('the prepared copy', pick(p.copy, ['v', 'prepared', 'op', 'text', 'expect']),
       JSON.stringify({ v: 2, prepared: true, op: 'timer', text: '定时关机', expect: '2026-10-02 00:30:00' }));
    isOk('confirmTimerDialog', ctxCall(api, s, 'confirmTimerDialog'));
    eq('settle', pick(ctxCall(api, s, 'settle'), ['result']), '{"result":"confirmed"}');
    eq('row A', JSON.stringify(__fx.state(A)), JSON.stringify({ state: '已关机', mode: 'gpu', timer: '2026-10-02 00:30:00' }));
    eq('settle with the prepared copy', res('settle(copy)', function () { return api.settle(p.copy); }), '{"result":"confirmed"}');
    clicks('set timer', { 'r0:设置定时关机': 1, 'picker:确定': 1, 'dialog:确定': 1 });
  }
  function modifyTimer() {
    __fx.mount(std());
    var api = newApi();
    var s = toPicker(api, E);
    eq('the picker opens on the current timer', pick(ctxCall(api, s, 'readTimer'), ['editor', 'date', 'time']),
       JSON.stringify({ editor: '2026-10-02 01:00', date: '2026-10-02', time: '01:00' }));
    typeTimer('2026-10-02', '02:00');
    isOk('confirmTimerPicker', ctxCall(api, s, 'confirmTimerPicker'));
    isOk('prepareTimer', ctxCall(api, s, 'prepareTimer'));
    isOk('confirmTimerDialog', ctxCall(api, s, 'confirmTimerDialog'));
    eq('settle', pick(ctxCall(api, s, 'settle'), ['result']), '{"result":"confirmed"}');
    eq('row E', pick(__fx.state(E), ['timer']), '{"timer":"2026-10-02 02:00:00"}');
    clicks('modify timer', { 'r4:修改': 1, 'picker:确定': 1, 'dialog:确定': 1 });
  }
  // The timer dialog is known by the text of its header, 定时关机 on the console (Task 6.3); any other title is refused.
  function timerTitle() {
    __fx.mount(std({ dialogTitle: '设置定时关机' }));
    var api = newApi();
    var s = isOk('startSetTimer(A)', call('startSetTimer(A)', function () { return api.startSetTimer(A); }));
    isRefused('bindDialog on a timer dialog with another title', ctxCall(api, s, 'bindDialog'), /title does not match/);
    eq('dialog() reads the title from the header', pick(call('dialog', function () { return api.dialog(); }), ['visible', 'list']),
       JSON.stringify({ visible: 1, list: [{ kind: 'dialog', text: '设置定时关机' }] }));
    clicks('timer title', { 'r0:设置定时关机': 1 });
  }

  // Negative cases. Each starts a power-on of A unless it says otherwise.
  function opened(spec) {
    __fx.mount(spec || std());
    var api = newApi();
    var s = isOk('startPowerOnGpu(A)', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }));
    return { api: api, s: s };
  }
  function bound(spec) {
    var o = opened(spec);
    o.b = isOk('bindDialog', call('bindDialog', function () { return o.api.bindDialog(o.s.ctx); }));
    return o;
  }
  function step(o, name, label) { return call(label || name, function () { return o.api[name](o.s.ctx); }); }
  function twoBoxes() {
    var o = opened();
    __fx.openBox(B, 'gpu-on');
    isRefused('bindDialog with two boxes', step(o, 'bindDialog'), /more than one dialog/);
    isRefused('dismiss with nothing bound', step(o, 'dismiss'), /reload/);
    clicks('two boxes', { 'r0:开机': 1 });
  }
  function otherRowChanges() {
    var o = bound();
    __fx.set(B, { state: '关机中' });
    isRefused('confirm after another row changed', step(o, 'confirm'), /changed/);
    isOk('dismiss', step(o, 'dismiss'));
    clicks('another row changed before the confirm', { 'r0:开机': 1, 'box:取消': 1 });
    o = bound(std({ delay: 'later' }));
    isOk('confirm', step(o, 'confirm'));
    __fx.set(B, { state: '关机中' });
    __fx.tick();
    eq('settle after another row changed too', pick(step(o, 'settle'), ['result']), '{"result":"uncertain"}');
  }
  function duplicateId() {
    __fx.mount({ rows: [{ id: A, gpuFree: true }, { id: A, state: '运行中' }, { id: B, state: '运行中' }] });
    var api = newApi();
    isRefused('row(A) shown twice', call('row(A)', function () { return api.row(A); }), /2 places/);
    isRefused('startPowerOnGpu(A) shown twice', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }), /2 places/);
    clicks('duplicate id', {});
  }
  function idElsewhere() {
    var spec = std();
    spec.rows[1].name = 'copy of ' + A;
    __fx.mount(spec);
    var api = newApi();
    isRefused('row(A) with A in the name of another row', call('row(A)', function () { return api.row(A); }), /2 places/);
    __fx.mount(std({ outside: '<div class="el-tooltip__popper" style="display:none">' + A + '</div>' }));
    api = newApi();
    isRefused('row(A) with A in a hidden tip', call('row(A)', function () { return api.row(A); }), /2 places/);
    __fx.mount({ rows: [{ id: B, gpuFree: true, extra: '<div style="display:none">DECOY-NOTE ' + A + '</div>' }] });
    api = newApi();
    isRefused('row(A) found only in a note', call('row(A)', function () { return api.row(A); }), /alone/);
    isRefused('startPowerOnGpu(A) found only in a note', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }), /alone/);
    __fx.mount({ rows: [{ id: B, gpuFree: true, name: A }] });
    api = newApi();
    isRefused('row(A) found only as a name', call('row(A)', function () { return api.row(A); }), /ID cell/);
    isRefused('startPowerOnGpu(A) found only as a name', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }), /ID cell/);
    clicks('the id elsewhere', {});
  }
  function rowChanges() {
    var o = bound();
    __fx.removeRow(A);
    isRefused('confirm after the row was removed', step(o, 'confirm'), /no row/);
    isOk('dismiss', step(o, 'dismiss'));
    clicks('row removed', { 'r0:开机': 1, 'box:取消': 1 });
    o = bound();
    __fx.reverseRows();
    isOk('confirm after the rows were reordered', step(o, 'confirm'));
    eq('settle after the rows were reordered', pick(step(o, 'settle'), ['result']), '{"result":"confirmed"}');
    o = bound();
    __fx.replaceRow(A);
    isRefused('confirm after the row was re-rendered', step(o, 'confirm'), /re-rendered/);
    isOk('dismiss', step(o, 'dismiss'));
    clicks('row replaced', { 'r0:开机': 1, 'box:取消': 1 });
  }
  function menus() {
    __fx.mount(std());
    var api = newApi();
    __fx.showMenu(B);
    isRefused('menu(A) while the menu of B is open', call('menu(A)', function () { return api.menu(A); }), /another/);
    isRefused('startPowerOnNoGpu(A) while the menu of B is open',
              call('startPowerOnNoGpu(A)', function () { return api.startPowerOnNoGpu(A); }), /another/);
    __fx.mount(std());
    api = newApi();
    __fx.dupMenu(A);
    isRefused('menu(A) when its menu id is not unique', call('menu(A)', function () { return api.menu(A); }), /not unique/);
    clicks('menus', {});
  }
  function frozen() {
    var o = opened(std({ freeze: true }));
    eq('bindDialog while the box is still animating', pick(step(o, 'bindDialog'), ['ok', 'pending']), '{"ok":false,"pending":true}');
    __fx.frame();
    isOk('bindDialog after a frame', step(o, 'bindDialog'));
    isOk('confirm', step(o, 'confirm'));
    eq('settle', pick(step(o, 'settle'), ['result']), '{"result":"confirmed"}');
    clicks('frozen box', { 'r0:开机': 1, 'box:确定': 1 });
    __fx.mount(std({ freeze: true }));
    var api = newApi();
    eq('menu(A) while it is still animating', pick(call('menu(A)', function () { return api.menu(A); }), ['ok', 'open']),
       '{"ok":true,"open":false}');
    isRefused('startPowerOnNoGpu(A) while the menu is still animating',
              call('startPowerOnNoGpu(A)', function () { return api.startPowerOnNoGpu(A); }), /not open/);
    __fx.frame();
    eq('menu(A) after a frame', pick(call('menu(A)', function () { return api.menu(A); }), ['ok', 'open']), '{"ok":true,"open":true}');
    clicks('frozen menu', { 'r0:hover': 1 });
  }
  function noChange() {
    var o = bound(std({ delay: 'never' }));
    isOk('confirm', step(o, 'confirm'));
    eq('settle while the row does not change', pick(step(o, 'settle'), ['result']), '{"result":"pending"}');
    eq('settle again', pick(step(o, 'settle'), ['result']), '{"result":"pending"}');
    __fx.toast('开机失败：GPU不足');
    eq('settle with a failure prompt', pick(step(o, 'settle'), ['result', 'prompts']),
       JSON.stringify({ result: 'uncertain', prompts: ['开机失败：GPU不足'] }));
    clicks('no change', { 'r0:开机': 1, 'box:确定': 1 });
    o = bound(std({ ignore: true }));
    isOk('confirm whose click the page ignores', step(o, 'confirm'));
    eq('settle while the box is still there', pick(step(o, 'settle'), ['result']), '{"result":"pending"}');
    isRefused('confirm again', step(o, 'confirm'), /already/);
    isRefused('dismiss after the final confirm', step(o, 'dismiss'), /already/);
    clicks('ignored click', { 'r0:开机': 1, 'box:确定': 1 });
    o = bound(std({ delay: 'never' }));
    isOk('confirm', step(o, 'confirm'));
    __fx.set(A, { state: '开机中', mode: 'nogpu' });
    eq('settle after an unexpected change', pick(step(o, 'settle'), ['result']), '{"result":"uncertain"}');
    // The row changed as expected while the bound box itself still shows: pending until it closes.
    o = bound(std({ keepBox: true }));
    isOk('confirm with a box that stays', step(o, 'confirm'));
    eq('settle while the bound box still shows', pick(step(o, 'settle'), ['result']), '{"result":"pending"}');
    __fx.closeBox();
    eq('settle once the box closed', pick(step(o, 'settle'), ['result']), '{"result":"confirmed"}');
    // Another box with the same text: uncertain, with its text among the prompts; with the copy, any box is foreign.
    o = bound(std({ delay: 'later' }));
    isOk('confirm', step(o, 'confirm'));
    __fx.openBox(B, 'gpu-on');
    __fx.tick();
    eq('settle with another box of the same text', pick(step(o, 'settle'), ['result', 'prompts']),
       JSON.stringify({ result: 'uncertain', prompts: ['确认开机吗？ 实例将按￥0.98/时整点扣费'] }));
    var api2 = newApi();
    eq('settle with the copy while a box shows', res('settle(copy)', function () { return api2.settle(o.b.copy); }),
       '{"result":"uncertain"}');
    // The bound box keeps its node but now says something else: it is not ours any more.
    o = bound(std({ keepBox: true }));
    isOk('confirm with a box that stays', step(o, 'confirm'));
    __fx.retextBox('另一段文字');
    eq('settle when the bound box was reworded', pick(step(o, 'settle'), ['result']), '{"result":"uncertain"}');
  }
  function pages() {
    __fx.mount(std({ login: true }));
    var api = newApi();
    eq('page() on a login page', pick(call('page', function () { return api.page(); }), ['list', 'login']), '{"list":false,"login":true}');
    isRefused('row(A) on a login page', call('row(A)', function () { return api.row(A); }), /log in/);
    isRefused('startPowerOnGpu(A) on a login page', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }), /log in/);
    __fx.mount(std());
    delete window.__autodl;
    var prod = call('make the api without test mode', function () { return make(); });
    eq('page() without test mode', pick(call('page', function () { return prod.page(); }), ['list', 'login']),
       '{"list":false,"login":false}');
    isRefused('row(A) without test mode', call('row(A)', function () { return prod.row(A); }), /instance list/);
    delete window.__autodl;
    var odd = call('make the api with an unknown mode', function () { return make('x'); });
    check('an unknown mode does not register', window.__autodl === undefined, typeof window.__autodl);
    isRefused('row(A) with an unknown mode', call('row(A)', function () { return odd.row(A); }), /unknown mode/);
    api = newApi();
    isRefused('row() with a real-shaped id in test mode', call('row(real)', function () { return api.row('zz00000000-0000zzzz'); }),
              /instance ID/);
    __fx.mount({ rows: [{ id: A, gpuFree: true }], noTable: true });
    api = newApi();
    eq('page() without the instance table', pick(call('page', function () { return api.page(); }), ['list']), '{"list":false}');
    isRefused('row(A) without the instance table', call('row(A)', function () { return api.row(A); }), /instance list/);
    __fx.mount(std({ dupCol: true }));
    api = newApi();
    eq('page() with two key columns of one class', pick(call('page', function () { return api.page(); }), ['list']), '{"list":false}');
    isRefused('row(A) with two key columns of one class', call('row(A)', function () { return api.row(A); }), /not distinct/);
    clicks('pages', {});
  }
  function contexts() {
    var o = bound();
    var api = o.api;
    isRefused('startCancelTimer(E) while a box is open', call('startCancelTimer(E)', function () { return api.startCancelTimer(E); }),
              /another operation/);
    isOk('dismiss', step(o, 'dismiss'));
    var s2 = isOk('startCancelTimer(E)', call('startCancelTimer(E)', function () { return api.startCancelTimer(E); }));
    isRefused('bindDialog with the dismissed context', step(o, 'bindDialog'), /dismissed/);
    isRefused('confirm with the dismissed context', step(o, 'confirm'), /dismissed/);
    isOk('bindDialog with the new context', call('bindDialog(s2)', function () { return api.bindDialog(s2.ctx); }));
    isOk('confirm with the new context', call('confirm(s2)', function () { return api.confirm(s2.ctx); }));
    eq('settle with the new context', res('settle(s2)', function () { return api.settle(s2.ctx); }), '{"result":"confirmed"}');
    isRefused('an unknown context', call('confirm(unknown)', function () { return api.confirm('nope'); }), /unknown context/);
    clicks('two contexts', { 'r0:开机': 1, 'box:取消': 1, 'r4:关闭': 1, 'box:确定': 1 });
  }
  function boxes() {
    var o = bound();
    __fx.swapBox();
    isRefused('confirm after the box was replaced by one with the same text', step(o, 'confirm'), /bound dialog/);
    isRefused('dismiss after the box was replaced', step(o, 'dismiss'), /bound dialog/);
    clicks('box swapped', { 'r0:开机': 1 });
    o = opened();
    __fx.replaceBox('nogpu-on');
    isRefused('bindDialog on the box of another operation', step(o, 'bindDialog'), /does not match/);
    isRefused('dismiss with nothing bound', step(o, 'dismiss'), /reload/);
    clicks('wrong text', { 'r0:开机': 1 });
    // A box about releasing the instance: the text template stops it, and the refusal list must stop it too when the
    // template is weakened (the mutation check for the refusal list weakens both).
    o = opened();
    __fx.replaceBox('release');
    var b = attempt('bindDialog on a release box', function () { return o.api.bindDialog(o.s.ctx); });
    if (b.out && b.out.ok) {
      var c = attempt('confirm on a release box', function () { return o.api.confirm(o.s.ctx); });
      check('confirm on a release box is stopped', (c.err && /refusal list/.test(c.err.message)) || (!c.err && c.out && c.out.ok === false),
            c.err ? c.err.message : JSON.stringify(c.out));
    } else {
      check('bindDialog on a release box is stopped', (b.err && /refusal list/.test(b.err.message)) || (!b.err && b.out && b.out.ok === false),
            b.err ? b.err.message : JSON.stringify(b.out));
    }
    check('a refusal names only the word', !b.err || b.err.message === 'refusal list: 释放', b.err && b.err.message);
    clicks('release box', { 'r0:开机': 1 });
  }
  function gpuGone() {
    var o = bound();
    __fx.set(A, { gpuFree: false });
    isRefused('confirm after GPU充足 is gone', step(o, 'confirm'), /GPU充足/);
    isOk('dismiss', step(o, 'dismiss'));
    clicks('GPU gone', { 'r0:开机': 1, 'box:取消': 1 });
  }
  // The budget check uses the GPU count bindDialog returns (review 4): the row changing to another count in place
  // before the final confirm is a refusal, and a GPU power-on whose count cannot be read does not start. A no-GPU
  // power-on needs no count.
  function specChanges() {
    var o = bound();
    __fx.set(A, { spec: 'RTX 0000 * 2卡' });
    isRefused('confirm after the GPU count changed in place', step(o, 'confirm'), /spec or GPU count/);
    isOk('dismiss', step(o, 'dismiss'));
    clicks('GPU count changed', { 'r0:开机': 1, 'box:取消': 1 });
    var spec = std();
    spec.rows[0].spec = 'RTX 0000';
    __fx.mount(spec);
    var api = newApi();
    isRefused('startPowerOnGpu(A) without a GPU count', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }),
              /GPU count/);
    isOk('menu(A)', call('menu(A)', function () { return api.menu(A); }));
    var s = isOk('startPowerOnNoGpu(A) without a GPU count', call('startPowerOnNoGpu(A)', function () { return api.startPowerOnNoGpu(A); }));
    eq('the no-GPU box carries no GPU count', pick(ctxCall(api, s, 'bindDialog'), ['kind', 'gpus']), '{"kind":"box","gpus":null}');
    isOk('dismiss', ctxCall(api, s, 'dismiss'));
    clicks('no GPU count', { 'r0:hover': 1, 'r0:menu:无卡模式开机': 1, 'box:取消': 1 });
  }
  function reloads() {
    var o = bound();
    var api2 = newApi();
    isRefused('confirm with a context from before a reload', call('confirm(old)', function () { return api2.confirm(o.s.ctx); }),
              /unknown context/);
    __fx.mount(std());
    var s2 = isOk('startPowerOnGpu(A) after the reload', call('startPowerOnGpu(A)', function () { return api2.startPowerOnGpu(A); }));
    isOk('bindDialog after the reload', call('bindDialog', function () { return api2.bindDialog(s2.ctx); }));
    isOk('confirm after the reload', call('confirm', function () { return api2.confirm(s2.ctx); }));
    eq('settle after the reload', res('settle', function () { return api2.settle(s2.ctx); }), '{"result":"confirmed"}');
    clicks('reload before the final confirm', { 'r0:开机': 1, 'box:确定': 1 });
    // After the final confirm: only the prepared copy saved before it, read-only.
    o = bound(std({ delay: 'later' }));
    isOk('confirm', step(o, 'confirm'));
    api2 = newApi();
    isRefused('settle with a context from before a reload', call('settle(old)', function () { return api2.settle(o.s.ctx); }),
              /unknown context/);
    eq('settle with the prepared copy before the row changes', res('settle(copy)', function () { return api2.settle(o.b.copy); }),
       '{"result":"pending"}');
    __fx.tick();
    eq('settle with the prepared copy after the row changed', res('settle(copy)', function () { return api2.settle(o.b.copy); }),
       '{"result":"confirmed"}');
    isRefused('settle with something else', call('settle(other)', function () { return api2.settle({ id: A }); }), /not a prepared copy/);
    var un = JSON.parse(JSON.stringify(o.b.copy));
    un.prepared = false;
    isRefused('settle with a copy not marked prepared', call('settle(unprepared)', function () { return api2.settle(un); }),
              /not a prepared copy/);
  }
  function hiddenBoxes() {
    var o = opened(std({ boxes: 'prerendered' }));
    isOk('bindDialog with hidden boxes in the page', step(o, 'bindDialog'));
    isOk('confirm with hidden boxes in the page', step(o, 'confirm'));
    eq('settle', pick(step(o, 'settle'), ['result']), '{"result":"confirmed"}');
    clicks('hidden boxes', { 'r0:开机': 1, 'box:确定': 1 });
    __fx.mount(std({ boxes: 'prerendered' }));
    var api = newApi();
    eq('dialog() ignores hidden boxes', pick(call('dialog', function () { return api.dialog(); }), ['visible']), '{"visible":0}');
    var s = toPicker(api, A);
    typeTimer('2026-10-02', '00:30');
    isOk('confirmTimerPicker', ctxCall(api, s, 'confirmTimerPicker'));
    var p = isOk('prepareTimer', ctxCall(api, s, 'prepareTimer'));
    isOk('confirmTimerDialog', ctxCall(api, s, 'confirmTimerDialog'));
    eq('settle with the prepared copy', res('settle(copy)', function () { return api.settle(p.copy); }), '{"result":"confirmed"}');
    clicks('timer with hidden boxes', { 'r0:设置定时关机': 1, 'picker:确定': 1, 'dialog:确定': 1 });
  }
  function powerOff() {
    __fx.mount(std());
    var api = newApi();
    isRefused('startPowerOff(A) while stopped', call('startPowerOff(A)', function () { return api.startPowerOff(A); }), /state/);
    var s = isOk('startPowerOff(B)', call('startPowerOff(B)', function () { return api.startPowerOff(B); }));
    eq('the bound box', pick(call('bindDialog', function () { return api.bindDialog(s.ctx); }), ['ok', 'kind', 'text', 'reviewed']),
       '{"ok":true,"kind":"box","text":"确认关机吗？","reviewed":false}');
    isRefused('confirm on the unreviewed box', call('confirm', function () { return api.confirm(s.ctx); }), /reviewed/);
    isOk('dismiss', call('dismiss', function () { return api.dismiss(s.ctx); }));
    clicks('power off', { 'r1:关机': 1, 'box:取消': 1 });
  }
  function steps() {
    var o = opened(std({ delay: 'never' }));
    isRefused('confirm before bindDialog', step(o, 'confirm'), /bindDialog/);
    isRefused('settle before the final confirm', step(o, 'settle'), /final confirm/);
    ['openTimerPicker', 'bindTimerPicker', 'confirmTimerPicker', 'prepareTimer', 'confirmTimerDialog', 'readTimer'].forEach(function (m) {
      isRefused(m + ' on a power-on context', step(o, m), /timer context/);
    });
    isRefused('focusTimerInput on a power-on context',
              call('focusTimerInput', function () { return o.api.focusTimerInput(o.s.ctx, 'date'); }), /timer context/);
    isOk('bindDialog', step(o, 'bindDialog'));
    isRefused('bindDialog twice', step(o, 'bindDialog'), /step/);
    isOk('confirm', step(o, 'confirm'));
    isRefused('dismiss after the final confirm', step(o, 'dismiss'), /already/);
    isRefused('bindDialog after the final confirm', step(o, 'bindDialog'), /already/);
    clicks('steps', { 'r0:开机': 1, 'box:确定': 1 });
    __fx.mount(std());
    var api = newApi();
    var s = isOk('startSetTimer(A)', call('startSetTimer(A)', function () { return api.startSetTimer(A); }));
    isRefused('openTimerPicker before bindDialog', ctxCall(api, s, 'openTimerPicker'), /bindDialog/);
    isOk('bindDialog', ctxCall(api, s, 'bindDialog'));
    isRefused('confirm on a timer context', ctxCall(api, s, 'confirm'), /confirmTimerDialog/);
    isRefused('bindTimerPicker before openTimerPicker', ctxCall(api, s, 'bindTimerPicker'), /step/);
    isRefused('focusTimerInput before the picker is bound', ctxCall(api, s, 'focusTimerInput', 'focusTimerInput', 'date'), /step/);
    isOk('openTimerPicker', ctxCall(api, s, 'openTimerPicker'));
    isRefused('openTimerPicker while its picker opens', ctxCall(api, s, 'openTimerPicker'), /step/);
    isOk('bindTimerPicker', ctxCall(api, s, 'bindTimerPicker'));
    isRefused('focusTimerInput on the editor', ctxCall(api, s, 'focusTimerInput', 'focusTimerInput(editor)', 'editor'), /date or time/);
    typeTimer('2026-10-02', '00:30');
    isOk('confirmTimerPicker', ctxCall(api, s, 'confirmTimerPicker'));
    isOk('openTimerPicker again', ctxCall(api, s, 'openTimerPicker'));
    isRefused('confirmTimerDialog while the picker is open again', ctxCall(api, s, 'confirmTimerDialog'), /picker/);
    isRefused('prepareTimer while the picker is open again', ctxCall(api, s, 'prepareTimer'), /step/);
    isOk('bindTimerPicker again', ctxCall(api, s, 'bindTimerPicker'));
    isOk('confirmTimerPicker again', ctxCall(api, s, 'confirmTimerPicker'));
    isOk('prepareTimer', ctxCall(api, s, 'prepareTimer'));
    isOk('openTimerPicker after prepareTimer', ctxCall(api, s, 'openTimerPicker'));
    isRefused('confirmTimerDialog after reopening the picker', ctxCall(api, s, 'confirmTimerDialog'), /picker/);
    isOk('bindTimerPicker a third time', ctxCall(api, s, 'bindTimerPicker'));
    isOk('confirmTimerPicker a third time', ctxCall(api, s, 'confirmTimerPicker'));
    isOk('prepareTimer again', ctxCall(api, s, 'prepareTimer'));
    isOk('confirmTimerDialog', ctxCall(api, s, 'confirmTimerDialog'));
    isRefused('confirmTimerPicker after the final confirm', ctxCall(api, s, 'confirmTimerPicker'), /already/);
    isRefused('prepareTimer after the final confirm', ctxCall(api, s, 'prepareTimer'), /already/);
    clicks('timer steps', { 'r0:设置定时关机': 1, 'picker:确定': 3, 'dialog:确定': 1 });
  }
  function interaction() {
    __fx.mount(std({ outside: '<div class="v-modal" style="position:fixed;left:0;top:0;right:0;bottom:0;z-index:1500"></div>' }));
    var api = newApi();
    isRefused('startPowerOnGpu(A) under a cover', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }), /covered/);
    __fx.mount(std({ shift: true }));
    api = newApi();
    isRefused('startPowerOnGpu(A) outside the viewport', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }),
              /viewport/);
    var spec = std();
    spec.rows[0].disabled = true;
    __fx.mount(spec);
    api = newApi();
    isRefused('startPowerOnGpu(A) on a disabled button', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }),
              /disabled/);
    spec = std();
    spec.rows[0] = { id: A, state: '关机中', gpuFree: true, ops: '开机' };
    __fx.mount(spec);
    api = newApi();
    isRefused('startPowerOnGpu(A) while 关机中', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }), /state/);
    [['hidden', /hidden/], ['aria-hidden', /hidden/], ['inert', /hidden/], ['vis-hidden', /hidden/], ['pe-none', /pointer events/]]
      .forEach(function (w) {
        var sp = std();
        sp.rows[0].wrap = w[0];
        __fx.mount(sp);
        var a = newApi();
        isRefused('startPowerOnGpu(A) inside ' + w[0], call('startPowerOnGpu(A)', function () { return a.startPowerOnGpu(A); }), w[1]);
      });
    __fx.mount(std());
    api = newApi();
    __fx.coverPart(A);
    isRefused('startPowerOnGpu(A) with one sample point covered', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }),
              /covered/);
    clicks('interaction', {});
  }

  // One operation at a time (review finding 1): a second start waits until the first ended.
  function oneAtATime() {
    var spec = std({ boxDelay: 'later' });
    spec.rows[4].gpuFree = true;
    __fx.mount(spec);
    var api = newApi();
    var s = isOk('startPowerOnGpu(A)', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }));
    eq('bindDialog before the box shows', pick(ctxCall(api, s, 'bindDialog'), ['ok', 'pending']), '{"ok":false,"pending":true}');
    isRefused('startPowerOnGpu(E) while A is open', call('startPowerOnGpu(E)', function () { return api.startPowerOnGpu(E); }),
              /another operation/);
    isRefused('menu(B) while A is open', call('menu(B)', function () { return api.menu(B); }), /another operation/);
    isRefused('dismiss with nothing bound', ctxCall(api, s, 'dismiss'), /reload/);
    __fx.tick();
    isOk('bindDialog once the box shows', ctxCall(api, s, 'bindDialog'));
    isOk('confirm', ctxCall(api, s, 'confirm'));
    eq('settle', pick(ctxCall(api, s, 'settle'), ['result']), '{"result":"confirmed"}');
    var s2 = isOk('startPowerOnGpu(E) once A ended', call('startPowerOnGpu(E)', function () { return api.startPowerOnGpu(E); }));
    __fx.tick();
    isOk('bindDialog for E', ctxCall(api, s2, 'bindDialog'));
    isOk('dismiss E', ctxCall(api, s2, 'dismiss'));
    isOk('startCancelTimer(E) once E was dismissed', call('startCancelTimer(E)', function () { return api.startCancelTimer(E); }));
    clicks('one at a time', { 'r0:开机': 1, 'box:确定': 1, 'r4:开机': 1, 'box:取消': 1, 'r4:关闭': 1 });
    // A final confirm whose outcome is still pending keeps the operation open.
    var o = bound(std({ delay: 'never' }));
    isOk('confirm', step(o, 'confirm'));
    eq('settle', pick(step(o, 'settle'), ['result']), '{"result":"pending"}');
    isRefused('startCancelTimer(E) while A is pending', call('startCancelTimer(E)', function () { return o.api.startCancelTimer(E); }),
              /another operation/);
    // Settling with the prepared copy ends the operation too, when it is the copy of the open context.
    o = bound(std());
    isOk('confirm', step(o, 'confirm'));
    eq('settle with the copy in the same page', res('settle(copy)', function () { return o.api.settle(o.b.copy); }), '{"result":"confirmed"}');
    isOk('startCancelTimer(E) once settled with the copy', call('startCancelTimer(E)', function () { return o.api.startCancelTimer(E); }));
    // The copy may come back with its keys in another order; it is still the copy of the open context.
    o = bound(std());
    isOk('confirm', step(o, 'confirm'));
    var c0 = o.b.copy || { before: {} };
    var turned = { expect: c0.expect, text: c0.text, others: c0.others, op: c0.op, id: c0.id, ctx: c0.ctx, prepared: c0.prepared, v: c0.v,
                   before: { buttons: c0.before.buttons, timer: c0.before.timer, mode: c0.before.mode, state: c0.before.state, id: c0.before.id } };
    eq('settle with the copy, its keys in another order', res('settle(copy)', function () { return o.api.settle(turned); }),
       '{"result":"confirmed"}');
    isOk('startCancelTimer(E) once settled with that copy', call('startCancelTimer(E)', function () { return o.api.startCancelTimer(E); }));
    // A box that never shows: nothing to dismiss; a reload starts over.
    o = opened(std({ boxDelay: 'never' }));
    eq('bindDialog while no box shows', pick(step(o, 'bindDialog'), ['ok', 'pending']), '{"ok":false,"pending":true}');
    isRefused('dismiss while no box shows', step(o, 'dismiss'), /reload/);
    __fx.mount(std());
    api = newApi();
    isOk('startPowerOnGpu(A) after a reload', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }));
  }
  // The picker is bound to the operation (review finding 3).
  function pickerBinding() {
    __fx.mount(std());
    var api = newApi();
    var s = isOk('startSetTimer(A)', call('startSetTimer(A)', function () { return api.startSetTimer(A); }));
    isOk('bindDialog', ctxCall(api, s, 'bindDialog'));
    __fx.extraPicker();
    isRefused('readTimer while a picker it did not bind is open', ctxCall(api, s, 'readTimer'), /did not bind/);
    isRefused('openTimerPicker while another picker is open', ctxCall(api, s, 'openTimerPicker'), /already open/);
    __fx.removePickers();
    isOk('openTimerPicker', ctxCall(api, s, 'openTimerPicker'));
    __fx.extraPicker();
    isRefused('bindTimerPicker with two pickers', ctxCall(api, s, 'bindTimerPicker'), /more than one picker/);
    __fx.mount(std());
    api = newApi();
    s = toPicker(api, A);
    __fx.swapPicker();
    isRefused('focusTimerInput after the picker was replaced', ctxCall(api, s, 'focusTimerInput', 'focusTimerInput', 'date'),
              /bound picker/);
    isRefused('confirmTimerPicker after the picker was replaced', ctxCall(api, s, 'confirmTimerPicker'), /bound picker/);
    isRefused('readTimer after the picker was replaced', ctxCall(api, s, 'readTimer'), /bound picker/);
    isOk('dismiss', ctxCall(api, s, 'dismiss'));
    clicks('picker replaced', { 'r0:设置定时关机': 1, 'dialog:取消': 1 });
    __fx.mount(std({ pickerDelay: 'later' }));
    api = newApi();
    s = isOk('startSetTimer(A)', call('startSetTimer(A)', function () { return api.startSetTimer(A); }));
    isOk('bindDialog', ctxCall(api, s, 'bindDialog'));
    isOk('openTimerPicker', ctxCall(api, s, 'openTimerPicker'));
    eq('bindTimerPicker before the picker shows', pick(ctxCall(api, s, 'bindTimerPicker'), ['ok', 'pending']), '{"ok":false,"pending":true}');
    __fx.tick();
    isOk('bindTimerPicker once it shows', ctxCall(api, s, 'bindTimerPicker'));
    __fx.mount(std({ freeze: true }));
    api = newApi();
    s = isOk('startSetTimer(A)', call('startSetTimer(A)', function () { return api.startSetTimer(A); }));
    eq('bindDialog while the dialog is still animating', pick(ctxCall(api, s, 'bindDialog'), ['ok', 'pending']), '{"ok":false,"pending":true}');
    __fx.frame();
    isOk('bindDialog after a frame', ctxCall(api, s, 'bindDialog'));
    isOk('openTimerPicker', ctxCall(api, s, 'openTimerPicker'));
    eq('bindTimerPicker while the picker is still animating', pick(ctxCall(api, s, 'bindTimerPicker'), ['ok', 'pending']),
       '{"ok":false,"pending":true}');
    __fx.frame();
    isOk('bindTimerPicker after a frame', ctxCall(api, s, 'bindTimerPicker'));
  }
  // The time must change and lie after the server time (review finding 5).
  function sameTime() {
    __fx.mount(std());
    var api = newApi();
    var s = toPicker(api, E);
    isOk('confirmTimerPicker on the current time', ctxCall(api, s, 'confirmTimerPicker'));
    isRefused('prepareTimer with the current time', ctxCall(api, s, 'prepareTimer'), /same as the current timer/);
    // A copy whose expected time is the current one never settles as confirmed: the row did not change.
    isOk('openTimerPicker', ctxCall(api, s, 'openTimerPicker'));
    isOk('bindTimerPicker', ctxCall(api, s, 'bindTimerPicker'));
    typeTimer('2026-10-02', '02:00');
    isOk('confirmTimerPicker', ctxCall(api, s, 'confirmTimerPicker'));
    var p = isOk('prepareTimer', ctxCall(api, s, 'prepareTimer'));
    isOk('dismiss', ctxCall(api, s, 'dismiss'));
    var forged = JSON.parse(JSON.stringify(p.copy || {}));
    forged.expect = '2026-10-02 01:00:00';
    eq('settle with a copy expecting the current time', res('settle(forged)', function () { return api.settle(forged); }),
       '{"result":"pending"}');
    __fx.mount(std({ pickerIgnore: true }));
    api = newApi();
    s = toPicker(api, A);
    typeTimer('2026-10-02', '00:30');
    isOk('confirmTimerPicker that the page ignores', ctxCall(api, s, 'confirmTimerPicker'));
    isRefused('prepareTimer when the picker did not fill the editor', ctxCall(api, s, 'prepareTimer'), /no date and time/);
    __fx.mount(std());
    api = newApi();
    s = toPicker(api, A);
    typeTimer('2026-10-01', '23:00');
    isOk('confirmTimerPicker', ctxCall(api, s, 'confirmTimerPicker'));
    isRefused('prepareTimer before the server time', ctxCall(api, s, 'prepareTimer'), /server time/);
    // The server time must read as a real moment (seconds may be missing), and so must the time chosen.
    [['unknown', /server time cannot be read/], ['2026/10/01 23:58:00', /server time cannot be read/]].forEach(function (st) {
      __fx.mount(std({ serverTime: st[0] }));
      var a = newApi();
      var x = toPicker(a, A);
      typeTimer('2026-10-02', '00:30');
      isOk('confirmTimerPicker', ctxCall(a, x, 'confirmTimerPicker'));
      isRefused('prepareTimer with the server time ' + st[0], ctxCall(a, x, 'prepareTimer'), st[1]);
    });
    __fx.mount(std({ serverTime: '2026-10-01 23:58:00' }));
    api = newApi();
    s = toPicker(api, A);
    typeTimer('2026-10-02', '00:30');
    isOk('confirmTimerPicker', ctxCall(api, s, 'confirmTimerPicker'));
    isOk('prepareTimer with a server time with seconds', ctxCall(api, s, 'prepareTimer'));
    __fx.mount(std());
    api = newApi();
    s = toPicker(api, A);
    typeTimer('2026-13-40', '25:61');
    isOk('confirmTimerPicker', ctxCall(api, s, 'confirmTimerPicker'));
    isRefused('prepareTimer with no real moment', ctxCall(api, s, 'prepareTimer'), /no valid time/);
  }
  // The console's date editor shows the time to the minute (seen on the real page, 2026-10-02) while the row shows its
  // seconds. An editor that showed the seconds is read the same way, and the final confirm requires the editor to be as
  // it was prepared, whichever form it has.
  function editorForms() {
    [false, true].forEach(function (seconds) {
      var shows = seconds ? '2026-10-02 00:30:00' : '2026-10-02 00:30';
      var tag = seconds ? ' (editor with seconds)' : ' (editor to the minute)';
      __fx.mount(std({ editorSeconds: seconds }));
      var api = newApi();
      var s = toPicker(api, A);
      typeTimer('2026-10-02', '00:30');
      isOk('confirmTimerPicker' + tag, ctxCall(api, s, 'confirmTimerPicker'));
      eq('what the editor shows' + tag, pick(ctxCall(api, s, 'readTimer'), ['editor']), JSON.stringify({ editor: shows }));
      var p = isOk('prepareTimer' + tag, ctxCall(api, s, 'prepareTimer'));
      eq('the prepared time has seconds' + tag, pick(p, ['expect']), '{"expect":"2026-10-02 00:30:00"}');
      __fx.type(__fx.editor(), seconds ? '2026-10-02 00:45:00' : '2026-10-02 00:45');
      isRefused('confirmTimerDialog after the editor changed' + tag, ctxCall(api, s, 'confirmTimerDialog'), /changed after prepareTimer/);
      __fx.type(__fx.editor(), shows);
      isOk('confirmTimerDialog' + tag, ctxCall(api, s, 'confirmTimerDialog'));
      eq('settle' + tag, pick(ctxCall(api, s, 'settle'), ['result']), '{"result":"confirmed"}');
      eq('row A' + tag, pick(__fx.state(A), ['timer']), '{"timer":"2026-10-02 00:30:00"}');
    });
    // The same time as the current timer is refused in either form.
    __fx.mount(std({ editorSeconds: true }));
    var api = newApi();
    var s = toPicker(api, E);
    eq('the picker opens on the current timer (editor with seconds)', pick(ctxCall(api, s, 'readTimer'), ['editor', 'date', 'time']),
       JSON.stringify({ editor: '2026-10-02 01:00:00', date: '2026-10-02', time: '01:00' }));
    isOk('confirmTimerPicker on the current time (editor with seconds)', ctxCall(api, s, 'confirmTimerPicker'));
    isRefused('prepareTimer with the current time (editor with seconds)', ctxCall(api, s, 'prepareTimer'), /same as the current timer/);
  }
  // The refusal list covers all visible text of a dialog, and names only the word (review finding 6).
  function dialogWords() {
    __fx.mount(std({ dialogNote: '到期后将释放实例' }));
    var api = newApi();
    var s = isOk('startSetTimer(A)', call('startSetTimer(A)', function () { return api.startSetTimer(A); }));
    var b = attempt('bindDialog on a timer dialog that mentions releasing', function () { return api.bindDialog(s.ctx); });
    check('the timer dialog is stopped by the refusal list', !!b.err && b.err.message === 'refusal list: 释放',
          b.err ? b.err.message : JSON.stringify(b.out));
    isRefused('openTimerPicker on a blocked dialog', ctxCall(api, s, 'openTimerPicker'), /step/);
    isOk('dismiss the blocked dialog', ctxCall(api, s, 'dismiss'));
    clicks('dialog words', { 'r0:设置定时关机': 1, 'dialog:取消': 1 });
    __fx.mount(std({ dialogRootNote: '到期后将释放实例' }));
    api = newApi();
    s = isOk('startSetTimer(A)', call('startSetTimer(A)', function () { return api.startSetTimer(A); }));
    b = attempt('bindDialog on a timer dialog with the word in its root', function () { return api.bindDialog(s.ctx); });
    check('text right in the dialog root counts', !!b.err && b.err.message === 'refusal list: 释放', b.err ? b.err.message : JSON.stringify(b.out));
  }
  // One script per page (second review, finding 1): running it again returns the same API, so the page has one state.
  function rePaste() {
    var spec = std({ boxDelay: 'later' });
    spec.rows[4].gpuFree = true;
    __fx.mount(spec);
    var api = newApi();
    var s = isOk('startPowerOnGpu(A)', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }));
    var first = window.__autodl;
    var raw2 = call('run the script again in the same page', function () { return make('offline-test'); });
    check('the second run returns the registered API', !!first && raw2 === first, typeof raw2);
    var again = wrap(raw2);
    isRefused('startPowerOnGpu(E) through the second run while A is open',
              call('startPowerOnGpu(E)', function () { return again.startPowerOnGpu(E); }), /another operation/);
    __fx.tick();
    isOk('bindDialog through the second run', ctxCall(again, s, 'bindDialog'));
    isOk('dismiss through the second run', ctxCall(again, s, 'dismiss'));
    window.__autodl = { brand: 'someone else' };
    var other = call('run the script with window.__autodl taken', function () { return make('offline-test'); });
    isRefused('row(A) with window.__autodl taken', call('row(A)', function () { return other.row(A); }), /reload/);
    eq('page() with window.__autodl taken', pick(call('page', function () { return other.page(); }), ['list']), '{"list":false}');
    delete window.__autodl;
    clicks('run again', { 'r0:开机': 1, 'box:取消': 1 });
  }
  // Every row shows one valid and unique instance ID before an operation starts (review finding 10).
  function tableRows() {
    __fx.mount({ rows: [{ id: A, gpuFree: true }, { id: 'not-an-id', state: '运行中' }] });
    var api = newApi();
    eq('row(A) still reads', pick(call('row(A)', function () { return api.row(A); }), ['ok']), '{"ok":true}');
    isRefused('startPowerOnGpu(A) with a row whose ID is not valid', call('startPowerOnGpu(A)', function () {
      return api.startPowerOnGpu(A); }), /no single instance ID/);
    __fx.mount({ rows: [{ id: A, gpuFree: true }, { id: B, state: '运行中' }, { id: B, state: '已关机' }] });
    api = newApi();
    isRefused('startPowerOnGpu(A) with another ID in two rows', call('startPowerOnGpu(A)', function () {
      return api.startPowerOnGpu(A); }), /two rows/);
    clicks('table rows', {});
  }

  // ---- the clone (version 8): the menu item, the dialog, the rows after a creation, the SSH address ----
  var CLONE_OPEN = { 'r4:hover': 1, 'r4:menu:克隆实例新': 1 };
  function plus(base, more) {
    var o = {};
    [base, more].forEach(function (x) { Object.keys(x).forEach(function (k) { o[k] = x[k]; }); });
    return o;
  }
  // A clone of E (stopped, without GPU充足) with its dialog open; with the dialog bound; with the data disk ticked.
  function cloneOpened(extra) {
    __fx.mount(std(extra));
    var api = newApi();
    isOk('menu(E)', call('menu(E)', function () { return api.menu(E); }));
    var s = isOk('startClone(E)', call('startClone(E)', function () { return api.startClone(E); }));
    return { api: api, s: s };
  }
  function cloneBound(extra) {
    var o = cloneOpened(extra);
    o.b = isOk('bindCloneDialog', step(o, 'bindCloneDialog'));
    return o;
  }
  function cloneTicked(extra) {
    var o = cloneBound(extra);
    isOk('tickCloneDataDisk', step(o, 'tickCloneDataDisk'));
    return o;
  }
  function shownCloneItem() {
    return Array.prototype.filter.call(document.querySelectorAll('li.el-dropdown-menu__item'), function (li) {
      return li.textContent === '克隆实例新' && li.getClientRects().length > 0;
    })[0];
  }
  function cloneButton(label) {
    return Array.prototype.filter.call(__fx.cloneDialog().querySelectorAll('button'), function (b) { return b.textContent === label; })[0];
  }
  // startClone clicks the one menu item whose whole text is 克隆实例新, on a stopped row without GPU充足 whose menu is
  // open, and nothing else. The older functions cannot take that item for one of theirs.
  function cloneStart() {
    __fx.mount(std());
    var api = newApi();
    isRefused('startClone(E) before its menu is open', call('startClone(E)', function () { return api.startClone(E); }), /not open/);
    isRefused('startClone(A) on a row that shows GPU充足', call('startClone(A)', function () { return api.startClone(A); }), /GPU充足/);
    isRefused('startClone(B) while running', call('startClone(B)', function () { return api.startClone(B); }), /state/);
    isRefused('startClone(not an id)', call('startClone(nope)', function () { return api.startClone('nope'); }), /instance ID/);
    eq('menu(E)', pick(call('menu(E)', function () { return api.menu(E); }), ['open', 'items']),
       JSON.stringify({ open: true, items: ITEMS }));
    var s = isOk('startClone(E)', call('startClone(E)', function () { return api.startClone(E); }));
    eq('the clone context', pick(s, ['ok', 'stage']), '{"ok":true,"stage":"opened"}');
    isRefused('startClone(E) while the clone is open', call('startClone(E) again', function () { return api.startClone(E); }),
              /another operation/);
    isRefused('startPowerOnGpu(A) while the clone is open', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }),
              /another operation/);
    clicks('clone start', CLONE_OPEN);
    [['克隆实例', 'without its badge'], ['克隆实例新版', 'with one more character']].forEach(function (v) {
      __fx.mount(std({ cloneItem: v[0] }));
      var a = newApi();
      isOk('menu(E)', call('menu(E)', function () { return a.menu(E); }));
      isRefused('startClone(E) with the item ' + v[1], call('startClone(E)', function () { return a.startClone(E); }), /no single/);
      clicks('the clone item ' + v[1], { 'r4:hover': 1 });
    });
    __fx.mount(std({ cloneTwice: true }));
    api = newApi();
    isOk('menu(E)', call('menu(E)', function () { return api.menu(E); }));
    isRefused('startClone(E) with the item twice', call('startClone(E)', function () { return api.startClone(E); }), /no single/);
    clicks('the clone item twice', { 'r4:hover': 1 });
    var spec = std();
    spec.rows[4].spec = 'RTX 0000';
    __fx.mount(spec);
    api = newApi();
    isRefused('startClone(E) without a readable GPU count', call('startClone(E)', function () { return api.startClone(E); }),
              /cannot be read/);
    __fx.mount(std());
    api = newApi();
    __fx.showMenu(B);
    isRefused('startClone(E) while the menu of B is open', call('startClone(E)', function () { return api.startClone(E); }), /another/);
    __fx.mount(std());
    api = newApi();
    isOk('menu(E)', call('menu(E)', function () { return api.menu(E); }));
    __fx.openBox(B, 'gpu-on');
    isRefused('startClone(E) while a box is open', call('startClone(E)', function () { return api.startClone(E); }), /already open/);
    __fx.mount({ rows: [{ id: E }, { id: 'not-an-id', state: '运行中' }] });
    api = newApi();
    isRefused('startClone(E) with a row whose ID is not valid', call('startClone(E)', function () { return api.startClone(E); }),
              /no single instance ID/);
    __fx.mount(std());
    api = newApi();
    isOk('menu(E)', call('menu(E)', function () { return api.menu(E); }));
    __fx.coverCorner(shownCloneItem());
    isRefused('startClone(E) with the item partly covered', call('startClone(E)', function () { return api.startClone(E); }), /covered/);
    clicks('the clone item covered', { 'r4:hover': 1 });
    __fx.mount(std({ menuItems: ['克隆实例', '释放实例'] }));
    api = newApi();
    eq('a menu without the no-GPU item', pick(call('menu(E)', function () { return api.menu(E); }), ['items']),
       JSON.stringify({ items: ['克隆实例新', '释放实例'] }));
    isRefused('startPowerOnNoGpu(E) finds no item of its own', call('startPowerOnNoGpu(E)', function () { return api.startPowerOnNoGpu(E); }),
              /no single/);
    clicks('the older function leaves the clone item alone', { 'r4:hover': 1 });
  }
  // bindCloneDialog binds the one dialog titled 克隆实例 once every text in it is an entry of the fixed table, the system
  // disk is ticked and fixed, the data disk can be ticked and the sparse-file switch is off. A dialog that is the clone
  // dialog but fails a check can still be dismissed; anything else leaves only a reload.
  function cloneBind() {
    var o = cloneOpened();
    eq('dialog() sees the clone dialog', pick(call('dialog', function () { return o.api.dialog(); }), ['visible', 'list']),
       JSON.stringify({ visible: 1, list: [{ kind: 'dialog', text: '克隆实例' }] }));
    isRefused('bindDialog on a clone context', step(o, 'bindDialog'), /bindCloneDialog/);
    isRefused('tickCloneDataDisk before the dialog is bound', step(o, 'tickCloneDataDisk'), /step/);
    isRefused('continueClone before the dialog is bound', step(o, 'continueClone'), /step/);
    isRefused('dismiss before the dialog is bound', step(o, 'dismiss'), /reload/);
    var b = isOk('bindCloneDialog', step(o, 'bindCloneDialog'));
    var READ = ['stage', 'kind', 'text', 'dataDisk', 'expandGb', 'remaining', 'spec', 'gpus'];
    var first = JSON.stringify({ stage: 'clone-bound', kind: 'dialog', text: '克隆实例', dataDisk: false, expandGb: 0, remaining: 10,
                                 spec: 'RTX 0000 * 1卡', gpus: 1 });
    eq('what the bound clone dialog says', pick(b, READ), first);
    eq('the dialog read a second time', pick(step(o, 'bindCloneDialog', 'bindCloneDialog again'), READ), first);
    isRefused('confirm on a clone context', step(o, 'confirm'), /step/);
    isRefused('settle on a clone context', step(o, 'settle'), /final confirm/);
    ['openTimerPicker', 'bindTimerPicker', 'confirmTimerPicker', 'prepareTimer', 'confirmTimerDialog', 'readTimer'].forEach(function (m) {
      isRefused(m + ' on a clone context', step(o, m), /timer context/);
    });
    isOk('dismiss the clone dialog', step(o, 'dismiss'));
    isRefused('bindCloneDialog after the dismiss', step(o, 'bindCloneDialog'), /dismissed/);
    clicks('clone bound and dismissed', plus(CLONE_OPEN, { 'clone:取消': 1 }));
    isOk('startPowerOnGpu(A) once the clone was dismissed', call('startPowerOnGpu(A)', function () { return o.api.startPowerOnGpu(A); }));
    var p = bound();
    ['bindCloneDialog', 'tickCloneDataDisk', 'continueClone'].forEach(function (m) {
      isRefused(m + ' on a power-on context', step(p, m), /clone context/);
    });
    isRefused('bindCloneDialog with an unknown context', call('bindCloneDialog(unknown)', function () { return p.api.bindCloneDialog('nope'); }),
              /unknown context/);
    o = cloneOpened({ boxDelay: 'later' });
    eq('bindCloneDialog before the dialog shows', pick(step(o, 'bindCloneDialog'), ['ok', 'pending']), '{"ok":false,"pending":true}');
    __fx.tick();
    isOk('bindCloneDialog once it shows', step(o, 'bindCloneDialog'));
    __fx.mount(std({ freeze: true }));
    var api = newApi();
    eq('menu(E) while it is still animating', pick(call('menu(E)', function () { return api.menu(E); }), ['ok', 'open']),
       '{"ok":true,"open":false}');
    __fx.frame();
    isOk('menu(E) after a frame', call('menu(E)', function () { return api.menu(E); }));
    var s = isOk('startClone(E)', call('startClone(E)', function () { return api.startClone(E); }));
    eq('bindCloneDialog while the dialog is still animating', pick(ctxCall(api, s, 'bindCloneDialog'), ['ok', 'pending']),
       '{"ok":false,"pending":true}');
    __fx.frame();
    isOk('bindCloneDialog after a frame', ctxCall(api, s, 'bindCloneDialog'));
    [[{ note: '到期后将释放实例' }, /not in the fixed table/, 'a line that holds a word of the refusal list', true],
     [{ note: '本次克隆免费' }, /not in the fixed table/, 'a line of harmless text', true],
     [{ alert: '克隆后源实例将被释放' }, /not in the fixed table/, 'another warning', true],
     [{ go: '继续并支付' }, /not in the fixed table/, 'another label on 继续', true],
     [{ left: '今天剩余克隆次数：若干次' }, /not in the fixed table/, 'a count that is no number', true],
     [{ diskLabel: '数据盘（推荐）' }, /not in the fixed table/, 'another label on the data disk', true],
     [{ noDisk: true }, /once each and in order/, 'no 数据盘 checkbox', true],
     [{ sys: 'unchecked' }, /not ticked and fixed/, 'the system disk not ticked', true],
     [{ sys: 'enabled' }, /not ticked and fixed/, 'the system disk free to change', true],
     [{ diskDisabled: true }, /数据盘 checkbox is disabled/, 'the data disk disabled', true],
     [{ sparse: true }, /switch is not off/, 'the sparse-file switch on', true],
     [{ extraInput: true }, /inputs other than/, 'one more input', true],
     [{ title: '克隆实例（新）' }, /not the 克隆实例 dialog/, 'another title', false]].forEach(function (v) {
      var x = cloneOpened({ clone: v[0] });
      isRefused('bindCloneDialog with ' + v[2], step(x, 'bindCloneDialog'), v[1]);
      isRefused('tickCloneDataDisk after ' + v[2], step(x, 'tickCloneDataDisk'), /step/);
      isRefused('continueClone after ' + v[2], step(x, 'continueClone'), /step/);
      if (v[3]) {
        isOk('dismiss the dialog refused for ' + v[2], step(x, 'dismiss'));
        clicks('the dialog with ' + v[2], plus(CLONE_OPEN, { 'clone:取消': 1 }));
      } else {
        isRefused('dismiss with nothing bound after ' + v[2], step(x, 'dismiss'), /reload/);
        clicks('the dialog with ' + v[2], CLONE_OPEN);
      }
    });
    o = cloneOpened();
    __fx.openClone();
    isRefused('bindCloneDialog with two dialogs', step(o, 'bindCloneDialog'), /more than one dialog/);
    o = cloneOpened({ boxDelay: 'never' });
    __fx.openBox(B, 'gpu-on');
    isRefused('bindCloneDialog on a confirm box', step(o, 'bindCloneDialog'), /not the 克隆实例 dialog/);
    isRefused('dismiss with nothing bound', step(o, 'dismiss'), /reload/);
    clicks('a box in place of the clone dialog', CLONE_OPEN);
  }
  // tickCloneDataDisk clicks the label of the data disk's checkbox while it is not ticked, and never to untick it. The
  // dialog is then read again: a source with a paid expansion shows one more sentence, which gives its size.
  function cloneTick() {
    var o = cloneTicked();
    var READ = ['ok', 'dataDisk', 'expandGb', 'remaining'];
    eq('the dialog read again: ticked, no expansion', pick(step(o, 'bindCloneDialog'), READ),
       JSON.stringify({ ok: true, dataDisk: true, expandGb: 0, remaining: 10 }));
    isRefused('tickCloneDataDisk twice', step(o, 'tickCloneDataDisk', 'tickCloneDataDisk again'), /already ticked/);
    clicks('clone tick', plus(CLONE_OPEN, { 'clone:数据盘': 1 }));
    o = cloneBound({ clone: { expandGb: 5 } });
    eq('before the tick the expansion is not shown', pick(o.b, ['dataDisk', 'expandGb']), '{"dataDisk":false,"expandGb":0}');
    isOk('tickCloneDataDisk', step(o, 'tickCloneDataDisk'));
    eq('the dialog read again: 5 GB of paid expansion', pick(step(o, 'bindCloneDialog'), READ),
       JSON.stringify({ ok: true, dataDisk: true, expandGb: 5, remaining: 10 }));
    o = cloneTicked({ clone: { expandText: '源实例有扩容数据盘：5GB，请先扩容目标实例' } });
    isRefused('the dialog read again with the sentence in other words', step(o, 'bindCloneDialog'), /not in the fixed table/);
    isRefused('continueClone with the sentence in other words', step(o, 'continueClone'), /not in the fixed table/);
    isOk('dismiss', step(o, 'dismiss'));
    clicks('another sentence', plus(CLONE_OPEN, { 'clone:数据盘': 1, 'clone:取消': 1 }));
    o = cloneTicked({ clone: { lag: true, expandGb: 5 } });
    eq('the dialog read again before the page marked the tick', pick(step(o, 'bindCloneDialog'), ['ok', 'pending']),
       '{"ok":false,"pending":true}');
    eq('continueClone before the page marked the tick', pick(step(o, 'continueClone'), ['ok', 'pending']), '{"ok":false,"pending":true}');
    eq('tickCloneDataDisk before the page marked the tick', pick(step(o, 'tickCloneDataDisk', 'tickCloneDataDisk again'), ['ok', 'pending']),
       '{"ok":false,"pending":true}');
    __fx.tick();
    eq('the dialog read again once it did', pick(step(o, 'bindCloneDialog'), READ),
       JSON.stringify({ ok: true, dataDisk: true, expandGb: 5, remaining: 10 }));
    clicks('a tick that shows late', plus(CLONE_OPEN, { 'clone:数据盘': 1 }));
    o = cloneBound();
    __fx.coverCorner(__fx.cloneDialog().querySelector('input[value="copy_data_disk"]').closest('label'));
    isRefused('tickCloneDataDisk on a label partly covered', step(o, 'tickCloneDataDisk'), /covered/);
    clicks('the label covered', CLONE_OPEN);
  }
  // continueClone repeats every check, needs the data disk ticked, a clone left today, and the row as at the start and
  // still without GPU充足; then it clicks 继续 and the operation is over.
  function cloneGo() {
    var o = cloneBound({ clone: { expandGb: 5 } });
    isRefused('continueClone before the data disk is ticked', step(o, 'continueClone'), /not ticked/);
    isOk('tickCloneDataDisk', step(o, 'tickCloneDataDisk'));
    var g = isOk('continueClone', step(o, 'continueClone'));
    eq('what continueClone answers', pick(g, ['stage', 'dataDisk', 'expandGb', 'remaining']),
       JSON.stringify({ stage: 'continued', dataDisk: true, expandGb: 5, remaining: 10 }));
    clicks('clone continued', plus(CLONE_OPEN, { 'clone:数据盘': 1, 'clone:继续': 1 }));
    eq('the page is no longer the instance list', pick(call('page', function () { return o.api.page(); }), ['list']), '{"list":false}');
    isRefused('continueClone twice', step(o, 'continueClone', 'continueClone again'), /step/);
    isRefused('tickCloneDataDisk after 继续', step(o, 'tickCloneDataDisk'), /step/);
    isRefused('dismiss after 继续', step(o, 'dismiss'), /instance list/);
    // back on the list with the same script object, as in the console's single page: the operation has ended
    __fx.mount(std());
    isOk('startPowerOnGpu(A) once the clone went on', call('startPowerOnGpu(A)', function () { return o.api.startPowerOnGpu(A); }));
    o = cloneBound({ clone: { remaining: 0 } });
    eq('the dialog says no clone is left today', pick(o.b, ['remaining']), '{"remaining":0}');
    isOk('tickCloneDataDisk', step(o, 'tickCloneDataDisk'));
    isRefused('continueClone with no clone left today', step(o, 'continueClone'), /no clone is left/);
    isOk('dismiss', step(o, 'dismiss'));
    clicks('no clone left', plus(CLONE_OPEN, { 'clone:数据盘': 1, 'clone:取消': 1 }));
    o = cloneTicked();
    __fx.set(E, { gpuFree: true });
    isRefused('continueClone once the row shows GPU充足', step(o, 'continueClone'), /GPU充足/);
    isOk('dismiss', step(o, 'dismiss'));
    clicks('GPU back before 继续', plus(CLONE_OPEN, { 'clone:数据盘': 1, 'clone:取消': 1 }));
    o = cloneTicked();
    __fx.set(B, { state: '关机中' });
    isRefused('continueClone after another row changed', step(o, 'continueClone'), /changed/);
    o = cloneTicked();
    __fx.replaceRow(E);
    isRefused('continueClone after the row was re-rendered', step(o, 'continueClone'), /re-rendered/);
    o = cloneTicked();
    __fx.set(E, { spec: 'RTX 0000 * 2卡' });
    isRefused('continueClone after the GPU count changed in place', step(o, 'continueClone'), /spec or GPU count/);
    o = cloneTicked();
    __fx.swapClone();
    isRefused('continueClone after the dialog was replaced', step(o, 'continueClone'), /bound dialog/);
    isRefused('tickCloneDataDisk after the dialog was replaced', step(o, 'tickCloneDataDisk'), /bound dialog/);
    isRefused('bindCloneDialog after the dialog was replaced', step(o, 'bindCloneDialog'), /bound dialog/);
    isRefused('dismiss after the dialog was replaced', step(o, 'dismiss'), /bound dialog/);
    clicks('the dialog replaced', plus(CLONE_OPEN, { 'clone:数据盘': 1 }));
    o = cloneTicked();
    __fx.coverCorner(cloneButton('继续'));
    isRefused('continueClone with 继续 partly covered', step(o, 'continueClone'), /covered/);
    clicks('继续 covered', plus(CLONE_OPEN, { 'clone:数据盘': 1 }));
    // the page ignores 继续: for the script the operation is over, and the dialog it left can still be dismissed
    o = cloneTicked({ clone: { ignoreGo: true } });
    isOk('continueClone that the page ignores', step(o, 'continueClone'));
    isOk('dismiss the dialog the page left open', step(o, 'dismiss'));
    clicks('继续 ignored', plus(CLONE_OPEN, { 'clone:数据盘': 1, 'clone:继续': 1, 'clone:取消': 1 }));
  }
  // idDigests answers a digest per row and no ID. findCreated answers only rows that were not there before, on the
  // given host and with the given spec; with the ID the platform gave, only that row, under the same three conditions.
  function cloneRows() {
    __fx.mount(std());
    var api = newApi();
    var d = isOk('idDigests', call('idDigests', function () { return api.idDigests(); }));
    var digs = d.digests || [];
    check('one digest per row, in the form the script uses', digs.length === 5 && digs.every(function (x) {
      return /^\d{1,3}:[0-9a-f]{8}$/.test(x); }), JSON.stringify(d));
    check('the digests differ row by row', digs.filter(function (x, k) { return digs.indexOf(x) === k; }).length === 5, JSON.stringify(d));
    check('the digests name no instance', [A, B, C, D, E].every(function (id) { return JSON.stringify(d).indexOf(id) < 0; }),
          JSON.stringify(d));
    var N1 = 'wxyz111111-2222wxyz';
    var N2 = 'wxyz111111-3333wxyz';
    var N3 = 'mnop222222-4444mnop';
    var SPEC = 'RTX 0000 * 1卡';
    var after = std();
    after.rows.push({ id: N1, state: '开机中' }, { id: N2, state: '运行中', spec: 'RTX 0000 * 2卡' }, { id: N3, state: '运行中' });
    __fx.mount(after);
    function found(label, f) { return pick(call(label, f), ['ok', 'count', 'rows']); }
    function none(label, f) { eq(label, found(label, f), '{"ok":true,"count":0,"rows":[]}'); }
    var f = call('findCreated', function () { return api.findCreated(digs, 'wxyz111111', SPEC); });
    eq('the one candidate', pick(f, ['ok', 'count', 'rows']),
       JSON.stringify({ ok: true, count: 1, rows: [{ id: N1, state: '开机中', mode: 'gpu', timer: null }] }));
    check('the answer names no other row', [A, B, C, D, E, N2, N3].every(function (id) { return JSON.stringify(f).indexOf(id) < 0; }),
          JSON.stringify(f));
    eq('with the ID the platform gave', found('findCreated(id)', function () { return api.findCreated(digs, 'wxyz111111', SPEC, N1); }),
       JSON.stringify({ ok: true, count: 1, rows: [{ id: N1, state: '开机中', mode: 'gpu', timer: null }] }));
    none('with the ID of a new row that has another spec', function () { return api.findCreated(digs, 'wxyz111111', SPEC, N2); });
    none('with the ID of a new row on another host', function () { return api.findCreated(digs, 'wxyz111111', SPEC, N3); });
    none('with the ID of a row that was there before', function () { return api.findCreated(digs, 'wxyz987654', 'vGPU-00GB-000W * 2卡', B); });
    none('on a host whose only row was there before', function () { return api.findCreated(digs, 'abcd123456', SPEC); });
    none('on a host with no row', function () { return api.findCreated(digs, 'qrst000000', SPEC); });
    eq('the new row of the other spec', found('findCreated(other spec)', function () { return api.findCreated(digs, 'wxyz111111', 'RTX 0000 * 2卡'); }),
       JSON.stringify({ ok: true, count: 1, rows: [{ id: N2, state: '运行中', mode: 'gpu', timer: null }] }));
    var two = std();
    two.rows.push({ id: N1, state: '开机中' }, { id: N2, state: '开机中', mode: 'nogpu', timer: '2026-10-02 03:00:00' });
    __fx.mount(two);
    eq('two candidates are both answered', found('findCreated(two)', function () { return api.findCreated(digs, 'wxyz111111', SPEC); }),
       JSON.stringify({ ok: true, count: 2, rows: [{ id: N1, state: '开机中', mode: 'gpu', timer: null },
                                                  { id: N2, state: '开机中', mode: 'nogpu', timer: '2026-10-02 03:00:00' }] }));
    [[['abc'], 'a digest of another form'], ['19:0a1b2c3d', 'a digest that is not in a list'], [[], 'an empty list'],
     [[19], 'a number'], [undefined, 'nothing']].forEach(function (v) {
      isRefused('findCreated with ' + v[1], call('findCreated(' + v[1] + ')', function () { return api.findCreated(v[0], 'wxyz111111', SPEC); }),
                /digests/);
    });
    isRefused('findCreated with an instance ID for the host', call('findCreated(host)', function () {
      return api.findCreated(digs, N1, SPEC); }), /host ID/);
    isRefused('findCreated with an empty spec', call('findCreated(spec)', function () { return api.findCreated(digs, 'wxyz111111', ''); }),
              /spec/);
    isRefused('findCreated with something that is no instance ID', call('findCreated(bad id)', function () {
      return api.findCreated(digs, 'wxyz111111', SPEC, 'nope'); }), /instance ID/);
    __fx.mount({ rows: [{ id: A }, { id: 'not-an-id' }] });
    isRefused('idDigests with a row whose ID is not valid', call('idDigests', function () { return api.idDigests(); }),
              /no single instance ID/);
    isRefused('findCreated with a row whose ID is not valid', call('findCreated', function () {
      return api.findCreated(digs, 'wxyz111111', SPEC); }), /no single instance ID/);
    clicks('clone rows', {});
  }
  // sshAddress is the one function that reads the page's own data: for a running row, the three keys of the one object
  // that carries its ID, checked against each other. A refusal says which check failed and carries no value read.
  function sshAddr() {
    __fx.mount(std());
    var api = newApi();
    __fx.dataReads();
    var r = isOk('sshAddress(B)', call('sshAddress(B)', function () { return api.sshAddress(B); }));
    eq('the address of the running row, and nothing else', JSON.stringify(r),
       JSON.stringify({ ok: true, id: B, host: 'connect.faker1.seetacloud.com', port: 20001 }));
    eq('the keys read from the page data', JSON.stringify(__fx.dataReads()),
       JSON.stringify(['uuid', 'uuid', 'uuid', 'uuid', 'uuid', 'proxy_host', 'ssh_port', 'ssh_command']));
    isRefused('sshAddress(A) while stopped', call('sshAddress(A)', function () { return api.sshAddress(A); }), /state/);
    isRefused('sshAddress(D) while starting', call('sshAddress(D)', function () { return api.sshAddress(D); }), /state/);
    isRefused('sshAddress(not an id)', call('sshAddress(nope)', function () { return api.sshAddress('nope'); }), /instance ID/);
    eq('nothing is read from the page data for a refusal by the row', JSON.stringify(__fx.dataReads()), '[]');
    isOk('startPowerOnGpu(A)', call('startPowerOnGpu(A)', function () { return api.startPowerOnGpu(A); }));
    isOk('sshAddress(B) while another operation is open', call('sshAddress(B)', function () { return api.sshAddress(B); }));
    function withB(fields, extra) {
      var s = std(extra);
      Object.keys(fields).forEach(function (k) { s.rows[1][k] = fields[k]; });
      __fx.mount(s);
      return newApi();
    }
    function refusedB(name, fields, extra, re, secrets) {
      var a = withB(fields, extra);
      var out = call('sshAddress(B) with ' + name, function () { return a.sshAddress(B); });
      isRefused('sshAddress(B) with ' + name, out, re);
      var j = JSON.stringify(out) || '';
      check('the refusal with ' + name + ' carries no value read', (secrets || []).every(function (s) { return j.indexOf(s) < 0; }), j);
      check('nothing of the other table is read with ' + name, __fx.dataReads().every(function (k) { return k.indexOf('other:') !== 0; }), '');
    }
    refusedB('a host outside the two domains', { ssh: { host: 'connect.fake.example.org' } }, null, /SSH host/, ['example']);
    refusedB('the domain at the front only', { ssh: { host: 'seetacloud.com.evil.example' } }, null, /SSH host/, ['evil']);
    refusedB('a host that only ends like the domain', { ssh: { host: 'evilseetacloud.com' } }, null, /SSH host/, ['evil']);
    refusedB('a host with a space in it', { ssh: { host: 'con nect.seetacloud.com' } }, null, /SSH host/, ['con nect']);
    refusedB('a host in capitals', { ssh: { host: 'Connect.Fake.seetacloud.com' } }, null, /SSH host/, ['Connect']);
    refusedB('a host with an empty label', { ssh: { host: 'connect..seetacloud.com' } }, null, /SSH host/, ['connect']);
    refusedB('a host that is no text', { ssh: { host: 12345 } }, null, /SSH host/, ['12345']);
    refusedB('port 0', { ssh: { port: 0 } }, null, /SSH port/, []);
    refusedB('port 65536', { ssh: { port: 65536 } }, null, /SSH port/, ['65536']);
    refusedB('a port with a fraction', { ssh: { port: 22.5 } }, null, /SSH port/, ['22.5']);
    refusedB('a port as text', { ssh: { port: '20001' } }, null, /SSH port/, ['20001']);
    refusedB('a command for another host', { ssh: { cmd: 'ssh -p 20001 root@other.seetacloud.com' } }, null, /SSH command/,
             ['other.', '20001', 'faker1']);
    refusedB('a command with more in it', { ssh: { cmd: 'ssh -p 20001 root@connect.faker1.seetacloud.com -o ProxyCommand=x' } }, null,
             /SSH command/, ['ProxyCommand', '20001', 'faker1']);
    refusedB('no entry in the page data', { inData: 0 }, null, /no entry/, ['faker1']);
    refusedB('two entries in the page data', { inData: 2 }, null, /more than once/, ['faker1', '20001']);
    refusedB('no page data on the app element', {}, { vnode: 'none' }, /was not found/, []);
    refusedB('two tables rooted at the instance table', {}, { vnode: 'two' }, /was not found/, ['faker1']);
    refusedB('a table without its rows', {}, { vnode: 'nodata' }, /was not found/, []);
    refusedB('the table under another name', {}, { vnode: 'named' }, /was not found/, ['faker1']);
    refusedB('the table deeper than the walk goes', {}, { vnode: 'deep' }, /was not found/, ['faker1']);
    refusedB('a node that holds itself (the walk has to end)', {}, { vnode: 'loop' }, /was not found/, ['faker1']);
    var a2 = withB({ ssh: { host: 'connect.fake.autodl.com', port: 65535 } });
    eq('a host under autodl.com and the highest port', pick(call('sshAddress(B)', function () { return a2.sshAddress(B); }), ['host', 'port']),
       '{"host":"connect.fake.autodl.com","port":65535}');
    a2 = withB({}, { vnode: 'suspense' });
    eq('the table behind a suspense boundary', pick(call('sshAddress(B)', function () { return a2.sshAddress(B); }), ['ok', 'port']),
       '{"ok":true,"port":20001}');
    check('nothing of the other table was read', __fx.dataReads().every(function (k) { return k.indexOf('other:') !== 0; }), '');
    clicks('ssh address', {});
  }
  var CLONE_FNS = ['startClone', 'bindCloneDialog', 'tickCloneDataDisk', 'continueClone', 'idDigests', 'findCreated', 'sshAddress'];
  // The seven functions exist in every form of the API: outside the instance list and in the shell that refuses all.
  // A page that holds the everyday copy does not serve the copy for cloning: that one refuses until a reload.
  function cloneShells() {
    var NEW = CLONE_FNS;
    __fx.mount(std());
    var api = newApi();
    eq('the copy for cloning says what it is', String(api.clone), 'true');
    window.__autodl = { brand: 'autodl-autogpu console.js', version: 10, mode: 'offline-test' };
    var over = call('run the copy for cloning in a page that holds the everyday copy', function () { return make('offline-test'); });
    isRefused('row(A) through it', call('row(A)', function () { return over.row(A); }), /reload/);
    isRefused('startClone(E) through it', call('startClone(E)', function () { return over.startClone(E); }), /reload/);
    delete window.__autodl;
    var prod = call('make the api without test mode', function () { return make(); });
    NEW.forEach(function (m) {
      isRefused(m + ' without test mode', call(m, function () { return prod[m](E); }),
                m === 'findCreated' ? /instance list/ : /instance list|unknown context/);
    });
    delete window.__autodl;
    window.__autodl = { brand: 'someone else' };
    var other = call('run the script with window.__autodl taken', function () { return make('offline-test'); });
    NEW.forEach(function (m) {
      isRefused(m + ' with window.__autodl taken', call(m, function () { return other[m](E); }), /reload/);
    });
    delete window.__autodl;
    clicks('clone shells', {});
  }
  // The everyday copy has none of the clone's functions, clicks nothing of the clone's and reads nothing of the page's
  // own data. A page that holds the copy for cloning serves it as well.
  function noClone() {
    __fx.mount(std({ menuItems: ['克隆实例', '释放实例'] }));
    var api = newApi();
    CLONE_FNS.concat(['clone']).forEach(function (m) { check('the everyday copy has no ' + m, api[m] === undefined, typeof api[m]); });
    eq('a menu without the no-GPU item', pick(call('menu(E)', function () { return api.menu(E); }), ['items']),
       JSON.stringify({ items: ['克隆实例新', '释放实例'] }));
    isRefused('startPowerOnNoGpu(E) finds no item of its own', call('startPowerOnNoGpu(E)', function () { return api.startPowerOnNoGpu(E); }),
              /no single/);
    clicks('the everyday copy leaves the clone item alone', { 'r4:hover': 1 });
    eq('nothing was read from the page data', JSON.stringify(__fx.dataReads()), '[]');
    var full = { brand: 'autodl-autogpu console.js', version: 10, mode: 'offline-test', clone: true };
    window.__autodl = full;
    var got = call('run the everyday copy in a page that holds the copy for cloning', function () { return make('offline-test'); });
    check('the copy for cloning serves the everyday copy as well', got === full, typeof got);
    delete window.__autodl;
  }

  // A CSP that blocks network, frames, media and forms, if the page accepts one added by script (as in skeleton_test.js).
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

  var SCENARIOS = [reads, hostInfo, gpuOn, noGpuOn, noGpuLeft, cancelTimer, setTimer, modifyTimer, timerTitle, twoBoxes, otherRowChanges, duplicateId,
                   idElsewhere, rowChanges, menus, frozen, noChange, pages, contexts, boxes, gpuGone, specChanges, reloads, hiddenBoxes, powerOff,
                   steps, interaction, oneAtATime, pickerBinding, sameTime, editorForms, dialogWords, rePaste, tableRows];
  // The copy for cloning runs these as well; the everyday copy runs noClone in their place.
  var CLONE_SCENARIOS = [cloneStart, cloneBind, cloneTick, cloneGo, cloneRows, sshAddr, cloneShells];
  var cspActive = null;
  async function run(fn) {
    var evalHits = trapsOff();
    checks = [];
    check('evaluating the pasted source touched nothing', evalHits.length === 0, evalHits.join(', '));
    check('the pasted source is a function', typeof fn === 'function', typeof fn);
    if (typeof fn !== 'function') {
      return JSON.stringify({ pass: false, total: checks.length, failed: checks.filter(function (c) { return !c.ok; }) });
    }
    var digest = await sha256(fn.toString());
    if (cspActive === null) cspActive = await installCsp();
    make = fn;
    // Which copy is this: the one with the clone's functions or the everyday one. window.__expectClone, when it is set
    // (tests/console/run_headless.py sets it), says which it has to be.
    var probe = null;
    delete window.__autodl;
    try { probe = fn('offline-test'); } catch (e) { probe = null; }
    delete window.__autodl;
    var clone = !!probe && typeof probe.startClone === 'function';
    if (typeof window.__expectClone === 'boolean') {
      check('this copy has the functions of the clone: ' + window.__expectClone, clone === window.__expectClone, String(clone));
    }
    SCENARIOS.concat(clone ? CLONE_SCENARIOS : [noClone]).forEach(function (s) {
      try { s(); } catch (e) { check(s.name + ': ran to the end', false, e && e.message); }
    });
    __fx.mount({ rows: [] });
    var failed = checks.filter(function (c) { return !c.ok; });
    return JSON.stringify({ pass: failed.length === 0, digest: digest, csp: cspActive, clone: clone, total: checks.length, failed: failed });
  }

  // For mutation checks only: evaluate a source text with the traps on, then run().
  async function runSource(src) {
    trapsOn();
    var fn;
    try { fn = realEval(src); } catch (e) { fn = null; }
    return run(fn);
  }

  return { trapsOn: trapsOn, run: run, runSource: runSource };
})();
