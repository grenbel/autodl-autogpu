// Offline fixtures for reference/console.js: fake AutoDL instance-list pages built like the real console's structure
// (docs/tests/2026-10-01-console-fixtures.md) with every value fake, plus the console's behaviours (hover menus,
// confirm boxes, the timer dialog and its picker, rows that change after a confirm) as event handlers.
// Every DOM change made by these handlers or by the knobs below happens between enter() and leave(); a change outside
// them is a change made by the script under test and is reported by violations().
// Paste this file first, then tests/console/run.js. Nothing here talks to the network.
window.__fx = (function () {
  'use strict';
  var COLS = ['实例ID / 名称', '状态', '规格详情', '本地磁盘', '健康状态', '付费方式', '释放时间/停机时间', 'SSH登录', '快捷工具', '操作'];
  var MENU = ['无卡模式开机', '更换镜像', '保存镜像', '升降配置', '扩容数据盘', '缩容数据盘', '转包年包月', '克隆实例',
              '跨实例拷贝数据', '修改SSH密码', '重置系统', '释放实例'];
  var depth = 0;
  var violations = [];
  var mo = new MutationObserver(function () {});
  var queue = [];
  var counts = {};
  var opts = {};
  var rows = {};
  var seq = 0;

  function enter() { if (depth === 0) violations = violations.concat(mo.takeRecords()); depth++; }
  function leave() { depth--; if (depth === 0) mo.takeRecords(); }
  function quiet(fn) { enter(); try { return fn(); } finally { leave(); } }
  function count(key) { counts[key] = (counts[key] || 0) + 1; }
  function later(fn) { if (opts.delay === 'never') return; if (opts.delay === 'later') queue.push(fn); else fn(); }
  function h(html) { var t = document.createElement('template'); t.innerHTML = html; return t.content.firstElementChild; }
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
  function nextId(p) { seq++; return p + '-' + seq; }

  function header() {
    return '<div class="el-table__header-wrapper"><table class="el-table__header"><colgroup>' +
      COLS.map(function () { return '<col>'; }).join('') + '</colgroup><thead class="has-gutter"><tr>' +
      COLS.map(function (c, i) {
        var inner = c === '释放时间/停机时间' ? '<span>' + c + '</span><span class="tip-icon" aria-describedby="hdr-tip"></span>'
          : (c === '状态' || c === '付费方式') ? esc(c) + '<span class="filter" aria-describedby="hdr-filter-' + i + '">' +
            '<i class="el-icon-arrow-down"></i></span>' : c === '规格详情' && opts.noSpecCol ? '规格' : esc(c);
        var n = opts.dupCol && c === '操作' ? 2 : i + 1;
        return '<th class="el-table_1_column_' + n + ' is-leaf el-table__cell"><div class="cell">' + inner + '</div></th>';
      }).join('') + '</tr></thead></table></div>';
  }
  function statusCell(r) {
    return '<div class="cell"><div class="dot"></div><div><div class="state"><span>' + r.state + '</span>' +
      (r.mode === 'nogpu' && r.state !== '已关机' ? '<span class="mode">无卡模式</span>' : '') + '</div>' +
      '<div class="gpu">' + (r.gpuFree ? '<span class="ok">GPU充足</span>' : '') + '</div></div></div>';
  }
  function timerCell(r) {
    var inner = r.timer
      ? '<span><span>' + r.timer + '</span></span><button type="button" class="el-button el-button--text el-button--small t-edit">' +
        '<span>修改</span></button><button type="button" class="el-button el-button--text el-button--small t-off"><span>关闭</span></button>'
      : '<span><span>' + (r.release || '') + '</span></span><button type="button" ' +
        'class="el-button el-button--text el-button--small t-set"><span>设置定时关机</span></button>';
    return '<div class="cell"><div><div class="release">' + inner + '</div></div></div>';
  }
  // Attributes put on the wrapper of a row's operation buttons, to hide or block them in one way each.
  var WRAP = { 'hidden': ' hidden style="display:block"', 'aria-hidden': ' aria-hidden="true"', 'inert': ' inert',
               'pe-none': ' style="pointer-events:none"', 'vis-hidden': ' style="visibility:hidden"' };
  function opsCell(r) {
    var label = r.ops || (r.state === '已关机' ? '开机' : '关机');
    return '<div class="cell"><div' + (WRAP[r.wrap] || '') + '><button type="button"' + (r.disabled ? ' disabled' : '') +
      ' class="el-button el-button--text el-button--small op ' + (label === '开机' ? 'op-on' : 'op-off') +
      (r.disabled ? ' is-disabled' : '') + '"><span>' + label + '</span></button>' +
      '<div class="el-dropdown" aria-describedby="' + r.popper + '"><button type="button" aria-controls="' + r.menu + '" ' +
      'role="button" class="el-button el-button--text el-button--small op el-dropdown-selfdefine"><span>更多</span></button>' +
      '</div></div></div>';
  }
  function plainCell(n, inner) {
    return '<td class="el-table_1_column_' + n + ' el-table__cell"><div class="cell">' + inner + '</div></td>';
  }
  // How a row's region names its host popover: the console's own spelling (plain), the standard one (hyphen), both
  // naming the same popover (both) or two (differ), none, or an id nothing has (missing). The other kinds change the
  // popover itself (see hostHtml).
  function regionRef(r) {
    var k = r.hostRef;
    if (k === 'none') return '';
    if (k === 'hyphen') return ' aria-describedby="' + r.host + '"';
    if (k === 'both') return ' aria-describedby="' + r.host + '" ariadescribedby="' + r.host + '"';
    if (k === 'differ') return ' aria-describedby="' + r.host + '" ariadescribedby="' + r.host + '-x"';
    if (k === 'missing') return ' ariadescribedby="' + r.host + '-gone"';
    return ' ariadescribedby="' + r.host + '"';
  }
  function specInner(r) {
    return '<div><div>' + esc(r.spec) + '</div>' + (r.specExtra ? '<div>DECOY-SPEC</div>' : '') +
      '<button type="button" class="el-button el-button--text el-button--small"><span>查看详情</span></button></div>';
  }
  function rowHtml(r) {
    return '<tr class="el-table__row" data-fx="' + r.key + '">' +
      plainCell(1, '<div><div class="region"' + regionRef(r) + '>DECOY-REGION<span>DECOY-HOST</span></div><div></div>' +
        '<div class="iid">' + r.id + '</div>' +
        '<button type="button" class="el-button el-button--text el-button--small"><span>' + esc(r.name) + '</span></button>' +
        r.extra + '<div><span></span></div></div>') +
      '<td class="el-table_1_column_2 el-table__cell">' + statusCell(r) + '</td>' +
      plainCell(3, specInner(r)) +
      plainCell(4, '<div><div><span>DECOY-DISK</span><span>12.3%</span></div></div>') +
      plainCell(5, '<div class="dot"></div><div><span>正常</span></div>') +
      plainCell(6, '<div><div><span>按量计费</span></div></div>') +
      '<td class="el-table_1_column_7 el-table__cell">' + timerCell(r) + '</td>' +
      plainCell(8, '<div></div>') + plainCell(9, '<div></div>') +
      '<td class="el-table_1_column_10 el-table__cell">' + opsCell(r) + '</td></tr>';
  }
  function popperHtml(r) {
    return '<div class="el-popper is-light dd" id="' + r.popper + '" role="tooltip" aria-hidden="true" style="display:none">' +
      '<div class="el-scrollbar"><div class="el-scrollbar__wrap"><ul class="el-scrollbar__view"><ul class="el-dropdown-menu" id="' +
      r.menu + '">' + MENU.map(function (t, k) {
        return '<div><li class="el-dropdown-menu__item" aria-describedby="' + r.menu + '-tip-' + k + '" aria-disabled="false">' +
          '<span>' + t + '</span></li></div>';
      }).join('') + '</ul></ul></div></div></div>' + MENU.map(function (t, k) {
        return '<div class="el-tooltip__popper is-dark" id="' + r.menu + '-tip-' + k + '" role="tooltip" aria-hidden="true" ' +
          'style="display:none">DECOY-TIP</div>';
      }).join('');
  }
  // The hidden host popover of a row, as the console renders one per row: only its GPU line may ever be read. hostRef
  // dup renders it twice with the same id, nolabel without the GPU line, twolabels with it twice, notpopper without the
  // el-popper class.
  function hostHtml(r) {
    function line(k, v) { return '<div><span>' + k + '</span><span>' + v + '</span></div>'; }
    var gpu = r.hostRef === 'nolabel' ? '' : line('GPU空闲/总量：', esc(r.idle));
    var body = line('主机名称：', 'DECOY-HOSTNAME') + line('可租用至：', 'DECOY-DATE') + line('数据盘可扩容：', 'DECOY-GB') + gpu +
      (r.hostRef === 'twolabels' ? gpu : '') + line('GPU驱动：', 'DECOY-DRIVER') + line('CUDA版本：', 'DECOY-CUDA');
    var one = '<div class="el-popover seeta-popover instance-popper' + (r.hostRef === 'notpopper' ? '' : ' el-popper') +
      ' is-light" id="' + r.host + '" role="tooltip" style="display:none">' + body + '</div>';
    return r.hostRef === 'dup' ? one + one : one;
  }
  var CSS = '<style>body{margin:0;font:11px/1.2 sans-serif}.el-table td,.el-table th{padding:0 2px;white-space:nowrap}' +
    '.el-button{font-size:11px;padding:0 2px}.el-popper,.el-tooltip__popper{position:absolute;top:150px;left:10px;' +
    'z-index:2500;background:#fff;border:1px solid #ccc}.el-dropdown-menu{list-style:none;margin:0;padding:0}' +
    '.el-message-box__wrapper,.el-overlay-dialog{position:fixed;top:0;left:0;right:0;bottom:0;z-index:2000}' +
    '.el-message-box,.el-dialog{position:absolute;top:40px;left:40px;width:460px;background:#fff;border:1px solid #888}' +
    '.el-picker-panel{position:fixed;top:250px;left:60px;z-index:3000;background:#fff;border:1px solid #888}' +
    '.el-message{position:fixed;top:5px;left:200px;z-index:4000}</style>';
  // Hidden boxes left over from earlier operations: a reset confirm, a timer dialog and a picker.
  function prerendered() {
    return '<div class="el-message-box__wrapper" style="display:none"><div class="el-message-box"><div class="el-message-box__content">' +
      '<div class="el-message-box__message"><p>重置系统将清空系统盘，确定重置吗？</p></div></div><div class="el-message-box__btns">' +
      '<button type="button" class="el-button el-button--default el-button--small"><span>取消</span></button>' +
      '<button type="button" class="el-button el-button--default el-button--small el-button--primary"><span>确定重置</span></button>' +
      '</div></div></div>' + dialogHtml(true) + pickerHtml(true);
  }
  function norm(s) { return String(s).replace(/\s+/g, ' ').trim(); }
  function rowEl(key) { return document.querySelector('tr.el-table__row[data-fx="' + key + '"]'); }
  function rerender(key) {
    var r = rows[key];
    var tr = rowEl(key);
    if (!tr) return;
    tr.cells[1].innerHTML = statusCell(r);
    tr.cells[2].innerHTML = '<div class="cell">' + specInner(r) + '</div>';
    tr.cells[6].innerHTML = timerCell(r);
    tr.cells[9].innerHTML = opsCell(r);
    var p = document.getElementById(r.host);
    var lab = p ? Array.prototype.filter.call(p.querySelectorAll('span'), function (s) { return s.textContent === 'GPU空闲/总量：'; }) : [];
    if (lab.length) lab[0].nextElementSibling.textContent = r.idle;
  }
  var frozen = [];
  function freeze(el) { if (opts.freeze) { el.style.opacity = '0'; frozen.push(el); } }
  function openBox(kind, r, text) {
    var w = h('<div class="el-message-box__wrapper" tabindex="-1" role="dialog" aria-modal="true" aria-label="提示">' +
      '<div class="el-message-box"><div class="el-message-box__header"><div class="el-message-box__title"><span>提示</span></div>' +
      '<button type="button" class="el-message-box__headerbtn"><i class="el-message-box__close el-icon-close"></i></button></div>' +
      '<div class="el-message-box__content"><div class="el-message-box__container"><div class="el-message-box__message"><p>' +
      esc(text) + '</p></div></div></div><div class="el-message-box__btns">' +
      '<button type="button" class="el-button el-button--default el-button--small"><span>取消</span></button>' +
      '<button type="button" class="el-button el-button--default el-button--small el-button--primary"><span>确定</span></button>' +
      '</div></div></div>');
    w.__fx = { kind: kind, key: r.key };
    freeze(w);
    document.body.appendChild(w);
    return w;
  }
  function boxText(kind) {
    return {
      'gpu-on': '确认开机吗？ 实例将按￥0.98/时整点扣费',
      'nogpu-on': '确认无卡模式开机吗？ 无卡模式配置为:0.5核心CPU，2GB内存，无GPU卡 配置费用:￥0.10/时，最低消费￥0.01',
      'cancel-timer': '取消定时关机',
      'power-off': '确认关机吗？',
      'release': '释放实例后数据将被清除，确定释放吗？'
    }[kind];
  }
  // The timer dialog as the console renders it (Element Plus, seen in Task 6.3): the title is a plain span in the header,
  // each value sits in the content cell after its label, and the server time (without seconds) has a tip after it in
  // the same cell. The balance and the voucher are decoys: nothing may return them.
  function dialogHtml(hidden) {
    function item(label, content) {
      return '<div class="el-form-item"><label class="el-form-item__label">' + label + '</label><div class="el-form-item__content">' +
        content + '</div></div>';
    }
    return '<div class="el-overlay-dialog"' + (hidden ? ' style="display:none"' : '') + '><div class="el-dialog" aria-modal="true" ' +
      'role="dialog" aria-label="dialog">' + (opts.dialogRootNote ? esc(opts.dialogRootNote) : '') +
      '<div class="el-dialog__header"><span>' + esc(opts.dialogTitle) + '</span><button type="button" class="el-dialog__headerbtn">' +
      '<i class="el-dialog__close el-icon el-icon-close"></i></button></div><div class="el-dialog__body"><form class="el-form">' +
      item('当前服务器时间：', '<span>' + esc(opts.serverTime) + '</span><div class="form-tip">如和您本地时间有差异，以下定时关机时间请以服务器时间为准</div>') +
      item('定时关机时间：', '<div class="el-input el-input--small el-input--prefix el-input--suffix el-date-editor el-date-editor--datetime">' +
        '<input type="text" autocomplete="off" class="el-input__inner"><span class="el-input__prefix"><i class="el-input__icon ' +
        'el-icon-time"></i></span><span class="el-input__suffix"><span class="el-input__suffix-inner"><i class="el-input__icon"></i>' +
        '</span></span></div>') +
      item('预计消费：', '<span class="cost">选择时间后计算费用</span>') + item('账户余额：', '￥ DECOY-BALANCE') +
      item('账户代金券：', '￥ DECOY-VOUCHER') + (opts.dialogNote ? '<div><span>' + esc(opts.dialogNote) + '</span></div>' : '') +
      '</form></div><div class="el-dialog__footer"><button type="button" class="el-button el-button--default el-button--small">' +
      '<span>取消</span></button><button type="button" class="el-button el-button--primary el-button--small"><span>确定</span></button>' +
      '</div></div></div>';
  }
  function pickerHtml(hidden) {
    return '<div class="el-picker-panel el-date-picker el-popper has-time"' + (hidden ? ' style="display:none"' : '') + '>' +
      '<div class="el-picker-panel__body-wrapper"><div class="el-picker-panel__body"><div class="el-date-picker__time-header">' +
      '<span class="el-date-picker__editor-wrap"><div class="el-input el-input--small"><input type="text" autocomplete="off" ' +
      'placeholder="选择日期" class="el-input__inner"></div></span><span class="el-date-picker__editor-wrap"><div class="el-input ' +
      'el-input--small"><input type="text" autocomplete="off" placeholder="选择时间" class="el-input__inner"></div></span></div></div>' +
      '</div><div class="el-picker-panel__footer"><button type="button" class="el-button el-picker-panel__link-btn el-button--text ' +
      'el-button--mini"><span>此刻</span></button><button type="button" class="el-button el-picker-panel__link-btn ' +
      'el-button--default el-button--mini is-plain"><span>确定</span></button></div></div>';
  }
  // The console's date editor shows a time to the minute (seen 2026-10-02: "2026-10-02 02:16"), while the row shows the
  // timer with its seconds. spec.editorSeconds makes the editor show the seconds as well.
  function editorText(full) { return opts.editorSeconds ? full : full.slice(0, 16); }
  function rowTime(text) { return /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(text) ? text + ':00' : text; }
  // Like the console, closing the picker takes the focus off the date editor.
  function closePicker(panel) {
    var ed = panel.__fx ? panel.__fx.dialog.querySelector('.el-date-editor input') : null;
    panel.remove();
    if (ed && document.activeElement === ed) ed.blur();
  }
  function shown(sel) {
    var all = document.querySelectorAll(sel);
    for (var i = 0; i < all.length; i++) if (all[i].style.display !== 'none') return all[i];
    return null;
  }
  function openDialog(r) {
    var w = h(dialogHtml(false));
    w.__fx = { kind: 'timer', key: r.key };
    if (r.timer) w.querySelector('input').value = editorText(r.timer);
    freeze(w);
    document.body.appendChild(w);
  }
  function openPicker(dlg) {
    if (shown('.el-picker-panel')) return;
    var p = h(pickerHtml(false));
    p.__fx = { dialog: dlg };
    // Like the console's picker, it opens on the editor's current value.
    var m = /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})(?::\d{2})?$/.exec(dlg.querySelector('.el-date-editor input').value);
    if (m) { p.querySelectorAll('input')[0].value = m[1]; p.querySelectorAll('input')[1].value = m[2]; }
    freeze(p);
    document.body.appendChild(p);
  }
  function ownerOfMenu(ul) {
    for (var k in rows) if (rows[k].menu === ul.id) return rows[k];
    return null;
  }
  function showMenu(r, on) {
    var p = document.getElementById(r.popper);
    if (!p) return;
    p.style.display = on ? '' : 'none';
    p.setAttribute('aria-hidden', on ? 'false' : 'true');
    if (on) freeze(p);
  }
  function hover(d, on) {
    var p = document.getElementById(d.getAttribute('aria-describedby'));
    var r = null;
    for (var k in rows) if (rows[k].popper === (p && p.id)) r = rows[k];
    if (!r) return;
    count(r.key + (on ? ':hover' : ':leave'));
    if (on && opts.menuDelay === 'later') queue.push(function () { showMenu(r, true); });
    else showMenu(r, on);
  }
  function effect(kind, key) {
    later(function () {
      var r = rows[key];
      if (kind === 'gpu-on') { r.state = '开机中'; r.mode = 'gpu'; }
      if (kind === 'nogpu-on') { r.state = '开机中'; r.mode = 'nogpu'; }
      if (kind === 'power-off') { r.state = '关机中'; }
      if (kind === 'cancel-timer') { r.timer = null; }
      if (kind === 'timer') { r.timer = r.pendingTimer; }
      rerender(key);
    });
  }
  // A confirm box opens now, after the next tick, or never (spec.boxDelay).
  function boxSoon(kind, r) {
    if (opts.boxDelay === 'never') return;
    if (opts.boxDelay === 'later') { queue.push(function () { openBox(kind, r, boxText(kind)); }); return; }
    openBox(kind, r, boxText(kind));
  }
  function onClick(t) {
    var b = t.closest && t.closest('button, li');
    if (!b) return;
    var label = norm(b.textContent);
    var tr = b.closest('tr.el-table__row');
    var r = tr ? rows[tr.getAttribute('data-fx')] : null;
    if (r) {
      count(r.key + ':' + label);
      if (b.classList.contains('op-on')) return boxSoon('gpu-on', r);
      if (b.classList.contains('op-off')) return boxSoon('power-off', r);
      if (b.classList.contains('t-off')) return boxSoon('cancel-timer', r);
      if (b.classList.contains('t-set') || b.classList.contains('t-edit')) return openDialog(r);
      return;
    }
    if (b.tagName === 'LI') {
      var owner = ownerOfMenu(b.closest('ul.el-dropdown-menu'));
      count((owner ? owner.key : '?') + ':menu:' + label);
      if (owner && label === '无卡模式开机') { showMenu(owner, false); boxSoon('nogpu-on', owner); }
      return;
    }
    var box = b.closest('.el-message-box__wrapper');
    if (box) {
      count('box:' + label);
      if (opts.ignore && label === '确定') return;
      if (label === '确定' && box.__fx) effect(box.__fx.kind, box.__fx.key);
      if (label === '取消' || (label === '确定' && !opts.keepBox)) box.remove();
      return;
    }
    var dlg = b.closest('.el-overlay-dialog');
    if (dlg) {
      count('dialog:' + label);
      if (label === '确定' && dlg.__fx) rows[dlg.__fx.key].pendingTimer = rowTime(dlg.querySelector('.el-date-editor input').value);
      if (label === '取消' || label === '确定') dlg.remove();
      if (label === '确定' && dlg.__fx) effect('timer', dlg.__fx.key);
      return;
    }
    var panel = b.closest('.el-picker-panel');
    if (panel) {
      count('picker:' + label);
      var ins = panel.querySelectorAll('input');
      if (label === '此刻') { ins[0].value = opts.serverTime.slice(0, 10); ins[1].value = opts.serverTime.slice(11, 16); }
      if (label === '确定' && opts.pickerIgnore) { closePicker(panel); return; }
      if (label === '确定' && panel.__fx) {
        var ed = panel.__fx.dialog.querySelector('.el-date-editor input');
        ed.value = ins[0].value + ' ' + ins[1].value + (opts.editorSeconds ? ':00' : '');
        panel.__fx.dialog.querySelector('.cost').textContent = '￥0.03';
        closePicker(panel);
      }
    }
  }
  function onFocus(t) {
    if (!(t.matches && t.matches('.el-date-editor input'))) return;
    var dlg = t.closest('.el-overlay-dialog');
    if (opts.pickerDelay === 'later') queue.push(function () { openPicker(dlg); });
    else openPicker(dlg);
  }
  var installed = false;
  function install() {
    document.addEventListener('mouseenter', function (e) {
      var d = e.target.closest && e.target.closest('.el-dropdown');
      if (d) quiet(function () { hover(d, true); });
    }, true);
    document.addEventListener('mouseleave', function (e) {
      var d = e.target.closest && e.target.closest('.el-dropdown');
      if (d) quiet(function () { hover(d, false); });
    }, true);
    document.addEventListener('click', function (e) { quiet(function () { onClick(e.target); }); }, false);
    document.addEventListener('focus', function (e) { quiet(function () { onFocus(e.target); }); }, true);
    installed = true;
  }

  // spec: {rows: [{id, state, mode, gpuFree, timer, release, name, extra, ops, disabled, wrap, spec, specExtra, idle, hostRef}],
  //        delay: 'now'|'later'|'never', menuDelay, boxDelay, pickerDelay, freeze, ignore, keepBox, pickerIgnore, dialogNote,
  //        dialogRootNote, dialogTitle, dupCol, noSpecCol, boxes: 'prerendered', login, outside, shift, noTable, serverTime,
  //        editorSeconds}
  // spec is the text of the row's 规格详情 cell (specExtra adds a second text), idle the free/total GPUs of its host
  // popover and hostRef how the row names that popover (see regionRef and hostHtml); noSpecCol renames the 规格详情 header.
  // delay: when a confirmed box changes the row; menuDelay and pickerDelay 'later' show the menu or the picker after the
  // next tick; boxDelay 'later' or 'never' does the same for confirm boxes. ops overrides the label of the row's first
  // operation button; wrap hides or blocks the row's operation buttons in one way (see WRAP). ignore makes a box's 确定
  // do nothing; keepBox applies it but leaves the box open; pickerIgnore makes the picker's 确定 close it without
  // filling the editor. dialogNote adds a line to the timer dialog's body, dialogRootNote text right in its root node;
  // serverTime is what the timer dialog shows as the server time (the console shows no seconds), dialogTitle the title in
  // its header (the console's is 定时关机); editorSeconds makes the date editor show seconds, which the console's does not;
  // dupCol gives 操作 the column class of 状态; shift
  // moves the table out of the viewport; noTable leaves the instance table out. Every page also has a zone of decoy
  // elements that no handler serves, so a click on them shows only in the effect log of tests/console/run.js.
  function mount(spec) {
    return quiet(function () {
      opts = { delay: spec.delay || 'now', menuDelay: spec.menuDelay || 'now', boxDelay: spec.boxDelay || 'now',
               pickerDelay: spec.pickerDelay || 'now', freeze: !!spec.freeze, ignore: !!spec.ignore, keepBox: !!spec.keepBox,
               pickerIgnore: !!spec.pickerIgnore, dialogNote: spec.dialogNote || '', dialogRootNote: spec.dialogRootNote || '',
               dupCol: !!spec.dupCol, noSpecCol: !!spec.noSpecCol, serverTime: spec.serverTime || '2026-10-01 23:58',
               dialogTitle: spec.dialogTitle || '定时关机', editorSeconds: !!spec.editorSeconds };
      queue = [];
      counts = {};
      rows = {};
      frozen = [];
      var list = spec.rows.map(function (s, i) {
        var r = { key: 'r' + i, id: s.id, state: s.state || '已关机', mode: s.mode || 'gpu', gpuFree: !!s.gpuFree,
                  timer: s.timer || null, release: s.release === undefined ? '14天23小时58分后释放' : s.release,
                  name: s.name || ('DECOY-NAME-' + i), extra: s.extra || '', ops: s.ops || null, disabled: !!s.disabled,
                  wrap: s.wrap || '', menu: nextId('dropdown-menu'), popper: nextId('el-popper'),
                  spec: s.spec === undefined ? 'RTX 0000 * 1卡' : s.spec, specExtra: !!s.specExtra,
                  idle: s.idle === undefined ? '1/8' : s.idle, hostRef: s.hostRef || 'plain', host: nextId('host-popper') };
        rows[r.key] = r;
        return r;
      });
      var table = spec.noTable ? '' : '<div class="el-table el-table--fit el-table--striped el-table--enable-row-hover ' +
        'el-table--enable-row-transition instance-table">' + header() + '<div class="el-table__body-wrapper is-scrolling-none">' +
        '<table class="el-table__body"><tbody>' + list.map(rowHtml).join('') + '</tbody></table></div></div>';
      document.body.innerHTML = CSS + (spec.shift ? '<style>.instance-table{margin-left:4000px}</style>' : '') +
        (spec.login ? '<form class="login"><input type="password" value="DECOY-PW"></form>' : '') +
        '<div id="app"><div class="main">' + table + (spec.outside || '') +
        '<div class="decoy-zone"><button type="button" class="el-button el-button--text el-button--small">开机</button>' +
        '<button type="button" aria-controls="decoy-menu" role="button" class="el-button el-button--text">更多</button>' +
        '<input type="text" placeholder="选择日期" class="el-input__inner"><span>DECOY-ZONE</span></div>' +
        '<input type="password" style="display:none" value="DECOY-PW2"></div></div>' +
        list.map(popperHtml).join('') + list.map(hostHtml).join('') + (spec.boxes === 'prerendered' ? prerendered() : '');
      if (!installed) install();
      mo.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
      violations = [];
      return list.map(function (r) { return r.key; });
    });
  }
  function keyOf(id) { for (var k in rows) if (rows[k].id === id) return k; return null; }

  return {
    mount: mount,
    busy: function () { return depth > 0; },
    // Records of DOM changes made outside the fixture's own handlers since the last call.
    violations: function () { var v = violations.concat(depth === 0 ? mo.takeRecords() : []); violations = []; return v; },
    counts: function () { return JSON.parse(JSON.stringify(counts)); },
    tick: function () { quiet(function () { var q = queue; queue = []; q.forEach(function (fn) { fn(); }); }); },
    frame: function () { quiet(function () { frozen.forEach(function (e) { e.style.opacity = ''; }); frozen = []; }); },
    type: function (input, text) { quiet(function () { input.value = text; }); },
    set: function (id, fields) { quiet(function () { var k = keyOf(id); Object.assign(rows[k], fields); rerender(k); }); },
    replaceRow: function (id) {
      quiet(function () { var tr = rowEl(keyOf(id)); var n = h('<table><tbody>' + rowHtml(rows[keyOf(id)]) + '</tbody></table>');
        tr.replaceWith(n.querySelector('tr')); });
    },
    removeRow: function (id) { quiet(function () { rowEl(keyOf(id)).remove(); }); },
    reverseRows: function () { quiet(function () { var tb = document.querySelector('.el-table__body tbody');
      Array.prototype.slice.call(tb.children).reverse().forEach(function (tr) { tb.appendChild(tr); }); }); },
    openBox: function (id, kind) { quiet(function () { openBox(kind, rows[keyOf(id)], boxText(kind)); }); },
    // The shown box is replaced by a new box with the same text (swapBox) or with the text of another kind (replaceBox).
    swapBox: function () {
      quiet(function () { var w = shown('.el-message-box__wrapper'); var f = w.__fx; w.remove(); openBox(f.kind, rows[f.key], boxText(f.kind)); });
    },
    replaceBox: function (kind) {
      quiet(function () { var w = shown('.el-message-box__wrapper'); var f = w.__fx; w.remove(); openBox(kind, rows[f.key], boxText(kind)); });
    },
    showMenu: function (id) { quiet(function () { showMenu(rows[keyOf(id)], true); }); },
    // A second, hidden copy of the row's menu with the same ids.
    dupMenu: function (id) {
      quiet(function () { var t = document.createElement('template'); t.innerHTML = popperHtml(rows[keyOf(id)]);
        document.body.appendChild(t.content); });
    },
    toast: function (text) { quiet(function () { document.body.appendChild(h('<div class="el-message el-message--error" ' +
      'role="alert"><p class="el-message__content">' + esc(text) + '</p></div>')); }); },
    closeBox: function () { quiet(function () { var w = shown('.el-message-box__wrapper'); if (w) w.remove(); }); },
    // The shown box keeps its node but gets another message, as a page reusing its box would do.
    retextBox: function (text) {
      quiet(function () { shown('.el-message-box__wrapper').querySelector('.el-message-box__message p').textContent = text; });
    },
    // The shown picker is replaced by a new one for the same dialog; or an unrelated picker is added; or all are removed.
    swapPicker: function () {
      quiet(function () { var p = shown('.el-picker-panel'); var d = p.__fx ? p.__fx.dialog : null; p.remove();
        var n = h(pickerHtml(false)); n.__fx = { dialog: d }; document.body.appendChild(n); });
    },
    extraPicker: function () {
      quiet(function () { var n = h(pickerHtml(false)); n.style.top = '330px'; document.body.appendChild(n); });
    },
    removePickers: function () {
      quiet(function () { Array.prototype.slice.call(document.querySelectorAll('.el-picker-panel')).forEach(function (p) {
        if (p.style.display !== 'none') p.remove(); }); });
    },
    // A transparent cover over the top left of the row's first operation button: not its center, but one sample point.
    coverPart: function (id) {
      quiet(function () { var b = rowEl(keyOf(id)).cells[9].querySelector('button'); var r = b.getBoundingClientRect();
        var d = document.createElement('div');
        d.style.cssText = 'position:fixed;z-index:1500;left:' + r.left + 'px;top:' + r.top + 'px;width:' + (r.width * 0.4) +
          'px;height:' + (r.height * 0.4) + 'px';
        document.body.appendChild(d); });
    },
    picker: function () { return shown('.el-picker-panel'); },
    editor: function () { var w = shown('.el-overlay-dialog'); return w ? w.querySelector('.el-date-editor input') : null; },
    row: function (id) { return rowEl(keyOf(id)); },
    state: function (id) { var r = rows[keyOf(id)]; return r ? { state: r.state, mode: r.mode, timer: r.timer } : null; },
    // For the effect log: which row an ID is in, whether its menu shows, which kind of box shows, and a name for an element.
    keyOf: keyOf,
    menuShown: function (id) {
      var r = rows[keyOf(id)]; var p = r && document.getElementById(r.popper);
      return !!p && p.style.display !== 'none' && p.style.opacity !== '0';
    },
    shownKind: function () { return shown('.el-message-box__wrapper') ? 'box' : shown('.el-overlay-dialog') ? 'dialog' : null; },
    describe: function (el) {
      var t = norm(el.textContent);
      var tr = el.closest('tr.el-table__row');
      if (tr) return tr.getAttribute('data-fx') + ':' + t;
      if (el.tagName === 'LI') { var o = ownerOfMenu(el.closest('ul.el-dropdown-menu')); return (o ? o.key : '?') + ':menu:' + t; }
      if (el.closest('.el-message-box__wrapper')) return 'box:' + t;
      if (el.closest('.el-overlay-dialog')) return 'dialog:' + (el.tagName === 'INPUT' ? 'editor' : t);
      if (el.closest('.el-picker-panel')) return 'picker:' + (el.tagName === 'INPUT' ? el.getAttribute('placeholder') : t);
      return 'other:' + el.tagName.toLowerCase() + ':' + t;
    }
  };
})();
