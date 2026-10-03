// reference/console.js: the page script for the AutoDL console's instance list (https://www.autodl.com/console/instance/list).
// The file is one function expression. Paste it as code into the page (var fn = <this file>;), check that it is the
// published file (reference/console.md says how), then call fn(): the API registers itself as window.__autodl, and a
// second call in the same page returns that same API, so the page never has two scripts with separate state. A reload
// or navigation drops the script and its contexts; put it into the page again (reference/console.md, section 2).
// Two copies of this file go into the page, both made by tests/console/paste_copy.py. reference/console.min.js is the
// everyday copy: it leaves out everything between the comment lines clone-begin and clone-end. reference/console-clone.min.js
// keeps it, and is loaded in place of the everyday copy, after a reload, when an instance is to be cloned.
// The script only reads the page, apart from these effects on elements it has just checked: click() on the fixed
// buttons and menu items of its action table; focus() on the timer inputs (plus a focus event when the page has no
// focus of its own); mouseenter or mouseleave on the target row's 更多 trigger; and, in the copy for cloning, click() on
// the clone's menu item, on the label of the data disk's checkbox in the clone dialog and on its 继续. It never writes
// the DOM, navigates, sends requests, or reads cookies or storage. It returns only the fields listed in
// reference/console.md and reference/clone.md and the text of dialogs and prompts.
// What it reads is what the page shows. The one exception is in the copy for cloning: sshAddress reads, in the page's
// own data behind the instance table, three keys of the one object that carries the given instance ID (its SSH host,
// port and command) and returns the host and the port. Of the other objects there it reads only the key that holds
// their ID, and no other key of any object.
// One operation at a time. A start function clicks the row's button; bindDialog binds the dialog that opened and, for a
// confirm box, returns the prepared copy; the timer goes on with openTimerPicker, bindTimerPicker, focusTimerInput,
// readTimer, confirmTimerPicker and prepareTimer, which returns the prepared copy; confirm or confirmTimerDialog is the
// final confirm; settle reads what happened. An operation ends with dismiss or with settle returning confirmed; after
// anything else only a reload of the page lets a new one start. Keep the prepared copy: after a final confirm use only
// settle, with the copy if the page reloaded or the call did not return. A failing check returns {ok: false, refused}
// and clicks nothing.
// Cloning an instance is an operation of its own (reference/clone.md): startClone clicks the menu item, bindCloneDialog
// binds the dialog and reads it again after a tick, tickCloneDataDisk ticks the data disk, continueClone clicks 继续 and
// ends the operation, because the console then leaves the instance list for the page that creates the new instance;
// dismiss gives the clone up. These functions go by a table of fixed texts: they click only an element whose whole
// text is one of three of them, and they bind the dialog only if every text it shows is in the table.
// The offline test is tests/console/run.js; the mode 'offline-test' works only on its test page.
(function (mode) {
  'use strict';
  var VERSION = 9;
  var BRAND = 'autodl-gpu console.js';
  var TEST = mode === 'offline-test';
  var ID_SHAPE = TEST ? /^(abcd|wxyz|mnop|qrst|efgh)\d{6}-\d{4}[a-z]{4}$/ : /^[0-9a-z]{10}-[0-9a-z]{8}$/;
  var modeError = mode !== undefined && !TEST ? 'unknown mode'
    : TEST && !(location.origin === 'https://example.com' || location.protocol === 'file:') ? 'test mode works only on the offline test page'
    : '';
  // Words that must never be on anything this script clicks, nor in the visible text of a dialog it binds or confirms.
  var DENY = ['释放', '重置', '更换镜像', '保存镜像', '升降配置', '扩容', '缩容', '迁移', '转包年包月', '转包月', '克隆', '跨实例拷贝',
              '修改SSH密码', '充值', '续费', '发票'];
  // Confirm-box texts with whitespace collapsed. The 关机 box has not been seen yet, so confirm refuses it until its text is here.
  var BOX = {
    'gpu-on': /^确认开机吗？ ?实例将按￥(\d+(?:\.\d+)?)\/时整点扣费$/,
    'nogpu-on': /^确认无卡模式开机吗？ ?无卡模式配置为:0\.5核心CPU，2GB内存，无GPU卡 ?配置费用:￥(\d+(?:\.\d+)?)\/时，最低消费￥0\.01$/,
    'cancel-timer': /^取消定时关机$/
  };
  var OPS = ['gpu-on', 'nogpu-on', 'cancel-timer', 'timer', 'power-off'];
  var BOOTING = ['开机中', '运行中'];
  var FULL_TIME = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/;
  var MINUTE_TIME = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/;
  var SKIP = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1 };
  var nonce = Math.random().toString(36).slice(2, 10);
  var seq = 0;
  var contexts = {};
  var active = null;

  function no(why) { return { ok: false, refused: why }; }
  function norm(s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); }
  function textOf(el) { return norm(el.textContent); }
  function ownText(el) {
    var s = '';
    for (var n = el.firstChild; n; n = n.nextSibling) if (n.nodeType === 3) s += n.nodeValue;
    return norm(s);
  }
  function list(nodes) { return Array.prototype.slice.call(nodes); }
  function shown(el) {
    return !!el && el.isConnected && el.getClientRects().length > 0 && getComputedStyle(el).visibility === 'visible';
  }
  // '' when el and its ancestors are displayed, fully opaque and not hidden; otherwise why not. A dialog or menu whose
  // opening animation has not run (the page draws no frames while its pane is hidden) is not fully opaque.
  function settled(el) {
    for (var n = el; n && n.nodeType === 1; n = n.parentElement) {
      var cs = getComputedStyle(n);
      if (cs.display === 'none') return 'is hidden';
      if (parseFloat(cs.opacity) < 1) return 'is still animating (not fully opaque)';
      if (n.hasAttribute('hidden') || n.hasAttribute('inert') || n.getAttribute('aria-hidden') === 'true') return 'is hidden';
    }
    return getComputedStyle(el).visibility === 'visible' ? '' : 'is hidden';
  }
  // '' when a click on el lands on it: settled, enabled, inside the viewport, and the topmost element at its center
  // and at four more sample points.
  function interactable(el) {
    if (!el || !el.isConnected) return 'is not in the page';
    var why = settled(el);
    if (why) return why;
    if (getComputedStyle(el).pointerEvents === 'none') return 'takes no pointer events';
    if (el.disabled === true || el.classList.contains('is-disabled') || el.closest('[aria-disabled="true"]')) return 'is disabled';
    var r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return 'has no size';
    var w = document.documentElement.clientWidth;
    var h = document.documentElement.clientHeight;
    var pts = [[0.5, 0.5], [0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]];
    for (var i = 0; i < pts.length; i++) {
      var x = r.left + r.width * pts[i][0];
      var y = r.top + r.height * pts[i][1];
      if (x < 0 || y < 0 || x >= w || y >= h) return 'is outside the viewport';
      var hit = document.elementFromPoint(x, y);
      if (!hit || !(hit === el || el.contains(hit))) return 'is covered by another element';
    }
    return '';
  }
  // The first word of the refusal list that text holds, or ''. deny throws with that word only, never the page's text.
  function denyWord(text) {
    for (var i = 0; i < DENY.length; i++) if (String(text).indexOf(DENY[i]) >= 0) return DENY[i];
    return '';
  }
  function deny(text) {
    var w = denyWord(text);
    if (w) throw new Error('refusal list: ' + w);
  }
  // The only effects: a click, a focus, and two kinds of event sent to an element already checked.
  function press(el) { deny(textOf(el)); el.click(); }
  function send(el, e) { el.dispatchEvent(e); }
  function hover(el, type) { send(el, new MouseEvent(type, { bubbles: false, cancelable: false, view: window })); }
  // While the page has no focus of its own (its pane is hidden), focus() makes the input the active element but fires
  // no focus event, so the page never hears of it; the focus event is then sent by hand.
  function focusOn(el) {
    el.focus({ preventScroll: true });
    if (document.activeElement === el && !document.hasFocus()) send(el, new FocusEvent('focus'));
  }

  function urlOk() {
    if (TEST) return location.origin === 'https://example.com' || location.protocol === 'file:';
    return location.origin === 'https://www.autodl.com' && /^\/console\/instance\/list\/?$/.test(location.pathname);
  }
  function colClass(th) {
    for (var i = 0; i < th.classList.length; i++) if (/^el-table_\d+_column_\d+$/.test(th.classList[i])) return th.classList[i];
    return '';
  }
  function child(el, cls) {
    for (var i = 0; i < el.children.length; i++) if (el.children[i].classList.contains(cls)) return el.children[i];
    return null;
  }
  // The instance table: the only .el-table whose header has 状态, 释放时间/停机时间 and 操作 once each, in three
  // different columns. Its rows are those of its body, not of fixed-column copies. 规格详情 is read when its header is
  // there once, in a column of its own; otherwise the spec fields of every row are null.
  function instanceTable() {
    var found = [];
    var bad = '';
    list(document.querySelectorAll('.el-table')).forEach(function (t) {
      var hw = child(t, 'el-table__header-wrapper');
      var bw = child(t, 'el-table__body-wrapper');
      if (!hw || !bw) return;
      var cols = {};
      var ok = true;
      [['state', '状态'], ['timer', '释放时间/停机时间'], ['ops', '操作']].forEach(function (p) {
        var ths = list(hw.querySelectorAll('th')).filter(function (th) { return textOf(th) === p[1]; });
        if (ths.length !== 1 || !colClass(ths[0])) ok = false;
        else cols[p[0]] = colClass(ths[0]);
      });
      if (ok && (cols.state === cols.timer || cols.state === cols.ops || cols.timer === cols.ops)) {
        bad = 'the key columns of the instance table are not distinct';
        ok = false;
      }
      if (ok) {
        var sp = list(hw.querySelectorAll('th')).filter(function (th) { return textOf(th) === '规格详情'; });
        var sc = sp.length === 1 ? colClass(sp[0]) : '';
        cols.spec = sc && sc !== cols.state && sc !== cols.timer && sc !== cols.ops ? sc : null;
        found.push({ cols: cols, rows: list(bw.querySelectorAll('tr.el-table__row')) });
      }
    });
    if (found.length !== 1) return { why: found.length ? 'more than one instance table' : bad || 'no instance table' };
    return found[0];
  }
  function page() {
    if (modeError) return { list: false, login: false, why: modeError };
    var login = list(document.querySelectorAll('input[type="password"]')).some(shown) || (!TEST && /\/login/.test(location.pathname));
    if (!urlOk()) return { list: false, login: login, why: 'not the instance list page' };
    if (login) return { list: false, login: true, why: 'the login page is showing; the user must log in' };
    var tb = instanceTable();
    if (tb.why) return { list: false, login: false, why: 'not the instance list page (' + tb.why + ')' };
    return { list: true, login: false };
  }
  function gate() {
    var p = page();
    return p.list ? '' : p.why;
  }

  function cell(tr, cls) {
    var cs = list(tr.cells).filter(function (td) { return td.classList.contains(cls); });
    return cs.length === 1 ? cs[0] : null;
  }
  // The text of shown elements that have text of their own, in page order.
  function leaves(root) {
    return list(root.querySelectorAll('*')).filter(function (el) { return ownText(el) !== '' && shown(el); }).map(ownText);
  }
  // All text a user may see in el, its own text included. Text under aria-hidden or inert still counts: it can be seen.
  function visibleText(el) {
    return [el].concat(list(el.querySelectorAll('*'))).filter(function (x) { return ownText(x) !== '' && shown(x); })
      .map(ownText).join(' ');
  }
  // The time of YYYY-MM-DD HH:MM(:SS) as a number, when it is a real calendar moment; otherwise null. No time zone is
  // involved: both sides of a comparison are read the same way.
  function moment(text) {
    var m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})(?::(\d{2}))?$/.exec(String(text == null ? '' : text));
    if (!m) return null;
    var p = m.slice(1).map(function (x) { return x === undefined ? 0 : Number(x); });
    var t = Date.UTC(p[0], p[1] - 1, p[2], p[3], p[4], p[5]);
    var d = new Date(t);
    var back = [d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate(), d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds()];
    return back.join(',') === p.join(',') ? t : null;
  }
  // What the timer dialog's date editor shows, as the row will show it: the console's editor shows a time to the minute
  // and the row shows it with seconds. Null when it is neither form.
  function fullTime(text) { return FULL_TIME.test(text) ? text : MINUTE_TIME.test(text) ? text + ':00' : null; }
  function buttonsIn(root) { return list(root.querySelectorAll('button')).filter(shown).map(textOf); }
  // The host's free and total GPUs as "free/total", from the hidden host popover that the region in the row's ID cell
  // names by aria-describedby (which the console spells ariadescribedby); null unless each link is single.
  function gpuIdle(tr) {
    var c = tr.cells[0];
    var rs = c ? list(c.querySelectorAll('.region')) : [];
    if (rs.length !== 1) return null;
    var refs = [rs[0].getAttribute('aria-describedby'), rs[0].getAttribute('ariadescribedby')]
      .filter(function (x, k, a) { return !!x && a.indexOf(x) === k; });
    if (refs.length !== 1) return null;
    var ps = list(document.querySelectorAll('[id]')).filter(function (p) { return p.id === refs[0]; });
    if (ps.length !== 1 || !ps[0].classList.contains('el-popper')) return null;
    var ls = list(ps[0].querySelectorAll('*')).filter(function (el) { return /^GPU空闲\/总量[:：]?$/.test(ownText(el)); });
    if (ls.length !== 1 || !ls[0].nextElementSibling) return null;
    var v = textOf(ls[0].nextElementSibling);
    return /^\d+\/\d+$/.test(v) ? v : null;
  }
  // Where the row is, as its ID cell shows it: the text of the one region there (the region and the host, as in
  // "某区 / 123机"), which is what a user finds the row by. Null unless that cell holds exactly one region.
  function place(tr) {
    var c = tr.cells[0];
    var rs = c ? list(c.querySelectorAll('.region')) : [];
    return rs.length === 1 ? visibleText(rs[0]) || null : null;
  }
  // A row's status (the first text of the 状态 cell), mode, GPU充足, the host's free GPUs, timer, release countdown,
  // spec text (the one text of the 规格详情 cell outside its buttons) with the GPU count it ends with, button texts,
  // and its place.
  function readRow(tr, cols) {
    var st = cell(tr, cols.state);
    var tm = cell(tr, cols.timer);
    var op = cell(tr, cols.ops);
    if (!st || !tm || !op) return null;
    var s = leaves(st);
    var t = leaves(tm);
    var timer = t.filter(function (x) { return FULL_TIME.test(x); });
    var release = t.filter(function (x) { return /后释放$/.test(x); });
    var state = s.length ? s[0] : '';
    var sp = cols.spec ? cell(tr, cols.spec) : null;
    var specs = sp ? list(sp.querySelectorAll('*')).filter(function (el) { return ownText(el) !== '' && shown(el) && !el.closest('button'); })
      .map(ownText) : [];
    var spec = specs.length === 1 ? specs[0] : null;
    var cards = spec === null ? null : /\* ?(\d+) ?卡$/.exec(spec);
    return { state: state, mode: s.indexOf('无卡模式') >= 0 ? 'nogpu' : state === '已关机' ? null : 'gpu',
             gpuFree: s.indexOf('GPU充足') >= 0, gpuIdle: gpuIdle(tr), timer: timer.length ? timer[0] : null,
             release: release.length ? release[0] : null, spec: spec, gpus: cards ? Number(cards[1]) : null,
             buttons: buttonsIn(tm).concat(buttonsIn(op)), place: place(tr) };
  }
  // The deepest elements whose text holds id, skipping scripts and styles; an element whose own text holds id counts
  // even when a child holds it as well.
  function holders(id) {
    var out = [];
    function walk(el) {
      var inChild = false;
      for (var i = 0; i < el.children.length; i++) {
        var k = el.children[i];
        if (!SKIP[k.tagName] && k.textContent.indexOf(id) >= 0) { inChild = true; walk(k); }
      }
      if (!inChild || ownText(el).indexOf(id) >= 0) out.push(el);
    }
    if (document.body && document.body.textContent.indexOf(id) >= 0) walk(document.body);
    return out;
  }
  // The instance ID a row shows in its first cell, or null.
  function rowId(tr) {
    var c = tr.cells[0];
    if (!c) return null;
    var m = list(c.querySelectorAll('*')).filter(function (el) { return ID_SHAPE.test(textOf(el)) && !el.closest('button, a'); });
    m = m.filter(function (el) { return !m.some(function (o) { return o !== el && el.contains(o); }); });
    return m.length === 1 ? textOf(m[0]) : null;
  }
  // The row of id: the only element on the page that holds id must hold only id and sit in the first cell of a row of
  // the instance table, outside any button or link.
  function target(id) {
    if (typeof id !== 'string' || !ID_SHAPE.test(id)) return { why: 'not an instance ID' + (TEST ? ' of the offline test' : '') };
    var tb = instanceTable();
    if (tb.why) return { why: 'not the instance list page (' + tb.why + ')' };
    var hs = holders(id);
    if (hs.length === 0) return { why: 'no row shows this instance ID' };
    if (hs.length > 1) return { why: 'the instance ID shows in ' + hs.length + ' places on the page' };
    var el = hs[0];
    if (textOf(el) !== id) return { why: 'the instance ID is not alone in its element' };
    var tr = el.closest('tr');
    if (!tr || tb.rows.indexOf(tr) < 0 || el.closest('button, a') || el.closest('td') !== tr.cells[0]) {
      return { why: 'the instance ID is not in the ID cell of an instance row' };
    }
    var info = readRow(tr, tb.cols);
    if (!info) return { why: 'the row has no single 状态, 释放时间/停机时间 or 操作 cell' };
    return { tr: tr, info: info };
  }
  // The stable entry of a row: GPU充足, the host's free GPUs and the release countdown change within minutes and are
  // left out, and so is the spec.
  function entry(id, i) { return { id: id, state: i.state, mode: i.mode, timer: i.timer, buttons: i.buttons }; }
  function canon(e) { return JSON.stringify([e.id, e.state, e.mode, e.timer, e.buttons]); }
  function allRows() {
    var tb = instanceTable();
    if (tb.why) return [];
    return tb.rows.map(function (tr) {
      var i = readRow(tr, tb.cols) || { state: '', mode: null, gpuFree: false, gpuIdle: null, timer: null, release: null, spec: null,
                                        gpus: null, buttons: [], place: null };
      return { tr: tr, id: rowId(tr), info: i };
    });
  }
  // '' when every row of the instance table shows one valid instance ID and no ID shows in two rows; otherwise why not.
  function rowsSane() {
    var ids = allRows().map(function (r) { return r.id; });
    if (ids.some(function (x) { return !x; })) return 'a row of the instance table shows no single instance ID';
    if (ids.some(function (x, k) { return ids.indexOf(x) !== k; })) return 'an instance ID shows in two rows';
    return '';
  }
  // All rows' stable entries in a fixed order (sorted, so reordering the rows changes nothing), leaving out one row.
  function canonAll(except) {
    return allRows().filter(function (r) { return r.tr !== except; }).map(function (r) { return canon(entry(r.id, r.info)); })
      .sort().join('\n');
  }
  function hash(s) {
    var h = 0x811c9dc5;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return s.length + ':' + ('0000000' + h.toString(16)).slice(-8);
  }

  function dialogs() { return list(document.querySelectorAll('.el-message-box, .el-dialog')).filter(shown); }
  function pickers() { return list(document.querySelectorAll('.el-picker-panel')).filter(shown); }
  function toasts() { return list(document.querySelectorAll('.el-message')).filter(shown); }
  function isBox(d) { return d.classList.contains('el-message-box'); }
  function one(root, sel) { var a = list(root.querySelectorAll(sel)); return a.length === 1 ? a[0] : null; }
  // A confirm box's message, or a dialog's title: the text of its header (the console puts a plain span there).
  function dialogText(d) {
    var m = one(d, isBox(d) ? '.el-message-box__message' : '.el-dialog__header');
    return m ? textOf(m) : '';
  }
  // The only shown button labelled label inside the only element matching sel within root.
  function buttonIn(root, sel, label) {
    var c = one(root, sel);
    if (!c) return null;
    var bs = list(c.querySelectorAll('button')).filter(function (b) { return textOf(b) === label && shown(b); });
    return bs.length === 1 ? bs[0] : null;
  }
  // This row's 更多 trigger and the one menu its aria-controls names.
  function rowMenu(tr) {
    var op = cell(tr, instanceTable().cols.ops);
    var ts = list(op.querySelectorAll('.el-dropdown [aria-controls]')).filter(function (b) { return textOf(b) === '更多'; });
    if (ts.length !== 1) return { why: 'no single 更多 trigger in this row' };
    var ref = ts[0].getAttribute('aria-controls');
    var ms = list(document.querySelectorAll('ul.el-dropdown-menu')).filter(function (u) { return u.id === ref; });
    if (ms.length !== 1) return { why: ms.length ? 'the menu id of this row is not unique' : 'the menu of this row is not in the page' };
    return { trigger: ts[0], menu: ms[0] };
  }
  function menuOpen(m) { return shown(m.menu) && settled(m.menu) === ''; }
  function otherMenuOpen(m) {
    return list(document.querySelectorAll('ul.el-dropdown-menu')).some(function (u) { return u !== m.menu && shown(u); });
  }

  function row(id) {
    var g = gate();
    if (g) return no(g);
    var t = target(id);
    if (t.why) return no(t.why);
    var i = t.info;
    return { ok: true, id: id, state: i.state, mode: i.mode, gpuFree: i.gpuFree, gpuIdle: i.gpuIdle, timer: i.timer, release: i.release,
             spec: i.spec, gpus: i.gpus, buttons: i.buttons, place: i.place };
  }
  function rows() {
    var g = gate();
    if (g) return no(g);
    return allRows().map(function (r) {
      var i = r.info;
      return { id: r.id, state: i.state, mode: i.mode, gpuFree: i.gpuFree, gpuIdle: i.gpuIdle, timer: i.timer, release: i.release,
               spec: i.spec, gpus: i.gpus, buttons: i.buttons, place: i.place };
    });
  }
  function snapshot() {
    var g = gate();
    if (g) return no(g);
    return allRows().map(function (r) { return entry(r.id, r.info); });
  }
  function dialog() {
    var g = gate();
    if (g) return no(g);
    var ds = dialogs();
    return { ok: true, visible: ds.length, list: ds.map(function (d) { return { kind: isBox(d) ? 'box' : 'dialog', text: dialogText(d) }; }),
             picker: pickers().length };
  }
  function busyWhy() {
    return 'another operation of this script is open (at ' + contexts[active].stage + '); it ends with dismiss or with settle ' +
      'returning confirmed, otherwise reload the page';
  }
  // Opens this row's 更多 menu by sending mouseenter to its trigger when it is not open yet, and lists the items once
  // it is open. Nothing else is touched; another open menu, or an operation still open, is a refusal.
  function menu(id) {
    var g = gate();
    if (g) return no(g);
    if (active) return no(busyWhy());
    var t = target(id);
    if (t.why) return no(t.why);
    var m = rowMenu(t.tr);
    if (m.why) return no(m.why);
    if (otherMenuOpen(m)) return no('another 更多 menu is open');
    if (!menuOpen(m)) {
      var why = interactable(m.trigger);
      if (why) return no('the 更多 trigger ' + why);
      hover(m.trigger, 'mouseenter');
    }
    if (!menuOpen(m)) return { ok: true, open: false };
    return { ok: true, open: true, items: list(m.menu.querySelectorAll('li.el-dropdown-menu__item')).map(textOf) };
  }
  function leaveMenu(id) {
    var g = gate();
    if (g) return no(g);
    var t = target(id);
    if (t.why) return no(t.why);
    var m = rowMenu(t.tr);
    if (m.why) return no(m.why);
    hover(m.trigger, 'mouseleave');
    return { ok: true };
  }

  function ctxOf(key) {
    if (typeof key !== 'string' || !Object.prototype.hasOwnProperty.call(contexts, key)) {
      return { why: 'unknown context (after a reload, call settle with the prepared copy)' };
    }
    return contexts[key];
  }
  function stageWhy(c) {
    if (c.stage === 'attempted') return 'the final confirm was already made in this context';
    if (c.stage === 'dismissed') return 'this context was dismissed';
    return 'wrong step: the context is at ' + c.stage;
  }
  // The prepared copy the AI keeps: enough for settle after a reload, and only a hash of the other rows.
  function copyOf(c) {
    return { v: 2, prepared: true, ctx: c.key, id: c.id, op: c.op, before: c.before, others: c.others, text: c.text, expect: c.expect };
  }
  // Opens an operation: checks the page, that no other operation is open, the rows, the state and the button, then
  // clicks the button.
  function start(op, id) {
    var g = gate();
    if (g) return no(g);
    if (active) return no(busyWhy());
    var t = target(id);
    if (t.why) return no(t.why);
    var sane = rowsSane();
    if (sane) return no(sane);
    if (dialogs().length || pickers().length) return no('a dialog or picker is already open; nothing is clicked while it shows');
    var i = t.info;
    if ((op === 'gpu-on' || op === 'nogpu-on') && i.state !== '已关机') return no('the state is ' + i.state + ', not 已关机');
    if (op === 'power-off' && i.state !== '运行中') return no('the state is ' + i.state + ', not 运行中');
    if (op === 'gpu-on' && !i.gpuFree) return no('this row shows no GPU充足: no free GPU on its host');
    if (op === 'gpu-on' && !(i.gpus >= 1)) return no('the GPU count of this row cannot be read from its 规格详情');
    if (op === 'cancel-timer' && !i.timer) return no('no timer is set on this row');
    var label;
    var cands;
    if (op === 'nogpu-on') {
      var m = rowMenu(t.tr);
      if (m.why) return no(m.why);
      if (otherMenuOpen(m)) return no('another 更多 menu is open');
      if (!menuOpen(m)) return no('the 更多 menu of this row is not open; call menu(ID) first');
      label = '无卡模式开机';
      cands = list(m.menu.querySelectorAll('li.el-dropdown-menu__item'));
    } else {
      var cols = instanceTable().cols;
      label = { 'gpu-on': '开机', 'power-off': '关机', 'cancel-timer': '关闭', 'timer': i.timer ? '修改' : '设置定时关机' }[op];
      cands = list(cell(t.tr, op === 'gpu-on' || op === 'power-off' ? cols.ops : cols.timer).querySelectorAll('button'));
    }
    cands = cands.filter(function (el) { return textOf(el) === label; });
    if (cands.length !== 1) return no('no single ' + label + ' in this row');
    var why = interactable(cands[0]);
    if (why) return no('the ' + label + ' ' + why);
    deny(label);
    seq += 1;
    var c = { key: nonce + '-' + seq, op: op, id: id, stage: 'opened', row: t.tr, before: entry(id, i), all: canonAll(),
              others: hash(canonAll(t.tr)), spec: i.spec, gpus: i.gpus, dialog: null, picker: null, text: null, expect: null,
              editor: null };
    contexts[c.key] = c;
    active = c.key;
    press(cands[0]);
    return { ok: true, ctx: c.key, stage: 'opened' };
  }
  // Binds the only dialog showing once it is fully shown and its text is the one this operation expects. For a
  // confirm box this prepares the operation and returns the prepared copy. It also returns the row's spec and GPU count
  // as read at the start; the final confirm requires the row to still show them.
  function bindDialog(key) {
    var c = ctxOf(key);
    if (c.why) return no(c.why);
    // clone-begin
    if (c.op === 'clone') return no('a clone context is bound with bindCloneDialog');
    // clone-end
    if (c.stage !== 'opened') return no(stageWhy(c));
    var g = gate();
    if (g) return no(g);
    var ds = dialogs();
    if (ds.length === 0) return { ok: false, pending: true, why: 'no dialog has shown yet' };
    if (ds.length > 1) return no('more than one dialog is showing (' + ds.length + ')');
    var d = ds[0];
    var box = c.op !== 'timer';
    if (isBox(d) !== box) return no('the dialog showing is not the kind this operation opens');
    var s = settled(d);
    if (s) return { ok: false, pending: true, why: 'the dialog ' + s };
    var text = dialogText(d);
    var out = { ok: true, stage: 'bound', kind: box ? 'box' : 'dialog', text: text, spec: c.spec, gpus: c.gpus };
    if (box) {
      var m = BOX[c.op] ? BOX[c.op].exec(text) : null;
      if (BOX[c.op] && !m) return no('the dialog text does not match: ' + text);
      if (m && m[1]) out.price = m[1];
      if (c.op === 'power-off') out.reviewed = false;
    } else {
      if (text !== '定时关机') return no('the dialog title does not match: ' + text);
      if (list(d.querySelectorAll('.el-date-editor input')).length !== 1) return no('the timer dialog has no single date editor');
    }
    // The node is recorded before the refusal list runs, so a dialog stopped by it can still be dismissed: at blocked,
    // dismiss is the only step left.
    c.dialog = d;
    c.text = text;
    var word = denyWord(visibleText(d));
    if (word) {
      c.stage = 'blocked';
      throw new Error('refusal list: ' + word);
    }
    c.stage = 'bound';
    if (box) out.copy = copyOf(c);
    return out;
  }
  // Before a final confirm or a dismiss: the bound dialog is still the only one showing, fully shown, with the same text.
  function recheckDialog(c) {
    if (!c.dialog.isConnected) return 'the bound dialog is gone';
    var ds = dialogs();
    if (ds.length !== 1 || ds[0] !== c.dialog) return 'the bound dialog is not the only one showing';
    var s = settled(c.dialog);
    if (s) return 'the bound dialog ' + s;
    if (dialogText(c.dialog) !== c.text) return 'the bound dialog text changed';
    return '';
  }
  // Before a final confirm: the target row is the same node, the rows are sane and read as at the start, the target row
  // shows the spec and GPU count it showed at the start, and a GPU power-on still shows GPU充足.
  function recheckRow(c) {
    var t = target(c.id);
    if (t.why) return t.why;
    if (t.tr !== c.row) return 'the target row was re-rendered (its node changed)';
    var sane = rowsSane();
    if (sane) return sane;
    if (canonAll() !== c.all) return 'a row changed since the start';
    if (t.info.spec !== c.spec || t.info.gpus !== c.gpus) return 'the spec or GPU count of this row changed since the start';
    if (c.op === 'gpu-on' && !t.info.gpuFree) return 'GPU充足 is gone from this row';
    return '';
  }
  // The final confirm of a box. The context counts as attempted before the click, whatever happens next.
  function confirm(key) {
    var c = ctxOf(key);
    if (c.why) return no(c.why);
    if (c.op === 'timer') return no('a timer context ends with confirmTimerDialog');
    if (c.stage === 'opened') return no('bind the dialog first (bindDialog)');
    if (c.stage !== 'bound') return no(stageWhy(c));
    if (c.op === 'power-off') return no('the 关机 confirm text has not been reviewed yet; read it and dismiss');
    var g = gate();
    if (g) return no(g);
    var why = recheckDialog(c);
    if (why) return no(why);
    if (!BOX[c.op].test(c.text)) return no('the dialog text does not match');
    var b = buttonIn(c.dialog, '.el-message-box__btns', '确定');
    if (!b) return no('no single 确定 in the dialog');
    why = interactable(b);
    if (why) return no('the 确定 ' + why);
    why = recheckRow(c);
    if (why) return no(why);
    deny(visibleText(c.dialog));
    c.stage = 'attempted';
    press(b);
    return { ok: true, stage: 'attempted', copy: copyOf(c) };
  }
  // Clicks 取消 in the bound dialog, before the final confirm and only while the bound dialog passes recheckDialog. It
  // never touches a dialog it did not bind; such a dialog goes away with a reload of the page.
  function dismiss(key) {
    var c = ctxOf(key);
    if (c.why) return no(c.why);
    if (c.stage === 'attempted') return no('the final confirm was already made in this context; it cannot be dismissed');
    if (c.stage === 'dismissed') return no('this context was already dismissed');
    if (c.stage === 'opened') return no('no dialog is bound to this context and nothing was confirmed; reload the page to start over');
    var g = gate();
    if (g) return no(g);
    var why = recheckDialog(c);
    if (why) return no(why + '; nothing was confirmed; reload the page to start over');
    var b = buttonIn(c.dialog, isBox(c.dialog) ? '.el-message-box__btns' : '.el-dialog__footer', '取消');
    if (!b) return no('no single 取消 in the bound dialog');
    why = interactable(b);
    if (why) return no('the 取消 ' + why);
    c.stage = 'dismissed';
    if (active === c.key) active = null;
    press(b);
    return { ok: true, stage: 'dismissed' };
  }

  // A timer context at one of the given stages whose bound dialog still passes recheckDialog, or {why}.
  function timerCtx(key, stages) {
    var c = ctxOf(key);
    if (c.why) return c;
    if (c.op !== 'timer') return { why: 'not a timer context' };
    if (c.stage === 'opened') return { why: 'bind the dialog first (bindDialog)' };
    if (stages.indexOf(c.stage) < 0) return { why: stageWhy(c) };
    var g = gate();
    if (g) return { why: g };
    var r = recheckDialog(c);
    if (r) return { why: r };
    return c;
  }
  // The bound picker is still the only picker showing, fully shown.
  function recheckPicker(c) {
    if (!c.picker || !c.picker.isConnected) return 'the bound picker is gone';
    var ps = pickers();
    if (ps.length !== 1 || ps[0] !== c.picker) return 'the bound picker is not the only one showing';
    var s = settled(c.picker);
    return s ? 'the bound picker ' + s : '';
  }
  function pickerInput(p, which) {
    var ph = which === 'date' ? '选择日期' : '选择时间';
    var ins = list(p.querySelectorAll('input')).filter(function (x) { return x.getAttribute('placeholder') === ph; });
    return ins.length === 1 ? ins[0] : null;
  }
  // Opens the picker by focusing the bound dialog's date editor; no picker may be open before.
  function openTimerPicker(key) {
    var c = timerCtx(key, ['bound', 'picked', 'prepared']);
    if (c.why) return no(c.why);
    if (pickers().length) return no('a picker is already open');
    var eds = list(c.dialog.querySelectorAll('.el-date-editor input'));
    if (eds.length !== 1) return no('no single date editor in the bound dialog');
    var why = interactable(eds[0]);
    if (why) return no('the date editor ' + why);
    c.picker = null;
    c.expect = null;
    c.editor = null;
    c.stage = 'opening';
    focusOn(eds[0]);
    return { ok: true, stage: 'opening' };
  }
  // Binds the only picker showing after openTimerPicker, once it is fully shown and has its two inputs and its 确定.
  function bindTimerPicker(key) {
    var c = timerCtx(key, ['opening']);
    if (c.why) return no(c.why);
    var ps = pickers();
    if (ps.length === 0) return { ok: false, pending: true, why: 'no picker has shown yet' };
    if (ps.length > 1) return no('more than one picker is showing (' + ps.length + ')');
    var s = settled(ps[0]);
    if (s) return { ok: false, pending: true, why: 'the picker ' + s };
    if (!pickerInput(ps[0], 'date') || !pickerInput(ps[0], 'time') || !buttonIn(ps[0], '.el-picker-panel__footer', '确定')) {
      return no('the picker has no single 选择日期, 选择时间 and 确定');
    }
    c.picker = ps[0];
    c.stage = 'picking';
    return { ok: true, stage: 'picking' };
  }
  // Focuses the bound picker's date or time input; the browser tool types the text.
  function focusTimerInput(key, which) {
    var c = timerCtx(key, ['picking']);
    if (c.why) return no(c.why);
    if (which !== 'date' && which !== 'time') return no('which must be date or time');
    var why = recheckPicker(c);
    if (why) return no(why);
    var el = pickerInput(c.picker, which);
    if (!el) return no('no single ' + which + ' input in the bound picker');
    why = interactable(el);
    if (why) return no('the ' + which + ' input ' + why);
    focusOn(el);
    return { ok: true, stage: 'picking' };
  }
  // The first text shown in the element right after a label of the dialog, such as the server time (the console puts a
  // tip after it in the same cell) or the expected cost.
  function labelValue(d, label) {
    var re = new RegExp('^' + label + '[:：]?$');
    var ls = list(d.querySelectorAll('*')).filter(function (el) { return re.test(ownText(el)); });
    if (ls.length !== 1 || !ls[0].nextElementSibling) return null;
    var n = ls[0].nextElementSibling;
    var ts = [n].concat(list(n.querySelectorAll('*'))).filter(function (x) { return ownText(x) !== '' && shown(x); }).map(ownText);
    return ts.length ? ts[0].slice(0, 60) : null;
  }
  // Reads the date editor, the bound picker's inputs, the server time and the expected cost. While picking, the bound
  // picker must pass its re-check; at bound, picked or prepared no picker may be open; at opening only the dialog is read.
  function readTimer(key) {
    var c = timerCtx(key, ['bound', 'opening', 'picking', 'picked', 'prepared']);
    if (c.why) return no(c.why);
    var why;
    if (c.stage === 'picking') {
      why = recheckPicker(c);
      if (why) return no(why);
    } else if (c.stage !== 'opening') {
      var ps = pickers();
      if (ps.length) return no(ps.length === 1 && ps[0] === c.picker ? 'the bound picker is still closing' : 'a picker this context did not bind is open');
    }
    var out = { ok: true, editor: null, date: null, time: null, serverTime: labelValue(c.dialog, '当前服务器时间'),
                cost: labelValue(c.dialog, '预计消费') };
    var eds = list(c.dialog.querySelectorAll('.el-date-editor input'));
    if (eds.length === 1) out.editor = eds[0].value;
    if (c.stage === 'picking') {
      var di = pickerInput(c.picker, 'date');
      var ti = pickerInput(c.picker, 'time');
      if (di) out.date = di.value;
      if (ti) out.time = ti.value;
    }
    return out;
  }
  // Clicks 确定 at the bottom of the bound picker, which fills the date editor.
  function confirmTimerPicker(key) {
    var c = timerCtx(key, ['picking']);
    if (c.why) return no(c.why);
    var why = recheckPicker(c);
    if (why) return no(why);
    var b = buttonIn(c.picker, '.el-picker-panel__footer', '确定');
    if (!b) return no('no single 确定 in the bound picker');
    why = interactable(b);
    if (why) return no('the 确定 of the picker ' + why);
    c.stage = 'picked';
    press(b);
    return { ok: true, stage: 'picked' };
  }
  // After the picker closed: the date editor must show a real moment after the server time (which must be readable) and
  // other than the current timer. Prepares the final confirm and returns the prepared copy; its expect is the editor's
  // time as the row will show it.
  function prepareTimer(key) {
    var c = timerCtx(key, ['picked']);
    if (c.why) return no(c.why);
    var ps = pickers();
    if (ps.length === 1 && ps[0] === c.picker) return { ok: false, pending: true, why: 'the picker is still closing' };
    if (ps.length) return no('a picker other than the bound one is open');
    var eds = list(c.dialog.querySelectorAll('.el-date-editor input'));
    var v = eds.length === 1 ? fullTime(eds[0].value) : null;
    if (v === null) return no('the date editor shows no date and time');
    var at = moment(v);
    if (at === null) return no('the date editor shows no valid time: ' + eds[0].value);
    if (v === c.before.timer) return no('the time chosen is the same as the current timer');
    var server = labelValue(c.dialog, '当前服务器时间');
    var now = moment(server);
    if (now === null) return no('the server time cannot be read' + (server ? ': ' + server : ''));
    if (at <= now) return no('the time chosen is not after the server time ' + server);
    deny(visibleText(c.dialog));
    c.expect = v;
    c.editor = eds[0].value;
    c.stage = 'prepared';
    return { ok: true, stage: 'prepared', expect: v, cost: labelValue(c.dialog, '预计消费'), copy: copyOf(c) };
  }
  // The final confirm of the timer: 确定 at the bottom of the dialog, once prepared, with no picker open and the editor
  // still showing the prepared time.
  function confirmTimerDialog(key) {
    var c = ctxOf(key);
    if (c.why) return no(c.why);
    if (c.op !== 'timer') return no('not a timer context');
    if (c.stage === 'opened') return no('bind the dialog first (bindDialog)');
    if (c.stage === 'bound' || c.stage === 'opening' || c.stage === 'picking') {
      return no('choose the time in the picker and press its 确定 first (confirmTimerPicker), then prepareTimer');
    }
    if (c.stage === 'picked') return no('prepare the time first (prepareTimer)');
    if (c.stage !== 'prepared') return no(stageWhy(c));
    var g = gate();
    if (g) return no(g);
    if (pickers().length) return no('a picker is open; the time has to be chosen and prepared again');
    var why = recheckDialog(c);
    if (why) return no(why);
    var eds = list(c.dialog.querySelectorAll('.el-date-editor input'));
    if (eds.length !== 1 || eds[0].value !== c.editor) return no('the date editor changed after prepareTimer');
    var b = buttonIn(c.dialog, '.el-dialog__footer', '确定');
    if (!b) return no('no single 确定 in the dialog footer');
    why = interactable(b);
    if (why) return no('the 确定 ' + why);
    why = recheckRow(c);
    if (why) return no(why);
    deny(visibleText(c.dialog));
    c.stage = 'attempted';
    press(b);
    return { ok: true, stage: 'attempted', copy: copyOf(c) };
  }

  function meets(op, now, expect) {
    if (op === 'gpu-on') return BOOTING.indexOf(now.state) >= 0 && now.mode === 'gpu';
    if (op === 'nogpu-on') return BOOTING.indexOf(now.state) >= 0 && now.mode === 'nogpu';
    if (op === 'power-off') return now.state === '关机中' || now.state === '已关机';
    if (op === 'cancel-timer') return now.timer === null;
    return op === 'timer' && !!expect && now.timer === expect;
  }
  // Field by field, whatever order the keys came back in.
  function sameCopy(a, b) {
    return a.v === b.v && a.prepared === b.prepared && a.ctx === b.ctx && a.id === b.id && a.op === b.op && a.others === b.others &&
      a.text === b.text && a.expect === b.expect && canon(a.before) === canon(b.before);
  }
  function validCopy(x) {
    return !!x && typeof x === 'object' && x.v === 2 && x.prepared === true && typeof x.ctx === 'string' &&
      typeof x.id === 'string' && ID_SHAPE.test(x.id) && OPS.indexOf(x.op) >= 0 && !!x.before && typeof x.before === 'object' &&
      typeof x.others === 'string' && typeof x.text === 'string' && x.text !== '' &&
      (x.op !== 'timer' || (typeof x.expect === 'string' && FULL_TIME.test(x.expect)));
  }
  // Read-only, after a final confirm. confirmed: the target row changed as expected, no other row changed, no error
  // prompt and no dialog showing. pending: nothing changed yet, or it changed as expected while the bound dialog itself
  // is still closing. uncertain: anything else, including any dialog that cannot be shown to be the bound one (with a
  // copy, every dialog). Takes the context, or the prepared copy after a reload. confirmed ends the operation.
  function settle(arg) {
    var cp;
    var c = null;
    if (typeof arg === 'string') {
      c = ctxOf(arg);
      if (c.why) return no(c.why);
      if (c.stage !== 'attempted') return no('no final confirm has been made in this context');
      cp = copyOf(c);
    } else if (validCopy(arg)) {
      cp = arg;
    } else {
      return no('not a prepared copy from this script (bindDialog or prepareTimer returns it)');
    }
    var g = gate();
    if (g) return no(g);
    var ds = dialogs();
    var ts = toasts();
    var errs = ts.filter(function (m) { return m.classList.contains('el-message--error') || m.classList.contains('el-message--warning'); });
    var prompts = ts.map(textOf).concat(ds.map(dialogText));
    var own = !!c && ds.length === 1 && ds[0] === c.dialog && isBox(ds[0]) === (c.op !== 'timer') && dialogText(ds[0]) === c.text;
    var foreign = ds.length > 0 && !own;
    var t = target(cp.id);
    if (t.why) return { ok: true, result: 'uncertain', why: t.why, prompts: prompts, changed: { target: 'not found', others: null } };
    var now = entry(cp.id, t.info);
    var moved = hash(canonAll(t.tr)) !== cp.others;
    var same = canon(now) === canon(cp.before);
    var good = !same && meets(cp.op, now, cp.expect);
    var calm = !moved && !errs.length && !foreign;
    var result = calm && good && !own ? 'confirmed' : calm && (same || good) ? 'pending' : 'uncertain';
    // confirmed ends the operation, also when settled with the copy, if the copy is field by field that of the open context.
    var k = c ? c.key : cp.ctx;
    if (result === 'confirmed' && active === k && Object.prototype.hasOwnProperty.call(contexts, k) && contexts[k].stage === 'attempted' &&
        sameCopy(copyOf(contexts[k]), cp)) {
      active = null;
    }
    return { ok: true, result: result, prompts: prompts,
             changed: { target: good ? 'as expected' : same ? 'unchanged' : 'unexpected', others: moved } };
  }

  var NAMES = ['page', 'row', 'rows', 'snapshot', 'dialog', 'menu', 'leaveMenu', 'startPowerOnGpu', 'startPowerOnNoGpu', 'startCancelTimer',
               'startSetTimer', 'startPowerOff', 'bindDialog', 'openTimerPicker', 'bindTimerPicker', 'focusTimerInput', 'readTimer',
               'confirmTimerPicker', 'prepareTimer', 'confirm', 'confirmTimerDialog', 'dismiss', 'settle'];
  // An API whose every function refuses with why.
  function shell(why) {
    var s = { brand: BRAND, version: VERSION, mode: 'none' };
    NAMES.forEach(function (n) {
      s[n] = n === 'page' ? function () { return { list: false, login: false, why: why }; } : function () { return no(why); };
    });
    return Object.freeze(s);
  }
  // One script per page: the first call registers the API as window.__autodl; a later call gets that same API back if it
  // is this brand, version and mode and has the clone's functions whenever the caller's copy has them; otherwise a
  // shell that refuses everything until the page is reloaded.
  function register(api) {
    if (modeError) return api;
    var prior = window.__autodl;
    if (prior === undefined || prior === null) {
      window.__autodl = api;
      return api;
    }
    if (prior.brand === BRAND && prior.version === VERSION && prior.mode === api.mode && (prior.clone === true || api.clone !== true)) {
      return prior;
    }
    return shell('another copy or version of the page script is loaded in this page, or window.__autodl is taken; reload the page');
  }

  var built = {
    brand: BRAND,
    version: VERSION,
    mode: modeError ? 'none' : TEST ? 'offline-test' : 'live',
    page: page,
    row: row,
    rows: rows,
    snapshot: snapshot,
    dialog: dialog,
    menu: menu,
    leaveMenu: leaveMenu,
    startPowerOnGpu: function (id) { return start('gpu-on', id); },
    startPowerOnNoGpu: function (id) { return start('nogpu-on', id); },
    startCancelTimer: function (id) { return start('cancel-timer', id); },
    startSetTimer: function (id) { return start('timer', id); },
    startPowerOff: function (id) { return start('power-off', id); },
    bindDialog: bindDialog,
    openTimerPicker: openTimerPicker,
    bindTimerPicker: bindTimerPicker,
    focusTimerInput: focusTimerInput,
    readTimer: readTimer,
    confirmTimerPicker: confirmTimerPicker,
    prepareTimer: prepareTimer,
    confirm: confirm,
    confirmTimerDialog: confirmTimerDialog,
    dismiss: dismiss,
    settle: settle
  };

  // clone-begin
  // ---- the clone: everything from here to clone-end is left out of the everyday copy (reference/console.min.js) and
  // kept in the copy that is loaded to clone an instance (reference/console-clone.min.js) ----
  // The clone goes by this table and not by the refusal list: the menu item, and every text the clone dialog shows, in
  // page order. head ends with the data disk's checkbox; the sentence about the source's paid expansion may follow it.
  var CLONE = {
    item: '克隆实例新',
    title: '克隆实例',
    disk: '数据盘',
    go: '继续',
    head: ['克隆实例', '克隆后源实例不受影响，不会释放也不会清理数据', '需要克隆的数据：', '系统盘', '数据盘'],
    expand: /^源实例有扩容数据盘：(\d+)GB 请扩容目标实例数据盘，以防拷贝失败$/,
    tail: ['优化稀疏文件拷贝：', '开启则会在拷贝时对稀疏文件进行优化，一般可节省目标实例磁盘空间'],
    left: /^今天剩余克隆次数：(\d+)次$/,
    buttons: ['取消', '继续']
  };
  var CLONE_CLICKS = ['克隆实例新', '数据盘', '继续'];
  // A host's ID is the part of an instance ID before the hyphen.
  var HOST_SHAPE = TEST ? /^(abcd|wxyz|mnop|qrst|efgh)\d{6}$/ : /^[0-9a-z]{10}$/;
  var DIGEST = /^\d{1,3}:[0-9a-f]{8}$/;
  var HOST_NAME = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/;
  var SSH_DOMAIN = /\.(seetacloud|autodl)\.com$/;
  // The clone's own click: only on an element whose whole text is one of the clone's three fixed texts.
  function pressClone(el, text) {
    if (CLONE_CLICKS.indexOf(text) < 0 || textOf(el) !== text) throw new Error('not a click of the clone');
    el.click();
  }
  // Opens a clone: the row is stopped and shows no GPU充足 (with a free GPU the thing to do is to power it on), its spec
  // and GPU count can be read, its 更多 menu is open (menu(ID)) and holds one item whose whole text is 克隆实例新.
  function startClone(id) {
    var g = gate();
    if (g) return no(g);
    if (active) return no(busyWhy());
    var t = target(id);
    if (t.why) return no(t.why);
    var sane = rowsSane();
    if (sane) return no(sane);
    if (dialogs().length || pickers().length) return no('a dialog or picker is already open; nothing is clicked while it shows');
    var i = t.info;
    if (i.state !== '已关机') return no('the state is ' + i.state + ', not 已关机');
    if (i.gpuFree) return no('this row shows GPU充足: power the instance on instead of cloning it');
    if (i.spec === null || !(i.gpus >= 1)) return no('the spec or GPU count of this row cannot be read from its 规格详情');
    var m = rowMenu(t.tr);
    if (m.why) return no(m.why);
    if (otherMenuOpen(m)) return no('another 更多 menu is open');
    if (!menuOpen(m)) return no('the 更多 menu of this row is not open; call menu(ID) first');
    var cands = list(m.menu.querySelectorAll('li.el-dropdown-menu__item')).filter(function (el) { return textOf(el) === CLONE.item; });
    if (cands.length !== 1) return no('no single ' + CLONE.item + ' in the menu of this row');
    var why = interactable(cands[0]);
    if (why) return no('the ' + CLONE.item + ' ' + why);
    seq += 1;
    var c = { key: nonce + '-' + seq, op: 'clone', id: id, stage: 'opened', row: t.tr, before: entry(id, i), all: canonAll(),
              others: hash(canonAll(t.tr)), spec: i.spec, gpus: i.gpus, dialog: null, picker: null, text: null, expect: null,
              editor: null };
    contexts[c.key] = c;
    active = c.key;
    pressClone(cands[0], CLONE.item);
    return { ok: true, ctx: c.key, stage: 'opened' };
  }
  // A clone context at one of the given stages, or {why}.
  function cloneCtx(key, stages) {
    var c = ctxOf(key);
    if (c.why) return c;
    if (c.op !== 'clone') return { why: 'not a clone context' };
    if (stages.indexOf(c.stage) < 0) return { why: stageWhy(c) };
    var g = gate();
    if (g) return { why: g };
    return c;
  }
  // The texts a user may see in el, one for each element that has text of its own, in page order.
  function texts(el) {
    return [el].concat(list(el.querySelectorAll('*'))).filter(function (x) { return ownText(x) !== '' && shown(x); }).map(ownText);
  }
  // Reads the clone dialog d. Every text it shows is an entry of the fixed table, each once and in order; the system
  // disk's checkbox is ticked and disabled and the data disk's is enabled; there is no input besides these two and the
  // sparse-file switch, which is off; 取消 and 继续 are alone in the footer. Returns {why}, or {pending} while the page
  // has not finished marking a tick, or what the dialog says.
  function readCloneDialog(d) {
    var segs = texts(d);
    var fixed = CLONE.head.concat(CLONE.tail, CLONE.buttons);
    for (var k = 0; k < segs.length; k++) {
      if (fixed.indexOf(segs[k]) < 0 && !CLONE.expand.test(segs[k]) && !CLONE.left.test(segs[k])) {
        return { why: 'the 克隆实例 dialog shows text that is not in the fixed table: ' + segs[k].slice(0, 80) };
      }
    }
    var x = segs.length === 11 ? CLONE.expand.exec(segs[5]) : null;
    var rest = x ? segs.slice(0, 5).concat(segs.slice(6)) : segs;
    var left = rest.length === 10 ? CLONE.left.exec(rest[7]) : null;
    if (!left || rest.join('\n') !== CLONE.head.concat(CLONE.tail, [rest[7]], CLONE.buttons).join('\n')) {
      return { why: 'the 克隆实例 dialog does not show the texts of the fixed table once each and in order' };
    }
    var boxes = list(d.querySelectorAll('label.el-checkbox input[type="checkbox"]'));
    if (boxes.length !== 2 || boxes[0].value !== 'copy_system_disk' || boxes[1].value !== 'copy_data_disk') {
      return { why: 'the dialog has no 系统盘 and 数据盘 checkboxes of its own' };
    }
    var sysLabel = boxes[0].closest('label');
    var diskLabel = boxes[1].closest('label');
    if (textOf(sysLabel) !== CLONE.head[3] || textOf(diskLabel) !== CLONE.disk) {
      return { why: 'the checkboxes of the dialog are not labelled 系统盘 and 数据盘' };
    }
    var sw = list(d.querySelectorAll('.el-switch'));
    var swIn = sw.length === 1 ? list(sw[0].querySelectorAll('input')) : [];
    if (swIn.length !== 1 || list(d.querySelectorAll('input')).length !== 3) {
      return { why: 'the dialog has inputs other than its two checkboxes and its one switch' };
    }
    if (!boxes[0].checked || !boxes[0].disabled || !sysLabel.classList.contains('is-checked') || !sysLabel.classList.contains('is-disabled')) {
      return { why: 'the 系统盘 checkbox is not ticked and fixed' };
    }
    if (boxes[1].disabled || diskLabel.classList.contains('is-disabled')) return { why: 'the 数据盘 checkbox is disabled' };
    if (sw[0].getAttribute('aria-checked') !== 'false' || sw[0].classList.contains('is-checked') || swIn[0].checked) {
      return { why: 'the sparse-file switch is not off' };
    }
    var foot = one(d, '.el-dialog__footer');
    if (!foot || buttonsIn(foot).join('\n') !== CLONE.buttons.join('\n')) return { why: 'the dialog footer does not hold 取消 and 继续 alone' };
    if (boxes[1].checked !== diskLabel.classList.contains('is-checked')) return { pending: 'the 数据盘 checkbox is still changing' };
    return { dataDisk: boxes[1].checked, expandGb: x ? Number(x[1]) : 0, remaining: Number(left[1]), diskLabel: diskLabel };
  }
  // Binds the clone dialog, or reads it again once it is bound (after tickCloneDataDisk). The only dialog showing must
  // be the one titled 克隆实例, fully shown, and pass readCloneDialog. A clone dialog that fails that reading is recorded
  // all the same, at blocked, so that dismiss can close it; any other dialog is left alone and goes away with a reload.
  // Returns whether the data disk is ticked, the source's paid expansion in GB (0 without the sentence about it), the
  // clones left today, and the row's spec and GPU count as read at the start.
  function bindCloneDialog(key) {
    var c = cloneCtx(key, ['opened', 'clone-bound']);
    if (c.why) return no(c.why);
    var r;
    if (c.stage === 'opened') {
      var ds = dialogs();
      if (ds.length === 0) return { ok: false, pending: true, why: 'no dialog has shown yet' };
      if (ds.length > 1) return no('more than one dialog is showing (' + ds.length + ')');
      var s = settled(ds[0]);
      if (s) return { ok: false, pending: true, why: 'the dialog ' + s };
      if (isBox(ds[0]) || dialogText(ds[0]) !== CLONE.title) return no('the dialog showing is not the 克隆实例 dialog');
      r = readCloneDialog(ds[0]);
      if (!r.pending) {
        c.dialog = ds[0];
        c.text = CLONE.title;
        c.stage = r.why ? 'blocked' : 'clone-bound';
      }
    } else {
      var why = recheckDialog(c);
      r = why ? { why: why } : readCloneDialog(c.dialog);
    }
    if (r.pending) return { ok: false, pending: true, why: r.pending };
    if (r.why) return no(r.why);
    return { ok: true, stage: 'clone-bound', kind: 'dialog', text: c.text, dataDisk: r.dataDisk, expandGb: r.expandGb,
             remaining: r.remaining, spec: c.spec, gpus: c.gpus };
  }
  // A clone context whose bound dialog still passes recheckDialog and readCloneDialog: {c, r}; otherwise {why} or {pending}.
  function cloneRead(key) {
    var c = cloneCtx(key, ['clone-bound']);
    if (c.why) return c;
    var why = recheckDialog(c);
    if (why) return { why: why };
    var r = readCloneDialog(c.dialog);
    return r.why || r.pending ? r : { c: c, r: r };
  }
  // Ticks the data disk: clicks the label of its checkbox, and only while it is not ticked.
  function tickCloneDataDisk(key) {
    var x = cloneRead(key);
    if (x.pending) return { ok: false, pending: true, why: x.pending };
    if (x.why) return no(x.why);
    if (x.r.dataDisk) return no('数据盘 is already ticked');
    var why = interactable(x.r.diskLabel);
    if (why) return no('the 数据盘 checkbox ' + why);
    pressClone(x.r.diskLabel, CLONE.disk);
    return { ok: true, stage: 'clone-bound' };
  }
  // The clone's last step in the instance list: the dialog passes every check again, the data disk is ticked, a clone
  // is left today, and the row is as at the start and still shows no GPU充足. Clicks 继续 and ends the operation: the
  // console then leaves the instance list for the page that creates the new instance, which has a script of its own.
  function continueClone(key) {
    var x = cloneRead(key);
    if (x.pending) return { ok: false, pending: true, why: x.pending };
    if (x.why) return no(x.why);
    var c = x.c;
    if (!x.r.dataDisk) return no('数据盘 is not ticked; call tickCloneDataDisk first');
    if (x.r.remaining < 1) return no('no clone is left today (今天剩余克隆次数 is 0)');
    var b = buttonIn(c.dialog, '.el-dialog__footer', CLONE.go);
    if (!b) return no('no single 继续 in the dialog');
    var why = interactable(b);
    if (why) return no('the 继续 ' + why);
    why = recheckRow(c);
    if (why) return no(why);
    if (target(c.id).info.gpuFree) return no('this row now shows GPU充足: dismiss the dialog and power the instance on instead');
    c.stage = 'continued';
    if (active === c.key) active = null;
    pressClone(b, CLONE.go);
    return { ok: true, stage: 'continued', dataDisk: true, expandGb: x.r.expandGb, remaining: x.r.remaining };
  }
  // Read-only: a digest of each row's instance ID, never an ID. Taken before a creation, it tells afterwards which rows
  // are new.
  function idDigests() {
    var g = gate();
    if (g) return no(g);
    var sane = rowsSane();
    if (sane) return no(sane);
    return { ok: true, digests: allRows().map(function (r) { return hash(r.id); }) };
  }
  // Read-only, after a creation: the rows whose ID has no digest in digests, begins with hostId and a hyphen, and whose
  // spec text is spec. With id, the ID the platform gave for the new instance, only that row counts, under the same
  // three conditions. Answers the candidates' ID, state, mode and timer, and nothing of any other row.
  function findCreated(digests, hostId, spec, id) {
    var g = gate();
    if (g) return no(g);
    if (!Array.isArray(digests) || !digests.length || !digests.every(function (x) { return typeof x === 'string' && DIGEST.test(x); })) {
      return no('digests must be the list that idDigests returned before the creation');
    }
    if (typeof hostId !== 'string' || !HOST_SHAPE.test(hostId)) return no('not a host ID' + (TEST ? ' of the offline test' : ''));
    if (typeof spec !== 'string' || spec === '') return no('spec must be the 规格详情 text of the source row');
    var given = id !== undefined && id !== null;
    if (given && (typeof id !== 'string' || !ID_SHAPE.test(id))) return no('not an instance ID' + (TEST ? ' of the offline test' : ''));
    var sane = rowsSane();
    if (sane) return no(sane);
    var found = allRows().filter(function (r) {
      return digests.indexOf(hash(r.id)) < 0 && r.id.indexOf(hostId + '-') === 0 && r.info.spec === spec && (!given || r.id === id);
    });
    return { ok: true, count: found.length, rows: found.map(function (r) {
      return { id: r.id, state: r.info.state, mode: r.info.mode, timer: r.info.timer };
    }) };
  }
  // The SSH host and port of a running instance, from the page's own data: the console shows the login command masked.
  // This is the only function that reads that data. The way to it is fixed: the root vnode on #app, down through each
  // component's subTree and each node's children, to the component named ElTable whose root element is the instance
  // table; its props.data holds an object per row. Exactly one object's uuid must be id; of it three keys are read, and
  // they must agree: a host name under seetacloud.com or autodl.com, a port number, and the command that names both. A
  // refusal says which check failed and never carries a value that was read.
  function sshAddress(id) {
    var g = gate();
    if (g) return no(g);
    var t = target(id);
    if (t.why) return no(t.why);
    if (t.info.state !== '运行中') return no('the state is ' + t.info.state + ', not 运行中');
    var el = t.tr.closest('.el-table');
    var app = document.getElementById('app');
    var seen = 0;
    var tables = [];
    function walk(v, depth) {
      if (!v || typeof v !== 'object' || depth > 200 || seen >= 200000) return;
      seen += 1;
      var c = v.component;
      if (c && typeof c === 'object') {
        if (c.type && c.type.name === 'ElTable' && c.subTree && c.subTree.el === el) tables.push(c);
        walk(c.subTree, depth + 1);
      }
      if (Array.isArray(v.children)) for (var k = 0; k < v.children.length; k++) walk(v.children[k], depth + 1);
      if (v.suspense && v.suspense.activeBranch) walk(v.suspense.activeBranch, depth + 1);
    }
    walk(app && app._vnode, 0);
    var data = tables.length === 1 && seen < 200000 && tables[0].props ? tables[0].props.data : null;
    if (!Array.isArray(data)) return no('the page data of the instance table was not found; the console may have changed');
    var mine = data.filter(function (r) { return !!r && typeof r === 'object' && r.uuid === id; });
    if (mine.length === 0) return no('the page data holds no entry for this instance ID');
    if (mine.length > 1) return no('the page data holds this instance ID more than once');
    var host = mine[0].proxy_host;
    var port = mine[0].ssh_port;
    var cmd = mine[0].ssh_command;
    if (typeof host !== 'string' || host.length > 253 || !HOST_NAME.test(host) || !SSH_DOMAIN.test(host)) {
      return no('the SSH host in the page data is not a host name under seetacloud.com or autodl.com');
    }
    if (typeof port !== 'number' || port % 1 !== 0 || port < 1 || port > 65535) return no('the SSH port in the page data is not a port number');
    if (cmd !== 'ssh -p ' + port + ' root@' + host) return no('the SSH command in the page data does not agree with its host and port');
    return { ok: true, id: id, host: host, port: port };
  }
  NAMES = NAMES.concat(['startClone', 'bindCloneDialog', 'tickCloneDataDisk', 'continueClone', 'idDigests', 'findCreated', 'sshAddress']);
  built.clone = true;
  built.startClone = startClone;
  built.bindCloneDialog = bindCloneDialog;
  built.tickCloneDataDisk = tickCloneDataDisk;
  built.continueClone = continueClone;
  built.idDigests = idDigests;
  built.findCreated = findCreated;
  built.sshAddress = sshAddress;
  // clone-end

  return register(Object.freeze(built));
})
