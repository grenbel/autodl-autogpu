// Offline test for reference/clone-page.js on the fake page of tests/console/clone_fixtures.js. The short way is
// python tests/console/run_headless.py, which runs it in headless Edge or Chrome, once focused and once as in a hidden
// pane. By hand it runs on https://example.com (or a file:// page), in three calls:
//   1. paste tests/console/clone_fixtures.js, then this file;
//   2. __ccon.trapsOn(); var fn = <the text of reference/clone-page.js, pasted as code>;
//   3. await __ccon.run(fn)
// run() returns one JSON string: {pass, digest: {bytes, sha256}, total, failed: [{name, ok, detail}]}. The digest is of
// fn.toString(); compare it with: python tests/console/fn_digest.py reference/clone-page.js
// While the script under test runs, network, storage, navigation and dynamic code are trapped, and DOM-writing entries
// are trapped unless the fixture's own handlers are running; DOM changes outside those handlers are reported too. Every
// click, focus, event and scroll the script makes is logged with its element and compared with what that call should do.
// The clock the script reads (Date.now) is the test's own, so that two minutes pass when the test says so.
window.__ccon = (function () {
  'use strict';
  var realEval = window.eval;
  var realNow = Date.now;
  var SRC = 'abcd123456-1234abcd';
  var OTHER = 'wxyz987654-9876wxyz';
  var MODEL = 'RTX 0000';
  var H1 = 'wxyz000001';
  var clock = 1700000000000;
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
    function busy() { return window.__cfx && window.__cfx.busy(); }
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
    // get: 'keep' keeps the getter; otherwise it is denied unless a fixture handler runs. set: a function (element,
    // value) that says whether this write is one of the script's allowed effects and logs it; without one every write
    // is denied unless a fixture handler runs. The trap goes where the property is defined along the prototype chain.
    function accessor(start, name, label, get, set) {
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
            if ((busy() || (set && set(this, v))) && own.set) return own.set.call(this, v);
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
    accessor(HTMLInputElement.prototype, 'checked', 'input checked', 'keep');
    accessor(Element.prototype, 'scrollLeft', 'scrollLeft', 'keep');
    // The one write the script may make: the scroll position of the host table's body.
    accessor(Element.prototype, 'scrollTop', 'scrollTop of something other than the host table\'s body', 'keep', function (el) {
      var ok = !!el.matches && el.matches('.machine-table .el-table__body-wrapper');
      if (ok) effects.push({ kind: 'scroll', el: el });
      return ok;
    });
    [[HTMLImageElement, ['src', 'srcset']], [HTMLScriptElement, ['src']], [HTMLLinkElement, ['href']],
     [HTMLAnchorElement, ['href', 'ping']], [HTMLAreaElement, ['href', 'ping']], [HTMLIFrameElement, ['src', 'srcdoc']],
     [HTMLMediaElement, ['src']], [HTMLSourceElement, ['src', 'srcset']], [HTMLFormElement, ['action']],
     [HTMLInputElement, ['formAction']], [HTMLButtonElement, ['formAction']], [HTMLObjectElement, ['data']],
     [HTMLEmbedElement, ['src']], [HTMLTextAreaElement, ['value']], [HTMLSelectElement, ['value']]]
      .forEach(function (pair) { pair[1].forEach(function (n) { accessor(pair[0].prototype, n, pair[0].name + '.' + n); }); });
    // The other effects the script may have: click a button, the label of a checkbox or of a radio button, or the
    // circle of a host's radio (not the radio's label: on the console most of it lies under the next column), and
    // focus an input without scrolling (and send it a focus event once it is the active element). Anything else
    // through these entries is a hit. Each effect let through is logged with its element, and attempt() compares the
    // log with the effects the call should have had.
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
    only(HTMLElement.prototype, 'click', 'click on something other than a button, the label of a box or the circle of a radio', function (el) {
      return el.tagName === 'BUTTON' ||
             (el.tagName === 'LABEL' && (el.classList.contains('el-checkbox') || el.classList.contains('el-radio-button'))) ||
             (el.tagName === 'SPAN' && el.classList.contains('el-radio__input') && !!el.parentElement &&
              el.parentElement.matches('.machine-table .el-table__body-wrapper label.el-radio'));
    }, function () { return 'click'; });
    only(HTMLElement.prototype, 'focus', 'focus on something other than an input, or without preventScroll', function (el, a) {
      return el.tagName === 'INPUT' && !!a[0] && a[0].preventScroll === true;
    }, function () { return 'focus'; });
    only(HTMLElement.prototype, 'blur', 'blur', function () { return false; }, null);
    only(EventTarget.prototype, 'dispatchEvent', 'an event other than a focus on the active input', function (el, a) {
      var e = a[0];
      return e instanceof FocusEvent && e.type === 'focus' && el.tagName === 'INPUT' && document.activeElement === el;
    }, function (a) { return 'event:' + a[0].type; });
    [['setTimeout', window.setTimeout], ['setInterval', window.setInterval]].forEach(function (t) {
      window[t[0]] = function (f) {
        if (typeof f === 'string') { hits.push(t[0] + '(string)'); throw new Error('forbidden: ' + t[0] + '(string)'); }
        return t[1].apply(window, arguments);
      };
      undo.push(function () { window[t[0]] = t[1]; });
    });
    Date.now = function () { return clock; };
    undo.push(function () { Date.now = realNow; });
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
    var v = __cfx.violations();
    check(label + ': touches no forbidden entry', got.length === 0, got.join(', '));
    check(label + ': changes nothing in the page itself', v.length === 0, v.length + ' mutation records');
    var j = (JSON.stringify(out) || '') + (err ? ' ' + err.message : '');
    check(label + ': no decoy in the result', j.indexOf('DECOY') < 0, j.slice(0, 300));
    var did = JSON.stringify(effects.map(function (e) { return e.kind + ' ' + __cfx.describe(e.el); }));
    var want = JSON.stringify(calls.length === 1 ? expected(calls[0], out, err) : []);
    check(label + ': exactly the expected effects', did === want, did + ' instead of ' + want);
    return { out: out, err: err };
  }
  // The effects a call should have had, from the method, its arguments and what it answered: none when it was refused
  // or threw; a focus is followed by a focus event only while the page has no focus of its own.
  function expected(c, out, err) {
    if (err || !out || out.ok !== true) return [];
    switch (c.m) {
      case 'tickModel': return ['click model:' + c.args[1]];
      case 'pickCount': return out.already ? [] : ['click count:' + c.args[1]];
      case 'loadMoreHosts': return ['scroll table:body'];
      case 'pickHost': return out.already ? [] : (out.scrolled ? ['scroll table:body'] : []).concat(['click host:' + c.args[1]]);
      case 'focusExpansion': return ['focus input:expansion'].concat(c.focused ? [] : ['event:focus input:expansion']);
      case 'confirmCreate': return ['click button:创建并开机'];
      case 'leave': return ['click button:取消'];
      default: return [];
    }
  }
  // The API as the tests use it: every method call is noted for expected().
  function wrap(raw) {
    if (!raw || typeof raw !== 'object') return raw;
    var api = {};
    Object.keys(raw).forEach(function (m) {
      if (typeof raw[m] !== 'function') { api[m] = raw[m]; return; }
      api[m] = function () {
        calls.push({ m: m, args: Array.prototype.slice.call(arguments), focused: document.hasFocus() });
        return raw[m].apply(raw, arguments);
      };
    });
    return api;
  }
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
  function clicks(name, want) { eq(name + ': what was clicked', JSON.stringify(__cfx.counts()), JSON.stringify(want)); }
  var make = null;
  // A fresh page: a reload drops window.__autodlClone, where the script registers itself.
  function newApi() {
    delete window.__autodlClone;
    return wrap(call('make the api', function () { return make('offline-test'); }));
  }
  // The page with the source's model ticked by the script: the page then selects wxyz000001, the one suitable host.
  function ready(spec) {
    __cfx.mount(spec);
    var api = newApi();
    isOk('tickModel', call('tickModel', function () { return api.tickModel(SRC, MODEL); }));
    return api;
  }
  function want(more) {
    var w = { host: H1, model: MODEL, gpus: 1, expandGb: 5 };
    Object.keys(more || {}).forEach(function (k) { w[k] = more[k]; });
    return w;
  }
  var COPY = { v: 1, prepared: true, id: SRC, host: H1, model: MODEL, vramGb: 12, gpus: 1, cpu: 10, memGb: 45, cpuModel: 'Fake(R) Gold 0000',
               driver: '580.105.08', cuda: '13.0', billing: 'payg', region: '测试乙区', price: '0.98', expandGb: 5, daily: '0.03',
               systemDiskGb: 30, diskGb: 50 };

  function reads() {
    __cfx.mount();
    var api = newApi();
    eq('version', String(api && api.version), '4');
    eq('the script registers itself', pick(window.__autodlClone, ['brand', 'version', 'mode']),
       JSON.stringify({ brand: 'autodl-autogpu clone-page.js', version: 4, mode: 'offline-test' }));
    eq('the functions of the script, and no other', JSON.stringify(Object.keys(api)),
       JSON.stringify(['brand', 'version', 'mode', 'page', 'tickModel', 'pickCount', 'hosts', 'loadMoreHosts', 'pickHost', 'expansion',
                       'focusExpansion', 'prepareCreate', 'confirmCreate', 'result', 'leave']));
    eq('page()', JSON.stringify(call('page', function () { return api.page(SRC); })), JSON.stringify({
      ok: true, region: '测试乙区', billing: 'payg', expandBytes: 5368709120, expandGb: 5,
      models: [{ name: 'vGPU-00GB', free: 2, total: 16, ticked: false }, { name: MODEL, free: 6, total: 88, ticked: false },
               { name: 'CPU', free: 2, total: 48, ticked: false }], all: false, count: 1 }));
    eq('expansion() as the page came', JSON.stringify(call('expansion', function () { return api.expansion(SRC); })),
       JSON.stringify({ ok: true, need: true, value: '5', gb: 5, now: '5', max: 2266 }));
    isRefused('page(not an id)', call('page(nope)', function () { return api.page('nope'); }), /instance ID/);
    isRefused('page(another instance)', call('page(other)', function () { return api.page(OTHER); }), /address/);
    clicks('reads', {});
  }
  // The gate: every function but result refuses, and clicks nothing, on a page that is not the page that creates a
  // clone of this instance with its data disk, paid by the hour, in the one region that can be chosen, without a coupon.
  var EVERY = [['page'], ['tickModel', MODEL], ['pickCount', 2], ['hosts', 1, 5], ['loadMoreHosts'], ['pickHost', H1], ['expansion'],
               ['focusExpansion'], ['prepareCreate', want()], ['confirmCreate', COPY], ['leave']];
  function allRefuse(label, spec, re, change) {
    __cfx.mount(spec);
    if (change) change();
    var api = newApi();
    EVERY.forEach(function (c) {
      isRefused(c[0] + ' ' + label, call(c[0] + ' ' + label, function () { return api[c[0]].apply(api, [SRC].concat(c.slice(1))); }), re);
    });
    clicks(label, {});
  }
  function gates() {
    var P = 'copy_data_disk=1&expand_size=5368709120';
    allRefuse('on another page of the console', { noWrap: true }, /not the page that creates/);
    allRefuse('with another instance in the address', { search: 'id=' + OTHER + '&' + P }, /address/);
    allRefuse('with two instances in the address', { search: 'id=' + SRC + '&id=' + OTHER + '&' + P }, /address/);
    allRefuse('without the data disk', { search: 'id=' + SRC + '&copy_data_disk=0&expand_size=5368709120' }, /copy_data_disk/);
    allRefuse('with no word about the data disk', { search: 'id=' + SRC + '&expand_size=5368709120' }, /copy_data_disk/);
    allRefuse('with an expansion that is no whole GB', { search: 'id=' + SRC + '&copy_data_disk=1&expand_size=123' }, /expand_size/);
    allRefuse('with two expansions', { search: 'id=' + SRC + '&' + P + '&expand_size=0' }, /expand_size/);
    allRefuse('with no expansion in the address', { search: 'id=' + SRC + '&copy_data_disk=1' }, /expand_size/);
    allRefuse('naming another source', { source: OTHER }, /源实例/);
    allRefuse('with another instance in its image line', { image: OTHER }, /镜像/);
    allRefuse('billed by the day', { billing: 'daily' }, /按量计费/);
    allRefuse('with two regions to choose from', { regions: [['测试甲区', 'testDC1', ''], ['测试乙区', 'testDC2', 'active']] }, /选择地区/);
    allRefuse('with no region selected', { regions: [['测试甲区', 'testDC1', 'disabled'], ['测试乙区', 'testDC2', '']] }, /选择地区/);
    allRefuse('with a zone that can be chosen', { zones: [['测试专区', 'zone1', '']] }, /选择地区/);
    allRefuse('with a coupon chosen', { coupon: 'DECOY-COUPON' }, /优惠券/);
    allRefuse('with a box showing', {}, /dialog is showing/, function () { __cfx.box('DECOY-QUESTION'); });
  }
  // tickModel ticks the one model, once, on a page where none is ticked; pickCount chooses the GPU count.
  function filters() {
    __cfx.mount();
    var api = newApi();
    isRefused('tickModel of a model the page does not have', call('tickModel(unknown)', function () { return api.tickModel(SRC, 'RTX 9999'); }),
              /no single model/);
    isRefused('tickModel(全部)', call('tickModel(全部)', function () { return api.tickModel(SRC, '全部'); }), /no single model/);
    isRefused('tickModel without a model', call('tickModel()', function () { return api.tickModel(SRC); }), /no single model/);
    isOk('tickModel', call('tickModel', function () { return api.tickModel(SRC, MODEL); }));
    eq('the page after the tick', pick(__cfx.state(), ['ticked', 'selected']), JSON.stringify({ ticked: [MODEL], selected: H1 }));
    var p = call('page', function () { return api.page(SRC); }) || {};
    eq('page() sees the tick', JSON.stringify((p.models || []).map(function (m) { return m.ticked; })), '[false,true,false]');
    isRefused('tickModel twice', call('tickModel again', function () { return api.tickModel(SRC, MODEL); }), /already ticked/);
    isRefused('tickModel of another model once one is ticked', call('tickModel(other)', function () { return api.tickModel(SRC, 'vGPU-00GB'); }),
              /already ticked/);
    clicks('tick a model', { 'model:RTX 0000': 1 });
    eq('pickCount of the count already chosen', pick(call('pickCount(1)', function () { return api.pickCount(SRC, 1); }), ['ok', 'already']),
       '{"ok":true,"already":true}');
    isOk('pickCount(2)', call('pickCount(2)', function () { return api.pickCount(SRC, 2); }));
    eq('the page after the count', pick(__cfx.state(), ['count']), '{"count":2}');
    isRefused('pickCount of a count the page does not offer', call('pickCount(9)', function () { return api.pickCount(SRC, 9); }), /no GPU count/);
    isRefused('pickCount of a text', call('pickCount(text)', function () { return api.pickCount(SRC, '4'); }), /no GPU count/);
    clicks('choose a count', { 'model:RTX 0000': 1, 'count:2': 1 });
    __cfx.mount({ all: true });
    api = newApi();
    isRefused('tickModel while 全部 is ticked', call('tickModel', function () { return api.tickModel(SRC, MODEL); }), /全部/);
    __cfx.mount({ ticked: [MODEL] });
    api = newApi();
    isRefused('tickModel of a model that came ticked', call('tickModel', function () { return api.tickModel(SRC, MODEL); }), /already ticked/);
    __cfx.mount({ shift: true });
    api = newApi();
    isRefused('tickModel out of the viewport', call('tickModel', function () { return api.tickModel(SRC, MODEL); }), /viewport/);
    isRefused('pickCount out of the viewport', call('pickCount', function () { return api.pickCount(SRC, 2); }), /viewport/);
    isRefused('leave out of the viewport', call('leave', function () { return api.leave(SRC); }), /viewport/);
    __cfx.mount();
    api = newApi();
    __cfx.coverCorner(__cfx.label('model:' + MODEL));
    isRefused('tickModel of a label partly covered', call('tickModel', function () { return api.tickModel(SRC, MODEL); }), /covered/);
    clicks('filters refused', {});
  }
  // hosts reads every row loaded, says whether those are all, finds the source's own host and judges each host by the
  // rules of the design against it.
  var OWN = 'it is the host of the source instance';
  var REASON = { model: 'another model or memory size', free: 'fewer free GPUs than needed', cpu: 'another number of CPU cores or memory per GPU',
                 driver: 'an older driver or a lower CUDA limit', price: 'a higher price', expand: 'too little room to expand the data disk',
                 radio: 'its radio is disabled' };
  function hostTable() {
    var api = ready();
    var t = isOk('hosts', call('hosts', function () { return api.hosts(SRC, 1, 5); }));
    eq('ten rows at first, which are not all', pick(t, ['model', 'free', 'total', 'complete']),
       JSON.stringify({ model: MODEL, free: 6, total: 88, complete: false }));
    eq('how many rows', String((t.rows || []).length), '10');
    eq('a row in full', JSON.stringify((t.rows || [])[0]), JSON.stringify({
      host: H1, alias: '101机', model: MODEL, vramGb: 12, free: 1, total: 8, cpu: 10, memGb: 45, cpuModel: 'Fake(R) Gold 0000',
      diskGb: 50, expandGb: 6998, driver: '580.105.08', cuda: '13.0', price: '0.98', listPrice: '1.03', disabled: false, selected: true }));
    eq('the reference row and what is suitable', pick(t, ['reference', 'referenceFree', 'suitable']),
       JSON.stringify({ reference: 'abcd123456', referenceFree: false, suitable: [H1] }));
    eq('why the others are not', JSON.stringify(t.unsuitable), JSON.stringify([
      { host: 'wxyz000002', why: REASON.driver }, { host: 'wxyz000003', why: REASON.driver }, { host: 'mnop000004', why: REASON.free },
      { host: 'abcd123456', why: OWN }, { host: 'mnop000006', why: REASON.free }, { host: 'mnop000007', why: REASON.free },
      { host: 'mnop000008', why: REASON.free }, { host: 'mnop000009', why: REASON.free }, { host: 'qrst000010', why: REASON.free }]));
    var more = isOk('loadMoreHosts', call('loadMoreHosts', function () { return api.loadMoreHosts(SRC); }));
    eq('loadMoreHosts says how many rows there were', pick(more, ['rows']), '{"rows":10}');
    __cfx.frame();
    t = isOk('hosts once the page loaded more', call('hosts', function () { return api.hosts(SRC, 1, 5); }));
    eq('eleven rows, which are all', pick(t, ['complete']) + (t.rows || []).length, '{"complete":true}11');
    clicks('host table', { 'model:RTX 0000': 1 });
    // one host for each reason
    var H = __cfx.host;
    __cfx.mount({ ticked: [MODEL], loaded: 20, hosts: [
      H('abcd123456', '100机', 0), H(H1, '101机', 1), H('wxyz000002', '102机', 1, { vram: 24 }), H('wxyz000003', '103机', 0),
      H('wxyz000004', '104机', 1, { cpu: 8 }), H('wxyz000005', '105机', 1, { mem: 40 }), H('wxyz000006', '106机', 1, { cpuModel: 'Other(R) 1' }),
      H('wxyz000007', '107机', 1, { driver: '570.9' }), H('wxyz000008', '108机', 1, { cuda: '12.8' }),
      H('wxyz000009', '109机', 1, { price: '0.99' }), H('mnop000010', '110机', 1, { expand: 4 }), H('mnop000011', '111机', 1, { locked: true }),
      H('mnop000012', '112机', 2, { expand: 9000, price: '0.90' }), H('mnop000013', '113机', 2, { expand: 9500 }),
      H('mnop000014', '114机', 3, { driver: '590.1', cuda: '13.2' }), H('mnop000015', '115机', 1, { driver: '595..1' }),
      H('mnop000016', '116机', 1, { driver: '590.1', cuda: '12.8' }), H('mnop000017', '117机', 1, { driver: '580.105.9' }),
      H('mnop000018', '118机', 4, { driver: '590.1', cuda: '13.2', cpuModel: 'Other(R) 2' })] });
    api = newApi();
    t = isOk('hosts with one host for each reason', call('hosts', function () { return api.hosts(SRC, 1, 5); }));
    // a newer driver will do (it runs what the older one ran), an older one will not; another CPU model will do as well
    // (the cores and the memory per GPU must be the same). The hosts that have the very driver and CPU model of the
    // source's host come first whatever the others have free, then those that differ in one of the two, then in both
    eq('the suitable ones, the nearest to the source\'s host first, then most free GPUs, then most room', JSON.stringify(t.suitable),
       JSON.stringify(['mnop000013', 'mnop000012', H1, 'mnop000014', 'wxyz000006', 'mnop000017', 'mnop000018']));
    eq('each reason', JSON.stringify(t.unsuitable), JSON.stringify([
      { host: 'abcd123456', why: OWN }, { host: 'wxyz000002', why: REASON.model }, { host: 'wxyz000003', why: REASON.free },
      { host: 'wxyz000004', why: REASON.cpu }, { host: 'wxyz000005', why: REASON.cpu },
      { host: 'wxyz000007', why: REASON.driver }, { host: 'wxyz000008', why: REASON.driver }, { host: 'wxyz000009', why: REASON.price },
      { host: 'mnop000010', why: REASON.expand }, { host: 'mnop000011', why: REASON.radio },
      { host: 'mnop000015', why: REASON.driver }, { host: 'mnop000016', why: REASON.driver }]));
    eq('all rows are there', pick(t, ['complete', 'referenceFree']), '{"complete":true,"referenceFree":false}');
    // the source's own host
    __cfx.mount({ ticked: [MODEL], hosts: [H(H1, '101机', 1), H('wxyz000002', '102机', 1)] });
    api = newApi();
    t = isOk('hosts without the source\'s host', call('hosts', function () { return api.hosts(SRC, 1, 5); }));
    eq('no reference row, so nothing is judged', pick(t, ['complete', 'reference', 'referenceFree', 'suitable', 'unsuitable']),
       JSON.stringify({ complete: true, reference: null, referenceFree: null, suitable: [], unsuitable: [] }));
    __cfx.mount({ ticked: [MODEL], hosts: [H('abcd123456', '100机', 1), H(H1, '101机', 1)] });
    api = newApi();
    t = isOk('hosts when the source\'s host has a free GPU', call('hosts', function () { return api.hosts(SRC, 1, 5); }));
    eq('the source\'s host is free: power the source on instead', pick(t, ['reference', 'referenceFree', 'suitable']),
       JSON.stringify({ reference: 'abcd123456', referenceFree: true, suitable: [H1] }));
    // two GPUs
    __cfx.mount({ ticked: [MODEL], count: 2, hosts: [H('abcd123456', '100机', 1), H(H1, '101机', 1), H('wxyz000002', '102机', 2)] });
    api = newApi();
    t = isOk('hosts for two GPUs', call('hosts', function () { return api.hosts(SRC, 2, 5); }));
    eq('a host needs two free GPUs', pick(t, ['referenceFree', 'suitable']), JSON.stringify({ referenceFree: false, suitable: ['wxyz000002'] }));
    isRefused('hosts for one GPU while two are chosen', call('hosts(1)', function () { return api.hosts(SRC, 1, 5); }), /GPU count selected/);
    // what cannot be read is not guessed
    __cfx.mount({ ticked: [MODEL], head: ['', '主机ID', '算力型号/显存', '空闲GPU', '每GPU分配', 'CPU型号', '硬盘', '驱动/CUDA'] });
    api = newApi();
    isRefused('hosts with a column missing', call('hosts', function () { return api.hosts(SRC, 1, 5); }), /nine columns/);
    __cfx.mount({ ticked: [MODEL], head: ['', '主机ID', '算力型号/显存', '空闲GPU', '每GPU分配', 'CPU型号', '硬盘', '驱动/CUDA', '价格(包月)'] });
    api = newApi();
    isRefused('hosts with a column renamed', call('hosts', function () { return api.hosts(SRC, 1, 5); }), /nine columns/);
    [['expand', '很多', /row 2.*硬盘/], ['free', '若干', /row 2.*空闲GPU/], ['price', '面议', /row 2.*价格/], ['vram', '不详', /row 2.*算力型号/],
     ['label', 'DECOY-HOST', /row 2.*host/], ['dot', false, /row 2.*host/]].forEach(function (v) {
      var hs = __cfx.defaultHosts();
      hs[2].lie = {};
      hs[2].lie[v[0]] = v[1];
      __cfx.mount({ ticked: [MODEL], hosts: hs });
      var a = newApi();
      isRefused('hosts with a cell that cannot be read (' + v[0] + ')', call('hosts', function () { return a.hosts(SRC, 1, 5); }), v[2]);
    });
    var dup = __cfx.defaultHosts();
    dup[3].id = dup[2].id;
    __cfx.mount({ ticked: [MODEL], hosts: dup });
    api = newApi();
    isRefused('hosts with a host twice', call('hosts', function () { return api.hosts(SRC, 1, 5); }), /twice/);
    __cfx.mount({ ticked: [MODEL], loaded: 20, modelNote: { 'RTX 0000': '(6/96)' } });
    api = newApi();
    eq('totals that do not add up: not all rows', pick(call('hosts', function () { return api.hosts(SRC, 1, 5); }), ['ok', 'complete']),
       '{"ok":true,"complete":false}');
    __cfx.mount({ ticked: [MODEL], modelNote: { 'RTX 0000': '若干' } });
    api = newApi();
    isRefused('hosts when the model\'s count cannot be read', call('hosts', function () { return api.hosts(SRC, 1, 5); }), /no single model|count of/);
    // the arguments
    api = ready();
    isRefused('hosts with another expansion than the address', call('hosts(4 GB)', function () { return api.hosts(SRC, 1, 4); }),
              /does not agree with the address/);
    [[0, 5], [1.5, 5], ['1', 5], [1, -1], [1, 2.5], [1, undefined]].forEach(function (v) {
      isRefused('hosts(' + JSON.stringify(v) + ')', call('hosts(bad)', function () { return api.hosts(SRC, v[0], v[1]); }), /whole number/);
    });
    __cfx.mount();
    api = newApi();
    isRefused('hosts before a model is ticked', call('hosts', function () { return api.hosts(SRC, 1, 5); }), /one model has to be ticked/);
    __cfx.mount({ ticked: [MODEL, 'vGPU-00GB'] });
    api = newApi();
    isRefused('hosts with two models ticked', call('hosts', function () { return api.hosts(SRC, 1, 5); }), /one model has to be ticked/);
    // the table still loading
    __cfx.mount({ tableDelay: 'later' });
    api = newApi();
    isOk('tickModel', call('tickModel', function () { return api.tickModel(SRC, MODEL); }));
    isRefused('hosts while the table loads', call('hosts', function () { return api.hosts(SRC, 1, 5); }), /loading/);
    isRefused('pickHost while the table loads', call('pickHost', function () { return api.pickHost(SRC, H1); }), /loading/);
    __cfx.tick();
    isOk('hosts once it loaded', call('hosts', function () { return api.hosts(SRC, 1, 5); }));
  }
  // pickHost clicks the radio of a host that can be selected and is not selected yet, after scrolling the table's body
  // when the row is not in view.
  function pickHosts() {
    var api = ready();
    // As on the console, the first column shows only the circle of a radio: the middle of the radio's label, where the
    // host ID would be, lies under the next column.
    var lab = document.querySelector('.machine-table .el-table__body-wrapper tr.el-table__row label.el-radio');
    var box = lab.getBoundingClientRect();
    var mid = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    var dot = lab.querySelector('.el-radio__input').getBoundingClientRect();
    var onDot = document.elementFromPoint(dot.left + dot.width / 2, dot.top + dot.height / 2);
    check('the test page cuts the host ID off next to a radio, as the console does', !!mid && !lab.contains(mid) && lab.contains(onDot),
          (mid ? mid.tagName : 'nothing') + ' / ' + (onDot ? onDot.tagName : 'nothing'));
    eq('pickHost of the host the page selected by itself', pick(call('pickHost(selected)', function () { return api.pickHost(SRC, H1); }),
       ['ok', 'already']), '{"ok":true,"already":true}');
    var r = isOk('pickHost of the next row', call('pickHost', function () { return api.pickHost(SRC, 'wxyz000002'); }));
    check('a row in view needs no scrolling', !r.scrolled, JSON.stringify(r));
    eq('the page after it', pick(__cfx.state(), ['selected']), '{"selected":"wxyz000002"}');
    isRefused('pickHost of a full host', call('pickHost(full)', function () { return api.pickHost(SRC, 'mnop000004'); }), /cannot be selected/);
    isRefused('pickHost of a host that is not loaded', call('pickHost(far)', function () { return api.pickHost(SRC, 'qrst000011'); }),
              /no row for this host/);
    isRefused('pickHost of something that is no host ID', call('pickHost(nope)', function () { return api.pickHost(SRC, 'nope'); }), /host ID/);
    clicks('pick a host', { 'model:RTX 0000': 1, 'host:wxyz000002': 1 });
    var hs = __cfx.defaultHosts();
    hs[9].free = 1;
    __cfx.mount({ ticked: [MODEL], hosts: hs });
    api = newApi();
    r = isOk('pickHost of a row below the part in view', call('pickHost', function () { return api.pickHost(SRC, 'mnop000009'); }));
    check('the table was scrolled first', r.scrolled === true, JSON.stringify(r));
    eq('the page after it', pick(__cfx.state(), ['selected']), '{"selected":"mnop000009"}');
    clicks('pick a host out of view', { 'host:mnop000009': 1 });
  }
  // The expansion: read, and focused for the browser tool to type the number.
  function expansions() {
    var api = ready();
    var E = function (label) { return JSON.stringify(call(label, function () { return api.expansion(SRC); })); };
    eq('expansion() with the suitable host selected', E('expansion'), JSON.stringify({ ok: true, need: true, value: '5', gb: 5, now: '5', max: 6998 }));
    isOk('focusExpansion', call('focusExpansion', function () { return api.focusExpansion(SRC); }));
    check('the input has the focus', document.activeElement === __cfx.input(), String(document.activeElement && document.activeElement.tagName));
    __cfx.type('6');
    eq('typed, before Enter', E('expansion'), JSON.stringify({ ok: true, need: true, value: '6', gb: 6, now: '5', max: 6998 }));
    __cfx.enter();
    eq('after Enter', E('expansion'), JSON.stringify({ ok: true, need: true, value: '6', gb: 6, now: '6', max: 6998 }));
    __cfx.type('abc');
    eq('something that is no number', E('expansion'), JSON.stringify({ ok: true, need: true, value: 'abc', gb: null, now: '6', max: 6998 }));
    clicks('expansion', { 'model:RTX 0000': 1 });
    api = ready({ expandGb: 0 });
    eq('a source without paid expansion: the input is empty', E('expansion'),
       JSON.stringify({ ok: true, need: true, value: '', gb: 0, now: null, max: 6998 }));
    api = ready({ need: false });
    eq('需要扩容 not ticked', pick(call('expansion', function () { return api.expansion(SRC); }), ['ok', 'need']), '{"ok":true,"need":false}');
    isRefused('focusExpansion while 需要扩容 is not ticked', call('focusExpansion', function () { return api.focusExpansion(SRC); }), /需要扩容/);
    api = ready();
    __cfx.coverCorner(__cfx.input());
    isRefused('focusExpansion on an input partly covered', call('focusExpansion', function () { return api.focusExpansion(SRC); }), /covered/);
    clicks('expansion refused', { 'model:RTX 0000': 1 });
  }
  // prepareCreate checks all that is about to be created against what was wanted and against the page itself, and
  // returns the prepared copy. Nothing is clicked.
  function prep(api, w, label) { return call(label || 'prepareCreate', function () { return api.prepareCreate(SRC, w); }); }
  function prepares() {
    var api = ready();
    var p = isOk('prepareCreate', prep(api, want()));
    eq('the prepared copy', JSON.stringify(p.copy), JSON.stringify(COPY));
    eq('what prepareCreate answers', JSON.stringify(Object.keys(p)), '["ok","stage","age","copy"]');
    eq('no time has passed since the tick', pick(p, ['stage', 'age']), '{"stage":"prepared","age":0}');
    clock += 61500;
    eq('a minute later it says so', pick(prep(api, want()), ['age']), '{"age":61}');
    [[{ host: 'nope' }, 'a host that is no host ID'], [{ model: '' }, 'no model'], [{ gpus: 0 }, 'no GPU'], [{ gpus: '1' }, 'a count as text'],
     [{ expandGb: -1 }, 'a negative expansion'], [{ expandGb: 2.5 }, 'half a GB']].forEach(function (v) {
      isRefused('prepareCreate with ' + v[1], prep(api, want(v[0])), /want must be/);
    });
    isRefused('prepareCreate with nothing', prep(api, undefined), /want must be/);
    isRefused('prepareCreate for another host than the one selected', prep(api, want({ host: 'wxyz000002' })), /host selected/);
    isRefused('prepareCreate for another model than the one ticked', prep(api, want({ model: 'vGPU-00GB' })), /model ticked/);
    isRefused('prepareCreate for another GPU count', prep(api, want({ gpus: 2 })), /GPU count selected/);
    isRefused('prepareCreate with another expansion than the source has', prep(api, want({ expandGb: 6 })), /does not agree with the address/);
    isOk('pickHost of a host with an older driver', call('pickHost', function () { return api.pickHost(SRC, 'wxyz000002'); }));
    isRefused('prepareCreate for a host that is not suitable', prep(api, want({ host: 'wxyz000002' })), /not suitable now.*driver/);
    clicks('prepare', { 'model:RTX 0000': 1, 'host:wxyz000002': 1 });
    // the expansion
    api = ready();
    __cfx.type('6');
    isRefused('prepareCreate with a number typed and not entered', prep(api, want()), /expansion in the input/);
    __cfx.enter();
    isRefused('prepareCreate with another expansion entered', prep(api, want()), /expansion in the input/);
    __cfx.tick();
    __cfx.type('5');
    __cfx.enter();
    isRefused('prepareCreate while the daily fee still shows the old expansion', prep(api, want()), /daily fee/);
    __cfx.tick();
    isOk('prepareCreate once the daily fee followed', prep(api, want()));
    api = ready({ need: false });
    isRefused('prepareCreate while 需要扩容 is not ticked', prep(api, want()), /需要扩容/);
    api = ready({ expandGb: 0 });
    p = isOk('prepareCreate for a source without paid expansion', prep(api, want({ expandGb: 0 })));
    eq('its copy', pick(p.copy, ['expandGb', 'daily']), '{"expandGb":0,"daily":"0.00"}');
    api = ready({ expandGb: 0, inputText: '0', nowText: '0' });
    isOk('prepareCreate with a zero in the input', prep(api, want({ expandGb: 0 })));
    api = ready({ expandGb: 0, nowText: 'undefined' });
    isOk('prepareCreate with an empty input whose value reads undefined', prep(api, want({ expandGb: 0 })));
    api = ready({ expandGb: 0, inputText: '3' });
    isRefused('prepareCreate with a number in the input the source does not have', prep(api, want({ expandGb: 0 })), /expansion in the input/);
    // the daily fee: 0.0066 yuan per GB, to the fen; exactly half a fen may show either way
    api = ready({ expandGb: 25 });
    eq('25 GB, the half fen up', pick(prep(api, want({ expandGb: 25 })).copy, ['daily']), '{"daily":"0.17"}');
    api = ready({ expandGb: 25, halfUp: false });
    eq('25 GB, the half fen down', pick(prep(api, want({ expandGb: 25 })).copy, ['daily']), '{"daily":"0.16"}');
    api = ready({ expandGb: 25, daily: '0.18' });
    isRefused('25 GB with a fee that is neither', prep(api, want({ expandGb: 25 })), /daily fee/);
    api = ready({ daily: '0.04' });
    isRefused('5 GB with the fee of 6', prep(api, want()), /daily fee/);
    // the spec and the config fee
    [[{ gpu: 'RTX 0000 * 2卡' }, /spec shown.*GPU型号/], [{ cpu: '12核心' }, /spec shown.*CPU/], [{ mem: '62GB' }, /spec shown.*内存/],
     [{ disk: '免费50GB SSD ，付费6GB' }, /spec shown.*数据盘/], [{ disk: '免费50GB SSD' }, /spec shown.*数据盘/]].forEach(function (v) {
      var a = ready({ specLie: v[0] });
      isRefused('prepareCreate with a spec that says ' + JSON.stringify(v[0]), prep(a, want()), v[1]);
    });
    api = ready({ feeUnit: '/日' });
    isRefused('prepareCreate with a config fee per day', prep(api, want()), /config fee/);
    api = ready({ configLie: '1.03' });
    isRefused('prepareCreate with another config fee than the price', prep(api, want()), /config fee/);
    api = ready({ buttons: ['取消', '创建并开机', '保存'] });
    isRefused('prepareCreate with a third button', prep(api, want()), /bottom of the page/);
    api = ready({ buttons: ['取消', '立即创建'] });
    isRefused('prepareCreate with another label on the button', prep(api, want()), /bottom of the page/);
    // the source's host has to be among the rows read
    var hs = __cfx.defaultHosts();
    hs.push(hs.splice(5, 1)[0]);
    api = ready({ hosts: hs });
    isRefused('prepareCreate before the source\'s host is loaded', prep(api, want()), /not among the rows loaded/);
    isOk('loadMoreHosts', call('loadMoreHosts', function () { return api.loadMoreHosts(SRC); }));
    __cfx.frame();
    isOk('prepareCreate once it is', prep(api, want()));
    // two GPUs
    var H = __cfx.host;
    __cfx.mount({ hosts: [H('abcd123456', '100机', 0), H(H1, '101机', 2)] });
    api = newApi();
    isOk('tickModel', call('tickModel', function () { return api.tickModel(SRC, MODEL); }));
    isOk('pickCount(2)', call('pickCount(2)', function () { return api.pickCount(SRC, 2); }));
    p = isOk('prepareCreate for two GPUs', prep(api, want({ gpus: 2 })));
    eq('its copy', pick(p.copy, ['gpus', 'cpu', 'memGb', 'price']), '{"gpus":2,"cpu":10,"memGb":45,"price":"0.98"}');
    clicks('prepare for two GPUs', { 'model:RTX 0000': 1, 'count:2': 1 });
  }
  // confirmCreate is the final confirm: the page still agrees with the prepared copy, the model was ticked by this
  // script no more than two minutes ago, and no final confirm was made on this page before. One click, once.
  function conf(api, copy, label) { return call(label || 'confirmCreate', function () { return api.confirmCreate(SRC, copy); }); }
  function prepared(spec) {
    var api = ready(spec);
    return { api: api, copy: isOk('prepareCreate', prep(api, want())).copy };
  }
  function confirms() {
    var o = prepared();
    clock += 120000;
    eq('confirmCreate two minutes after the tick', pick(conf(o.api, o.copy), ['ok', 'stage']), '{"ok":true,"stage":"attempted"}');
    clicks('confirm', { 'model:RTX 0000': 1, 'button:创建并开机': 1 });
    isRefused('confirmCreate a second time', conf(o.api, o.copy, 'confirmCreate again'), /already made/);
    isRefused('prepareCreate after the final confirm', prep(o.api, want()), /already made|dialog|not the page/);
    clicks('confirm once', { 'model:RTX 0000': 1, 'button:创建并开机': 1 });
    o = prepared({ outcome: 'nothing' });
    isOk('confirmCreate that the page ignores', conf(o.api, o.copy));
    isRefused('confirmCreate again on the same page', conf(o.api, o.copy, 'confirmCreate again'), /already made/);
    __cfx.mount({ outcome: 'nothing' });
    isOk('tickModel on a page entered again in the same window', call('tickModel', function () { return o.api.tickModel(SRC, MODEL); }));
    isRefused('confirmCreate on a page entered again in the same window', conf(o.api, o.copy), /already made/);
    clicks('no second creation in one window', { 'model:RTX 0000': 1 });
    o = prepared();
    clock += 120001;
    isRefused('confirmCreate later than two minutes after the tick', conf(o.api, o.copy), /two minutes/);
    __cfx.mount({ ticked: [MODEL], selected: H1 });
    var api = newApi();
    var pre = isOk('prepareCreate on a page whose model came ticked', prep(api, want()));
    eq('the age of such a page is not known', pick(pre, ['age']), '{"age":null}');
    var c = pre.copy;
    isRefused('confirmCreate when this script did not tick the model', conf(api, c), /not ticked by this script/);
    o = prepared();
    [['price', '0.90'], ['daily', '0.04'], ['driver', '595.71.05'], ['cpu', 12], ['region', '测试甲区'], ['billing', 'daily'], ['vramGb', 24],
     ['systemDiskGb', 50]].forEach(function (v) {
      var forged = JSON.parse(JSON.stringify(o.copy));
      forged[v[0]] = v[1];
      isRefused('confirmCreate with a copy whose ' + v[0] + ' was changed', conf(o.api, forged), /no longer agrees.*\(/);
    });
    var un = JSON.parse(JSON.stringify(o.copy));
    un.prepared = false;
    isRefused('confirmCreate with a copy not marked prepared', conf(o.api, un), /prepared copy/);
    isRefused('confirmCreate with nothing', conf(o.api, undefined), /prepared copy/);
    var other = JSON.parse(JSON.stringify(o.copy));
    other.id = OTHER;
    isRefused('confirmCreate with the copy of another source', conf(o.api, other), /prepared copy/);
    __cfx.setHost(H1, { price: '1.08' });
    isRefused('confirmCreate after the price went up', conf(o.api, o.copy), /not suitable now.*price/);
    o = prepared();
    __cfx.setHost(H1, { price: '0.95' });
    isRefused('confirmCreate after the price went down', conf(o.api, o.copy), /no longer agrees.*price/);
    o = prepared();
    __cfx.set({ selected: 'wxyz000002' });
    isRefused('confirmCreate after the page selected another host', conf(o.api, o.copy), /host selected/);
    o = prepared();
    __cfx.set({ billing: 'monthly' });
    isRefused('confirmCreate after the billing changed', conf(o.api, o.copy), /按量计费/);
    o = prepared();
    __cfx.coverCorner(__cfx.button('创建并开机'));
    isRefused('confirmCreate with the button partly covered', conf(o.api, o.copy), /covered/);
    clicks('confirm refused', { 'model:RTX 0000': 1 });
  }
  // result reads what the final confirm led to, on whatever page of the console, and clicks nothing: not even a box
  // that asks to confirm, whose words nobody has seen yet.
  var RES = ['ok', 'create', 'confirmed', 'prompts', 'dialogs', 'ids'];
  function res(api, label) { return call(label || 'result', function () { return api.result(SRC); }); }
  function results() {
    var o = prepared();
    var r0 = res(o.api);
    eq('result before the final confirm', pick(r0, RES),
       JSON.stringify({ ok: true, create: true, confirmed: false, prompts: [], dialogs: [], ids: [] }));
    eq('what result answers', JSON.stringify(Object.keys(r0 || {})), '["ok","path","create","confirmed","prompts","dialogs","ids"]');
    check('the path is that of the page', !!r0 && r0.path === location.pathname, r0 && r0.path);
    isOk('confirmCreate', conf(o.api, o.copy));
    eq('a success prompt, still on the page', pick(res(o.api), RES),
       JSON.stringify({ ok: true, create: true, confirmed: true, prompts: [{ kind: 'success', text: '创建成功' }], dialogs: [], ids: [] }));
    __cfx.tick();
    eq('then the console went on to its list', pick(res(o.api), ['ok', 'create', 'confirmed']), '{"ok":true,"create":false,"confirmed":true}');
    o = prepared({ outcomeText: '创建成功，实例ID：wxyz000001-9999wxyz，源实例 ' + SRC });
    isOk('confirmCreate', conf(o.api, o.copy));
    eq('a prompt that names the new instance', pick(res(o.api), ['ids']), '{"ids":["wxyz000001-9999wxyz"]}');
    o = prepared({ outcome: 'error' });
    isOk('confirmCreate', conf(o.api, o.copy));
    eq('an error prompt', pick(res(o.api), ['create', 'prompts', 'dialogs']),
       JSON.stringify({ create: true, prompts: [{ kind: 'error', text: '创建失败：所选主机的GPU已被占用' }], dialogs: [] }));
    o = prepared({ outcome: 'box' });
    isOk('confirmCreate', conf(o.api, o.copy));
    eq('a box that asks again: read, not clicked', pick(res(o.api), ['create', 'prompts', 'dialogs']),
       JSON.stringify({ create: true, prompts: [], dialogs: [{ kind: 'box', text: '提示 确认创建并开机吗？ 取消 确定', buttons: ['取消', '确定'] }] }));
    isRefused('leave while that box shows', call('leave', function () { return o.api.leave(SRC); }), /dialog is showing/);
    isRefused('confirmCreate while that box shows', conf(o.api, o.copy), /already made|dialog is showing/);
    clicks('a box nobody knows', { 'model:RTX 0000': 1, 'button:创建并开机': 1 });
    o = prepared({ outcome: 'nothing' });
    isOk('confirmCreate', conf(o.api, o.copy));
    eq('no reaction at all', pick(res(o.api), ['create', 'confirmed', 'prompts', 'dialogs']),
       JSON.stringify({ create: true, confirmed: true, prompts: [], dialogs: [] }));
    __cfx.mount({ noWrap: true });
    var api = newApi();
    eq('result on another page of the console', pick(res(api), RES),
       JSON.stringify({ ok: true, create: false, confirmed: false, prompts: [], dialogs: [], ids: [] }));
    isRefused('result(not an id)', call('result(nope)', function () { return api.result('nope'); }), /instance ID/);
  }
  // leave clicks the page's 取消; the console then shows its market page.
  function leaves() {
    var api = ready();
    isOk('leave', call('leave', function () { return api.leave(SRC); }));
    clicks('leave', { 'model:RTX 0000': 1, 'button:取消': 1 });
    isRefused('page() after leaving', call('page', function () { return api.page(SRC); }), /address|not the page/);
    api = ready({ buttons: ['返回', '创建并开机'] });
    isRefused('leave without a 取消', call('leave', function () { return api.leave(SRC); }), /取消/);
  }
  // What the script has no function for stays untouched through a whole run: the billing mode, the region, the coupon,
  // 全部, 需要扩容. And the script lives once per page.
  function untouched() {
    var o = prepared();
    isOk('hosts', call('hosts', function () { return o.api.hosts(SRC, 1, 5); }));
    isOk('loadMoreHosts', call('loadMoreHosts', function () { return o.api.loadMoreHosts(SRC); }));
    isOk('focusExpansion', call('focusExpansion', function () { return o.api.focusExpansion(SRC); }));
    isOk('confirmCreate', conf(o.api, o.copy));
    clicks('a whole run', { 'model:RTX 0000': 1, 'button:创建并开机': 1 });
    eq('the page\'s choices are as they came', pick(__cfx.state(), ['all', 'count', 'billing', 'need', 'expand']),
       '{"all":false,"count":1,"billing":"payg","need":true,"expand":5}');
    __cfx.mount();
    delete window.__autodlClone;
    var first = call('make the api', function () { return make('offline-test'); });
    var second = call('run the script again in the same page', function () { return make('offline-test'); });
    check('the second run returns the registered API', !!first && second === first && window.__autodlClone === first, typeof second);
    window.__autodlClone = { brand: 'someone else' };
    var taken = call('run the script with window.__autodlClone taken', function () { return make('offline-test'); });
    isRefused('page() with window.__autodlClone taken', call('page', function () { return taken.page(SRC); }), /reload/);
    isRefused('confirmCreate with window.__autodlClone taken', call('confirmCreate', function () { return taken.confirmCreate(SRC, COPY); }), /reload/);
    delete window.__autodlClone;
    var prod = call('make the api without test mode', function () { return make(); });
    isRefused('page() without test mode', call('page', function () { return prod.page(SRC); }), /instance ID|not the page/);
    isRefused('result() without test mode', call('result', function () { return prod.result(SRC); }), /instance ID|not a page/);
    delete window.__autodlClone;
    var odd = call('make the api with an unknown mode', function () { return make('x'); });
    check('an unknown mode does not register', window.__autodlClone === undefined, typeof window.__autodlClone);
    isRefused('page() with an unknown mode', call('page', function () { return odd.page(SRC); }), /unknown mode/);
    clicks('shells', {});
  }

  var SCENARIOS = [reads, gates, filters, hostTable, pickHosts, expansions, prepares, confirms, results, leaves, untouched];
  async function run(fn) {
    var evalHits = trapsOff();
    checks = [];
    clock = 1700000000000;
    check('evaluating the pasted source touched nothing', evalHits.length === 0, evalHits.join(', '));
    check('the pasted source is a function', typeof fn === 'function', typeof fn);
    if (typeof fn !== 'function') {
      return JSON.stringify({ pass: false, total: checks.length, failed: checks.filter(function (c) { return !c.ok; }) });
    }
    var digest = await sha256(fn.toString());
    make = fn;
    SCENARIOS.forEach(function (s) {
      try { s(); } catch (e) { check(s.name + ': ran to the end', false, e && e.message); }
    });
    __cfx.mount({ noWrap: true });
    var failed = checks.filter(function (c) { return !c.ok; });
    return JSON.stringify({ pass: failed.length === 0, digest: digest, total: checks.length, failed: failed });
  }
  // Evaluates a source text with the traps on, then run().
  async function runSource(src) {
    trapsOn();
    var fn;
    try { fn = realEval(src); } catch (e) { fn = null; }
    return run(fn);
  }

  return { trapsOn: trapsOn, run: run, runSource: runSource };
})();
