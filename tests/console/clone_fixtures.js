// Offline fixtures for reference/clone-page.js: a fake copy of the console's page that creates a cloned instance, built
// like the real one (read on 2026-10-03, docs/tests/2026-10-03-clone-dryrun.md) with every value fake, plus the page's
// behaviours as event handlers: ticking a model shows its hosts and selects the first that can be selected, the table
// shows ten rows and more once its body is scrolled to the end, selecting a host changes the spec, the room to expand
// and the fees, the daily fee follows a change of the expansion only after a while, and 创建并开机 ends in one of four ways.
// Every DOM change made by these handlers or by the knobs below happens between enter() and leave(); a change outside
// them is a change made by the script under test and is reported by violations().
// Paste this file first, then tests/console/clone_run.js. Nothing here talks to the network.
window.__cfx = (function () {
  'use strict';
  var SRC = 'abcd123456-1234abcd';
  var GIB = 1073741824;
  var HEAD = ['', '主机ID', '算力型号/显存', '空闲GPU', '每GPU分配', 'CPU型号', '硬盘', '驱动/CUDA', '价格(单卡)'];
  var depth = 0;
  var violations = [];
  var mo = new MutationObserver(function () {});
  var queue = [];
  var counts = {};
  var st = null;
  var installed = false;

  function enter() { if (depth === 0) violations = violations.concat(mo.takeRecords()); depth++; }
  function leave() { depth--; if (depth === 0) mo.takeRecords(); }
  function quiet(fn) { enter(); try { return fn(); } finally { leave(); } }
  function count(key) { counts[key] = (counts[key] || 0) + 1; }
  function h(html) { var t = document.createElement('template'); t.innerHTML = html; return t.content.firstElementChild; }
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
  function norm(s) { return String(s).replace(/\s+/g, ' ').trim(); }

  // The hosts of the fake region. Those of RTX 0000 follow what was seen for a real model: eleven hosts of eight GPUs,
  // three with a free one, two of those with an older driver (on the real page they had a newer one; the test of the
  // rule wants hosts that stay unsuitable); the source's own host (abcd123456) is full.
  function host(id, alias, free, more) {
    var x = { id: id, alias: alias, model: 'RTX 0000', vram: 12, free: free, total: 8, cpu: 10, mem: 45, cpuModel: 'Fake(R) Gold 0000',
              disk: 50, expand: 6998, driver: '580.105.08', cuda: '13.0', price: '0.98', list: '1.03', locked: false, lie: null };
    Object.keys(more || {}).forEach(function (k) { x[k] = more[k]; });
    return x;
  }
  function defaultHosts() {
    return [
      host('efgh000001', '301机', 2, { model: 'vGPU-00GB', vram: 32, total: 16, cpu: 12, mem: 62, cpuModel: 'Fake(R) Platinum 0000',
                                     expand: 2266, price: '1.58', list: '1.68' }),
      host('wxyz000001', '101机', 1),
      host('wxyz000002', '102机', 2, { driver: '570.86.10', cuda: '12.8', expand: 6077 }),
      host('wxyz000003', '103机', 3, { driver: '570.124.04', cuda: '12.8', expand: 5542 }),
      host('mnop000004', '104机', 0, { driver: '595.71.05', cuda: '13.2', expand: 4692 }),
      host('abcd123456', '105机', 0, { expand: 2881 }),
      host('mnop000006', '106机', 0, { expand: 6244 }),
      host('mnop000007', '107机', 0, { expand: 6298 }),
      host('mnop000008', '108机', 0, { expand: 6689 }),
      host('mnop000009', '109机', 0, { expand: 3927 }),
      host('qrst000010', '110机', 0, { expand: 8000 }),
      host('qrst000011', '111机', 0, { expand: 5000 })
    ];
  }
  function models() {
    var names = [];
    st.hosts.forEach(function (x) { if (names.indexOf(x.model) < 0) names.push(x.model); });
    return names.map(function (n) {
      var hs = st.hosts.filter(function (x) { return x.model === n; });
      var note = st.modelNote && st.modelNote[n];
      return { name: n, note: note || '(' + hs.reduce(function (a, x) { return a + x.free; }, 0) + '/' +
                                     hs.reduce(function (a, x) { return a + x.total; }, 0) + ')' };
    }).concat([{ name: 'CPU', note: '(2/48)' }]);
  }
  function listed() { return st.hosts.filter(function (x) { return !st.ticked.length || st.ticked.indexOf(x.model) >= 0; }); }
  function chosen() { return st.hosts.filter(function (x) { return x.id === st.selected; })[0] || null; }
  function canSelect(x) { return x.free >= st.count && !x.locked; }
  // The daily fee of gb of paid data disk as the console shows it: 0.0066 yuan per GB, rounded to the fen.
  function dailyOf(gb) {
    var tenth = gb * 66;                      // in hundredths of a fen
    var fen = Math.floor(tenth / 100) + (tenth % 100 > 50 || (tenth % 100 === 50 && st.halfUp) ? 1 : 0);
    return (fen / 100).toFixed(2);
  }

  // ---- the page, block by block, as the console renders it ----
  function radioButton(text, value, active, disabled) {
    return '<label class="el-radio-button el-radio-button--small' + (active ? ' is-active' : '') + (disabled ? ' is-disabled' : '') +
      '" role="radio" aria-checked="' + (active ? 'true' : 'false') + '"' + (disabled ? ' aria-disabled="true"' : '') +
      ' tabindex="' + (active ? '0' : '-1') + '"><input class="el-radio-button__original-radio" type="radio" name=""' +
      (disabled ? ' disabled' : '') + ' tabindex="-1" value="' + esc(value) + '"><span class="el-radio-button__inner">' + esc(text) +
      '</span></label>';
  }
  function checkbox(value, inner, checked, cls) {
    var on = checked ? ' is-checked' : '';
    return '<label class="el-checkbox' + (cls || '') + on + '"><span class="el-checkbox__input' + on + '" aria-checked="false">' +
      '<span class="el-checkbox__inner"></span><input class="el-checkbox__original" type="checkbox" aria-hidden="false" value="' +
      esc(value) + '"></span><span class="el-checkbox__label">' + inner + '</span></label>';
  }
  function headHtml() {
    return '<div class="header"><div class="st-breadcrumb"><span class="st-breadcrumb-item"><span><a class="">容器实例</a></span>' +
      '<span class="st-breadcrumb-delimiter">/</span></span><span class="st-breadcrumb-item"><span>克隆实例</span>' +
      '<span class="st-breadcrumb-delimiter">/</span></span></div><span class="autodl-tip"><span class="iconfont"></span>' +
      '<span> DECOY-TIP </span></span></div><div class="el-alert el-alert--warning is-light header-alert" role="alert">' +
      '<i class="el-alert__icon el-icon-warning"></i><div class="el-alert__content"><span class="el-alert__title">' +
      '克隆实例仅能在同一地区内进行，请在下方选租新实例</span></div></div>' +
      '<div class="card"><div class="instanceID label">源实例:</div><div class="value"><div class="noMachine"><span>' + esc(st.source) +
      '</span></div></div></div>';
  }
  function billingHtml() {
    return '<div class="card billing"><div class="label"><span>计费方式:</span></div><div class="value"><div class="radio">' +
      '<div class="el-radio-group" role="radiogroup">' +
      [['按量计费', 'payg'], ['包日', 'daily'], ['包周', 'weekly'], ['包月', 'monthly']].map(function (b) {
        return radioButton(b[0], b[1], st.billing === b[1], false);
      }).join('') + '</div><a target="_blank">计费规则</a></div><span class="note">创建完主机后仍然可以转换计费方式。如选择按量计费，' +
      '价格发生变动以实例开机时的价格为准</span></div></div>';
  }
  function filterHtml() {
    return '<div class="filter-item label-center margin-b-24"><span class="filter-label">选择地区:</span><div class="el-radio-group" ' +
      'role="radiogroup">' + st.regions.map(function (r) { return radioButton(r[0] + ' ', r[1], r[2] === 'active', r[2] === 'disabled'); })
      .join('') + '</div></div><div class="filter-item second-region margin-b-24"><div class="el-radio-group" role="radiogroup">' +
      st.zones.map(function (r) { return radioButton(r[0], r[1], r[2] === 'active', r[2] === 'disabled'); }).join('') + '</div></div>' +
      '<div class="filter-item"><span class="filter-label">GPU型号:</span><div class="st-checkbox"><div>' +
      checkbox('false', '全部', st.all, '') + '</div><div class="el-checkbox-group" role="group" aria-label="checkbox-group">' +
      models().map(function (m) {
        return checkbox(m.name, '<span>' + esc(m.name) + '</span><span class="note"> ' + esc(m.note) + '</span>', st.ticked.indexOf(m.name) >= 0, '');
      }).join('') + '</div></div></div><div class="filter-item label-center"><span class="filter-label">GPU数量:</span>' +
      '<div class="el-radio-group" role="radiogroup">' + [1, 2, 3, 4, 5, 6, 7, 8, 10, 12].map(function (n) {
        return radioButton(String(n), String(n), st.count === n, false);
      }).join('') + '</div></div>';
  }
  function rowHtml(x, k) {
    var sel = st.selected === x.id;
    var dis = !canSelect(x);
    var c = function (n, inner) {
      return '<td class="el-table_2_column_' + (11 + n) + ' el-table__cell" rowspan="1" colspan="1"><div class="cell">' + inner + '</div></td>';
    };
    var lie = x.lie || {};
    return '<tr class="el-table__row' + (k % 2 ? ' el-table__row--striped' : '') + ' row-can-click">' +
      c(0, '<label class="el-radio' + (sel ? ' is-checked' : '') + (dis ? ' is-disabled' : '') + '" role="radio" aria-checked="' +
        (sel ? 'true' : 'false') + '"' + (dis ? ' aria-disabled="true"' : '') + ' tabindex="' + (sel ? '0' : '-1') + '">' +
        '<span class="el-radio__input' + (sel ? ' is-checked' : '') + (dis ? ' is-disabled' : '') + '"><span class="el-radio__inner"></span>' +
        '<input class="el-radio__original" type="radio" aria-hidden="true" name=""' + (dis ? ' disabled' : '') + ' tabindex="-1" value="' +
        esc(x.id) + '"></span><span class="el-radio__label">' + esc(lie.label || x.id) + '</span></label>') +
      c(1, '<div><div class="machine-id-column"><span class="machineId">' + esc(x.alias) + '</span></div></div>') +
      c(2, '<div>' + esc(x.model) + '<br>' + (lie.vram || x.vram + 'GB') + '</div>') +
      c(3, '<div><span style="font-weight: bold">' + (lie.free === undefined ? x.free : lie.free) + '</span> / ' + x.total + '</div>') +
      c(4, '<div>CPU：' + x.cpu + '核<br>内存：' + x.mem + 'GB</div>') +
      c(5, '<div>' + esc(x.cpuModel) + '</div>') +
      c(6, '<div>数据盘：' + x.disk + 'GB\n            <br>可扩容：' + (lie.expand || x.expand + 'GB') + '</div>') +
      c(7, '<div style="display: inline-block; vertical-align: top;"><div>驱动：' + esc(x.driver) + '</div><div> CUDA：' + esc(x.cuda) +
        '</div></div>') +
      c(8, '<div><div class="price-slot"><div class="price"><span>' + (lie.price || '￥' + x.price) + '</span>/时 <br>' +
        '<span style="text-decoration: line-through">￥' + x.list + '/时</span></div><div class="discount-msg"></div></div></div>') + '</tr>';
  }
  function tableHtml() {
    var rows = st.loading ? [] : listed().slice(0, st.loaded);
    return '<div class="label">选择主机:</div><div class="loading-box"><div class="table-box fixed-height"><div class="el-table--fit ' +
      'el-table--striped el-table--fluid-height el-table--scrollable-y el-table--enable-row-hover el-table lazy-load">' +
      '<div class="el-table__header-wrapper"><table class="el-table__header"><thead class="has-gutter"><tr>' +
      st.head.map(function (t, i) {
        return '<th class="el-table_2_column_' + (11 + i) + ' is-leaf el-table__cell"><div class="cell">' + esc(t) +
          (t === '空闲GPU' || t === '硬盘' ? '<span class="caret-wrapper"><i class="sort-caret ascending"></i></span>' : '') + '</div></th>';
      }).join('') + '</tr></thead></table></div><div class="el-table__body-wrapper"><table class="el-table__body"><tbody>' +
      rows.map(rowHtml).join('') + '</tbody></table></div></div>' + (st.loading ? '<div class="el-loading-mask"></div>' : '') +
      '</div></div>';
  }
  function diskHtml() {
    var x = chosen();
    var max = x ? x.expand : 0;
    var now = st.nowText !== null ? st.nowText : st.showNow ? String(st.expand) : null;
    return '<span class="label">数据盘:</span><div class="value"><div class="base"><span class="free">免费' + (x ? x.disk : 50) + 'GB</span>' +
      '<div class="el-checkbox-group needExpand" role="group" aria-label="checkbox-group">' +
      checkbox('需要扩容', '需要扩容', st.need, ' el-checkbox--small needExpand') + '</div></div><div><span class="disk-label"></span>' +
      '<div class="disk-right need-expand"><div><div class="el-input-number el-input-number--small is-without-controls disk-size">' +
      '<div class="el-input el-input--small"><input class="el-input__inner" max="' + max + '" min="0" type="text" autocomplete="off" ' +
      'placeholder="容量范围:0~' + max + '" role="spinbutton" aria-valuemax="0" aria-valuemin="0"' +
      (now === null ? '' : ' aria-valuenow="' + esc(now) + '"') + ' aria-disabled="undefined"></div></div><span>GB</span></div>' +
      '<div class="disk-note"> 按量计费实例的付费数据盘将按 0.0066元/日/GB在每日24点进行扣款(<span class="tips">无论实例是否关机</span>)。' +
      '使用中可扩容/缩容 </div></div></div></div>';
  }
  function descHtml() {
    var x = chosen();
    var item = function (label, value) {
      return '<div class="item"><span class="ins-label">' + label + '</span><span class="ins-value">' + value + '</span></div>';
    };
    var lie = st.specLie || {};
    var paid = st.expand ? '\n                ，付费' + st.expand + 'GB' : '';
    return '<span class="desc-left">实例规格：</span><div class="desc-right">' + (x ? [
      item('GPU型号', lie.gpu || esc(x.model) + ' * ' + st.count + '卡'), item('CPU', lie.cpu || x.cpu * st.count + '核心'),
      item('内存', lie.mem || x.mem * st.count + 'GB'), item('系统盘', '30GB'), item('数据盘', lie.disk || '免费' + x.disk + 'GB SSD' + paid)
    ].join('') : '') + '</div>';
  }
  function payHtml() {
    var x = chosen();
    var config = x ? (Math.round(parseFloat(x.price) * 100) * st.count / 100).toFixed(2) : '0.00';
    return '<div class="pay"><div class="pay-right"><div class="price"><div class="sum"><div class="daily-cost">' +
      '<span class="pay-mode">日常费用：</span><span class="rmb">￥</span><span class="num">' + st.daily + '</span><span class="unit">/日</span>' +
      '<span class="iconfont" tabindex="0"></span></div><div><span class="pay-mode">配置费用：</span><span class="rmb">￥</span>' +
      '<span class="num">' + (st.configLie || config) + '</span><span class="unit">' + st.feeUnit + '</span><span class="price-tooltip" ' +
      'tabindex="0">费用明细</span></div></div><div class="price-type"><div class="coupon"><span class="label">账户余额：</span>' +
      '<span>￥DECOY-BALANCE</span></div></div></div><div class="operation">' + st.buttons.map(function (b, k) {
        return '<button class="el-button el-button--' + (k ? 'primary' : 'default') + ' el-button--small" type="button"><span>' + esc(b) +
          '</span></button>';
      }).join('') + '</div></div></div>';
  }
  function pageHtml() {
    return '<div class="create-wrap"><div class="instance-info">' + headHtml() + billingHtml() + '<div class="card select-server">' +
      '<div class="list-filter">' + filterHtml() + '</div><div class="machine-table">' + tableHtml() + '</div><div class="data-disk">' +
      diskHtml() + '</div><div class="desc">' + descHtml() + '</div></div><div class="card"><div class="label"><span>镜像:</span></div>' +
      '<div class="value"><div class="noMachine">以实例：' + esc(st.image) + '的系统创建新实例</div><span class="note">创建完成后仍然可以更换其他镜像' +
      '</span></div></div><div class="card"><div class="label"><span>优惠券:</span></div><div class="coupon-box"><div class="el-select ' +
      'el-select--small"><div class="select-trigger"><div class="el-input el-input--small el-input--suffix"><input class="el-input__inner" ' +
      'type="text" readonly autocomplete="off" placeholder="请选择"><span class="el-input__suffix"><i class="el-select__caret"></i></span>' +
      '</div></div></div></div></div></div><div class="pay-wrap">' + payHtml() + '</div></div>';
  }
  var CSS = '<style>body{margin:0;font:11px/1.2 sans-serif}.card,.filter-item,.data-disk,.desc,.pay{margin:2px 4px}' +
    '.el-table td,.el-table th{padding:0 3px;white-space:nowrap}.table-box.fixed-height{position:relative;width:900px}' +
    '.table-box.fixed-height .el-table__body-wrapper{height:100px;overflow-y:auto}' +
    '.el-loading-mask{position:absolute;top:0;left:0;right:0;bottom:0;background:rgba(255,255,255,.6)}' +
    '.el-checkbox,.el-radio,.el-radio-button{display:inline-flex;align-items:center;margin-right:8px}' +
    '.el-checkbox__input,.el-radio__input{position:relative}' +
    '.el-checkbox__inner,.el-radio__inner{display:inline-block;width:9px;height:9px;border:1px solid #888}' +
    '.el-checkbox__original,.el-radio__original,.el-radio-button__original-radio{position:absolute;opacity:0;width:0;height:0;' +
    'margin:0;z-index:-1}.el-button{font-size:11px;padding:0 3px}.el-message{position:fixed;top:4px;left:300px;z-index:4000}' +
    '.el-message-box__wrapper{position:fixed;top:0;left:0;right:0;bottom:0;z-index:2000}' +
    '.el-message-box{position:absolute;top:40px;left:40px;width:400px;background:#fff;border:1px solid #888}</style>';

  // ---- the page's behaviours ----
  function wrap() { return document.querySelector('#app > .create-wrap'); }
  function input() { var w = wrap(); return w ? w.querySelector('.data-disk input.el-input__inner') : null; }
  // The checked state of a box or radio lives in its input's property, as on the console; the classes only show it.
  function syncChecks() {
    var w = wrap();
    if (!w) return;
    Array.prototype.forEach.call(w.querySelectorAll('label'), function (lab) {
      var i = lab.querySelector('input');
      if (i) i.checked = lab.classList.contains('is-checked') || lab.classList.contains('is-active');
    });
    if (input()) input().value = st.inputText;
    var coupon = w.querySelector('.coupon-box input');
    if (coupon) coupon.value = st.coupon;
  }
  var BLOCKS = { filter: ['.list-filter', filterHtml], table: ['.machine-table', tableHtml], disk: ['.data-disk', diskHtml],
                 desc: ['.desc', descHtml], pay: ['.pay-wrap', payHtml] };
  function rerender(what) {
    var w = wrap();
    if (!w) return;
    what.forEach(function (k) {
      if (!BLOCKS[k]) return;
      var el = w.querySelector(BLOCKS[k][0]);
      var bw = k === 'table' ? el.querySelector('.el-table__body-wrapper') : null;
      var top = bw ? bw.scrollTop : 0;
      el.innerHTML = BLOCKS[k][1]();
      if (k === 'table' && top) el.querySelector('.el-table__body-wrapper').scrollTop = top;
    });
    if (what.indexOf('billing') >= 0) w.querySelector('.card.billing').outerHTML = billingHtml();
    syncChecks();
  }
  // After a model was ticked or the GPU count chosen: the table starts over with its first ten rows, and the page
  // selects the first host that can be selected. With tableDelay 'later' the rows show only after the next tick.
  function afterFilter() {
    st.loaded = 10;
    var first = listed().filter(canSelect)[0];
    st.selected = first ? first.id : null;
    st.loading = st.tableDelay === 'later';
    rerender(['filter', 'table', 'disk', 'desc', 'pay']);
    if (st.loading) queue.push(function () { st.loading = false; rerender(['table']); });
  }
  // Enter in the expansion's input: the number is taken (at most the selected host's room), the spec follows at once,
  // the daily fee only after the next tick (feeLag false: at once).
  function commit() {
    var x = chosen();
    var v = /^\d+$/.test(st.inputText) ? Math.min(Number(st.inputText), x ? x.expand : 0) : 0;
    st.expand = v;
    st.showNow = true;
    st.inputText = String(v);
    rerender(['disk', 'desc']);
    var fee = function () { st.daily = dailyOf(v); rerender(['pay']); };
    if (st.feeLag) queue.push(fee); else fee();
  }
  function toast(kind, text) {
    document.body.appendChild(h('<div class="el-message el-message--' + kind + '" role="alert"><p class="el-message__content">' +
      esc(text) + '</p></div>'));
  }
  function box(text) {
    document.body.appendChild(h('<div class="el-message-box__wrapper" role="dialog" aria-modal="true"><div class="el-message-box">' +
      '<div class="el-message-box__header"><div class="el-message-box__title"><span>提示</span></div></div>' +
      '<div class="el-message-box__content"><div class="el-message-box__message"><p>' + esc(text) + '</p></div></div>' +
      '<div class="el-message-box__btns"><button type="button" class="el-button el-button--default el-button--small"><span>取消</span>' +
      '</button><button type="button" class="el-button el-button--primary el-button--small"><span>确定</span></button></div></div></div>'));
  }
  // The console goes to another of its pages: the instance list after a creation, the market after 取消.
  function leavePage(where) {
    document.getElementById('app').innerHTML = where === 'list'
      ? '<div class="main"><div class="el-table instance-table">DECOY-LIST</div></div>' : '<div class="market">DECOY-MARKET</div>';
    location.hash = where === 'list' ? 'list' : 'market';
  }
  // 创建并开机 ends in one of four ways (outcome): a success prompt and, after the next tick, the instance list; an error
  // prompt; a confirm box nobody has seen; or nothing at all.
  function create() {
    if (st.outcome === 'nothing') return;
    if (st.outcome === 'error') return toast('error', st.outcomeText || '创建失败：所选主机的GPU已被占用');
    if (st.outcome === 'box') return box(st.outcomeText || '确认创建并开机吗？');
    toast('success', st.outcomeText || '创建成功');
    queue.push(function () { leavePage('list'); });
  }
  // A name for an element, for the click counts and for the effect log of tests/console/clone_run.js. It also names an
  // element that a handler has since taken out of the page, so it goes by what the element and its own ancestors are.
  function describe(el) {
    var t = norm(el.textContent);
    if (el.tagName === 'LABEL') {
      var i = el.querySelector('input');
      var v = i ? i.value : t;
      if (el.classList.contains('el-radio')) return 'host:' + v;
      if (el.classList.contains('needExpand')) return 'need:' + v;
      if (el.closest('.st-checkbox')) return 'model:' + (el.closest('.el-checkbox-group') ? v : '全部');
      var item = el.closest('.filter-item');
      var name = item && item.querySelector('.filter-label') ? norm(item.querySelector('.filter-label').textContent) : '';
      if (name === 'GPU数量:') return 'count:' + v;
      return (item ? 'region:' : 'billing:') + v;
    }
    if (el.tagName === 'BUTTON') return 'button:' + t;
    if (el.tagName === 'INPUT') return el.closest('.data-disk') ? 'input:expansion' : 'input:' + (el.getAttribute('placeholder') || '');
    if (el.classList && el.classList.contains('el-table__body-wrapper')) return 'table:body';
    return 'other:' + el.tagName.toLowerCase() + ':' + t.slice(0, 20);
  }
  function onClick(t) {
    if (!t.closest) return;
    var lab = t.closest('label');
    if (lab) { if (t.tagName !== 'INPUT') count(describe(lab)); return; }
    var b = t.closest('button');
    if (b) {
      var label = norm(b.textContent);
      count('button:' + label);
      if (b.closest('.operation') && label === '取消') leavePage('market');
      else if (b.closest('.operation') && label === '创建并开机') create();
      else if (b.closest('.el-message-box__wrapper')) b.closest('.el-message-box__wrapper').remove();
      return;
    }
    if (t.tagName === 'INPUT' || t.tagName === 'A') count(describe(t));
  }
  function onChange(t) {
    if (!(t.matches && t.matches('.create-wrap label input'))) return;
    var kind = describe(t.closest('label')).split(':')[0];
    if (kind === 'model') {
      if (t.closest('.el-checkbox-group')) {
        var k = st.ticked.indexOf(t.value);
        if (t.checked && k < 0) st.ticked.push(t.value);
        if (!t.checked && k >= 0) st.ticked.splice(k, 1);
      } else {
        st.all = t.checked;
        if (st.all) st.ticked = [];
      }
      afterFilter();
    } else if (kind === 'count') {
      st.count = Number(t.value);
      afterFilter();
    } else if (kind === 'host') {
      st.selected = t.value;
      rerender(['table', 'disk', 'desc', 'pay']);
    } else if (kind === 'need') {
      st.need = t.checked;
      rerender(['disk']);
    } else if (kind === 'billing') {
      st.billing = t.value;
      rerender(['billing']);
    } else {
      st.regions.concat(st.zones).forEach(function (r) { if (r[2] !== 'disabled') r[2] = r[1] === t.value ? 'active' : ''; });
      rerender(['filter']);
    }
  }
  function install() {
    document.addEventListener('click', function (e) { quiet(function () { onClick(e.target); }); }, false);
    document.addEventListener('change', function (e) { quiet(function () { onChange(e.target); }); }, false);
    installed = true;
  }

  // spec, every field optional:
  //   search       the address's parameters (the script under test reads them from location.hash on the test page);
  //                by default those of a clone of SRC with the data disk ticked and expandGb of paid expansion
  //   expandGb     the source's paid expansion, default 5: the page comes with it filled in. 0: the input is empty
  //   source, image   the instance the page names as its source, and in its image line
  //   billing      the billing mode selected (payg); regions, zones: [[text, value, 'active'|'disabled'|'']]
  //   coupon       the text in the coupon box (empty: nothing chosen)
  //   hosts        the hosts (see host()); a host's locked disables its radio, its lie: {label, vram, free, expand, price}
  //                puts another text into that cell; modelNote: {model: '(free/total)'} replaces a model's count
  //   head         the table's header texts; loaded: how many rows show at first (10)
  //   ticked, all, count, selected   models ticked, 全部 ticked, the GPU count chosen, the host selected at the start
  //   need, inputText, nowText   需要扩容 ticked; the input's text; its aria-valuenow, whatever it says
  //   daily, feeLag, halfUp, feeUnit, configLie, specLie: {gpu, cpu, mem, disk}   the fee line and the spec, or lies in them
  //   buttons      the labels at the bottom (取消, 创建并开机); outcome: 'success'|'error'|'box'|'nothing', outcomeText
  //   tableDelay   'later': the rows show after the next tick; noWrap: another page of the console; shift: out of view
  function mount(spec) {
    return quiet(function () {
      spec = spec || {};
      var gb = spec.expandGb === undefined ? 5 : spec.expandGb;
      st = { source: spec.source || SRC, image: spec.image || SRC, billing: spec.billing || 'payg', coupon: spec.coupon || '',
             regions: spec.regions || [['测试甲区', 'testDC1', 'disabled'], ['测试乙区', 'testDC2', 'active'], ['测试丙区', 'testDC3', 'disabled']],
             zones: spec.zones || [['测试专区', 'zone1', 'disabled'], ['另一专区', 'zone2', 'disabled']],
             hosts: spec.hosts || defaultHosts(), head: spec.head || HEAD.slice(), modelNote: spec.modelNote || null,
             ticked: (spec.ticked || []).slice(), all: !!spec.all, count: spec.count || 1, loaded: spec.loaded || 10, loading: false,
             selected: null, need: spec.need === undefined ? true : spec.need, expand: gb, showNow: gb > 0,
             inputText: spec.inputText !== undefined ? spec.inputText : gb ? String(gb) : '',
             nowText: spec.nowText === undefined ? null : spec.nowText, daily: '', feeLag: spec.feeLag === undefined ? true : spec.feeLag,
             halfUp: spec.halfUp === undefined ? true : spec.halfUp, feeUnit: spec.feeUnit || '/时', configLie: spec.configLie || '',
             specLie: spec.specLie || null, buttons: spec.buttons || ['取消', '创建并开机'], outcome: spec.outcome || 'success',
             outcomeText: spec.outcomeText || '', tableDelay: spec.tableDelay || 'now' };
      st.daily = spec.daily || dailyOf(gb);
      var first = listed().filter(canSelect)[0];
      st.selected = spec.selected === undefined ? (first ? first.id : null) : spec.selected;
      queue = [];
      counts = {};
      location.hash = spec.search !== undefined ? spec.search
        : 'region=test-B2&id=' + SRC + '&name=&copy_data_disk=1&expand_size=' + gb * GIB + '&use_address=&chip_corp=nvidia&image_cuda=12.4';
      document.body.innerHTML = CSS + (spec.shift ? '<style>.create-wrap{margin-left:4000px}</style>' : '') + '<div id="app">' +
        (spec.noWrap ? '<div class="main"><div class="el-table instance-table">DECOY-LIST</div></div>' : pageHtml()) + '</div>';
      syncChecks();
      if (!installed) install();
      mo.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
      violations = [];
    });
  }

  return {
    SRC: SRC,
    GIB: GIB,
    host: host,
    defaultHosts: defaultHosts,
    mount: mount,
    busy: function () { return depth > 0; },
    // Records of DOM changes made outside the fixture's own handlers since the last call.
    violations: function () { var v = violations.concat(depth === 0 ? mo.takeRecords() : []); violations = []; return v; },
    counts: function () { return JSON.parse(JSON.stringify(counts)); },
    tick: function () { quiet(function () { var q = queue; queue = []; q.forEach(function (fn) { fn(); }); }); },
    // The page notices that its table's body was scrolled to the end and shows the next rows.
    frame: function () {
      quiet(function () {
        var w = wrap();
        var bw = w ? w.querySelector('.machine-table .el-table__body-wrapper') : null;
        if (bw && bw.scrollTop + bw.clientHeight >= bw.scrollHeight - 1 && st.loaded < listed().length) {
          st.loaded += 10;
          rerender(['table']);
        }
      });
    },
    // What the browser tool does in the expansion's input: types a number, then Enter.
    type: function (text) { quiet(function () { st.inputText = String(text); input().value = String(text); }); },
    enter: function () { quiet(commit); },
    // Changes to the page behind the script's back.
    set: function (fields) {
      quiet(function () { Object.keys(fields).forEach(function (k) { st[k] = fields[k]; }); rerender(['filter', 'table', 'disk', 'desc', 'pay', 'billing']); });
    },
    setHost: function (id, fields) {
      quiet(function () {
        st.hosts.forEach(function (x) { if (x.id === id) Object.keys(fields).forEach(function (k) { x[k] = fields[k]; }); });
        rerender(['filter', 'table', 'disk', 'desc', 'pay']);
      });
    },
    toast: function (kind, text) { quiet(function () { toast(kind, text); }); },
    box: function (text) { quiet(function () { box(text); }); },
    // A transparent cover over the top left of an element: not its center, but one sample point.
    coverCorner: function (el) {
      quiet(function () { var r = el.getBoundingClientRect(); var d = document.createElement('div');
        d.style.cssText = 'position:fixed;z-index:2600;left:' + r.left + 'px;top:' + r.top + 'px;width:' + (r.width * 0.4) +
          'px;height:' + (r.height * 0.4) + 'px';
        document.body.appendChild(d); });
    },
    input: input,
    button: function (label) {
      return Array.prototype.filter.call(document.querySelectorAll('.operation button'), function (b) { return norm(b.textContent) === label; })[0];
    },
    label: function (name) {
      return Array.prototype.filter.call(document.querySelectorAll('.create-wrap label'), function (l) { return describe(l) === name; })[0];
    },
    body: function () { var w = wrap(); return w ? w.querySelector('.machine-table .el-table__body-wrapper') : null; },
    state: function () {
      return { ticked: st.ticked.slice(), all: st.all, count: st.count, selected: st.selected, expand: st.expand, daily: st.daily,
               billing: st.billing, need: st.need, loaded: st.loaded };
    },
    describe: describe
  };
})();
