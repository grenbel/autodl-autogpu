# 没有空闲卡时的等卡与克隆

适用于 ctl v0.9、`reference/console.js` 第 9 版与 `reference/clone-page.js` 第 1 版。要开有卡而实例所在的主机没有空闲 GPU 时读这一份；`ctl auth show` 的 `clone` 里有没了结的克隆（`open_record` 不为 null）时也先读它，从"对话断了之后"读起。文中的"手册"是 `reference/console.md`，"原机器"是要克隆的那台实例，"新机器"是克隆出来的那台，"事务号"是开克隆记录时得到的 16 位十六进制数。`ctl` 的调用方式见 SKILL.md。

克隆是新租一台同原机器配置一样的实例（驱动可以更新，见"找合适的主机"第 8 步），把系统盘与数据盘拷过去，任务换到新机器上跑。它花钱，只在用户开启了自动克隆、等卡等满之后做。全程不需要用户在场，对用户只在三处说话，即决定克隆时一句，换过去时汇报一次，出了问题时。你不释放任何实例，原机器由用户自己释放。

实测情况。写这一份时（2026-10-03），整个过程在真实账号上完整做过一次，从找合适的主机、准备、创建、认出、接手、换过去到了结，新旧两台数据盘的内容逐个文件校验一致；没人接手的票到点自己关机也在新机器上试过。那一次数据盘很小（五百多个文件，不到 200 KB），点"创建并开机"约一分钟后新的一行就是运行中。还没有见过的有三样，数据盘大的时候拷贝当中是什么样子、要多久，平台报错时怎么回答，点创建之后弹出确认框；下文写着"没有见过"的地方，遇到了就照"出错与停下"办，不猜。

## 设置

- 这是"开始时要确定的信息"的第五样，同别的几样一样先找后问。有四项，即是否开启"没有空闲卡时自动克隆"（默认关，没有用户的话不开）；等多久再克隆（默认 30 分钟）；本任务最多克隆几次（默认 1）；克隆稳定后原机器怎么办（默认"核对完提醒我释放"，另一种是"不管它，等平台 15 天后自动释放"）
- 问开关时把三件事说明白。一，开启就是事先同意这笔花费，等满之后你不再问，直接在预算之内新租一台配置一样、单价不高于原机器的实例，并为这次克隆把原机器无卡开两次、每次几分钟（克隆前做准备，了结时撤票），用法里只写了有卡的也一样。二，原机器有付费扩容的数据盘时，新机器同样扩容，之后两台都按天扣扩容费（每台每天是扩容量乘 0.0066 元，关着也扣），直到用户释放其中一台。三，一次克隆了结之前（你会告诉用户什么时候了结），不要自己拿原机器克隆或保存镜像，原因在"准备"里说克隆票的那一段
- 记进本机，`ctl auth clone --instance <原机器ID> --enable --wait 30m --max 1 --after remind --quote '<用户原话>' --said '<当时向用户说明的话>'`，`--after` 取 `remind` 或 `leave`。关掉是 `ctl auth clone --instance <原机器ID> --disable --quote '<用户原话>'`。每次 `--enable` 都把"本任务"重新算起。设置存在授权之外，重新 grant 或 revoke 都不动它。项目 `## AutoDL` 段里记一行 `clone: 已开启（等 30 分钟，本任务最多 1 次，之后提醒释放原机器）`，这一行只是给人看的，授权只认当前对话与本机记录
- `ctl auth show --instance <实例ID>` 的 `clone` 一项里有 `enabled`、`wait_s`、`max`、`made`（本任务已克隆几次）、`after`、`members`（同一个预算组里的各台，克隆出来的带 `from`，核实释放了的带 `released_at`，有扩容日常费用的带 `daily_fen`）与 `open_record`（没了结的克隆：事务号、做到哪一步、属于哪个项目、原机器与新机器）。没有设置过的实例这一项是 null。带着 `note` 的，是这台实例的任务已经换到它的克隆上了，开它之前先问用户
- 没开启时照旧，没有空闲卡就等，等不到告诉用户，克隆由用户自己决定。开启了的，用户在开启时已经同意过这笔花费，到时候不再问，决定克隆时告诉用户一句就往下做

## 等卡

- 要开有卡而这一行没有"GPU充足"（`row` 的 `gpuFree` 为假）时进入等卡。每 3 分钟照手册第 2 节刷新实例列表读一次这一行，一出现"GPU充足"就照开机流程开原机器，预算检查照做
- 等的时候不需要卡的事照做。用法允许时可以先开无卡，但开有卡或克隆之前都要先关机并收尾
- 等满设置的时长（`wait_s`）还没有空闲卡时，开启了克隆、`made` 小于 `max`、没有没了结的克隆，就往下走；否则告诉用户，停止等卡，用户让继续才继续
- 往下走之前原机器要是已关机（开着无卡的先照"关机"关掉并收尾），控制台主页没有欠费、停机、维护的提醒，余额读到了

## 两份脚本

克隆在两个页面上做，各用一份脚本。调用的写法、返回值的四种形状、截小图出帧、pending 的重试，都同手册第 3 节。

实例列表页上克隆要用七个函数，平时贴的 `reference/console.min.js` 里没有它们。克隆时改贴 `reference/console-clone.min.js`（函数文本 45877 字节，SHA-256 `ccc4870339a959ec6bded9f646d746affa2f6140703da6744384a0b5b3ce76ea`），它是 `console.js` 的全部代码，平时的函数照样都在。页面里已经放过平时那一份的，先刷新页面再放这一份，否则得到的是一概拒绝的壳（another copy or version）；放过这一份之后，平时的模板拿到的也是它。完整模板与短模板的用法同手册第 2 节，存文本的键是另一个。

```js
(async function () {
var fn = /* 这里放 reference/console-clone.min.js 的全文 */;
var t = fn.toString();
var b = new TextEncoder().encode(t);
var d = new Uint8Array(await crypto.subtle.digest('SHA-256', b));
var h = Array.prototype.map.call(d, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
if (b.length !== 45877 || h !== 'ccc4870339a959ec6bded9f646d746affa2f6140703da6744384a0b5b3ce76ea') return { ok: false, bytes: b.length, sha256: h };
try { sessionStorage.setItem('__autodl_console_clone_text', t); } catch (e) {}
var api = fn();
return { ok: true, version: api.version, mode: api.mode, clone: api.clone, page: api.page() };
})()
```

```js
(async function () {
var t = null;
try { t = sessionStorage.getItem('__autodl_console_clone_text'); } catch (e) {}
if (!t) return { ok: false, stored: false };
var b = new TextEncoder().encode(t);
var d = new Uint8Array(await crypto.subtle.digest('SHA-256', b));
var h = Array.prototype.map.call(d, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
if (b.length !== 45877 || h !== 'ccc4870339a959ec6bded9f646d746affa2f6140703da6744384a0b5b3ce76ea') return { ok: false, stored: true, bytes: b.length, sha256: h };
var fn = (0, eval)('(' + t + ')');
var api = fn();
return { ok: true, version: api.version, mode: api.mode, clone: api.clone, page: api.page() };
})()
```

预期返回 `ok: true`、`version: 9`、`mode: 'live'`、`clone: true`。`clone` 不是 true，是页面里先有了平时那一份，刷新后再放。

创建页（点"继续"之后的那一页）用 `reference/clone-page.js`，贴的是它去掉注释的 `reference/clone-page.min.js`（函数文本 29506 字节，SHA-256 `d00ffd252ccf4245eaba90e86bf9de8d1321317c1575183f3e559b9b429bc3b2`）。它注册成 `window.__autodlClone`，每个函数的第一个参数都是原机器的实例 ID。页面是在同一个标签页里换到创建页的，实例列表页的脚本对象还在，但它在创建页上一概拒绝。

```js
(async function () {
var fn = /* 这里放 reference/clone-page.min.js 的全文 */;
var t = fn.toString();
var b = new TextEncoder().encode(t);
var d = new Uint8Array(await crypto.subtle.digest('SHA-256', b));
var h = Array.prototype.map.call(d, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
if (b.length !== 29506 || h !== 'd00ffd252ccf4245eaba90e86bf9de8d1321317c1575183f3e559b9b429bc3b2') return { ok: false, bytes: b.length, sha256: h };
try { sessionStorage.setItem('__autodl_clone_page_text', t); } catch (e) {}
var api = fn();
return { ok: true, version: api.version, mode: api.mode };
})()
```

```js
(async function () {
var t = null;
try { t = sessionStorage.getItem('__autodl_clone_page_text'); } catch (e) {}
if (!t) return { ok: false, stored: false };
var b = new TextEncoder().encode(t);
var d = new Uint8Array(await crypto.subtle.digest('SHA-256', b));
var h = Array.prototype.map.call(d, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
if (b.length !== 29506 || h !== 'd00ffd252ccf4245eaba90e86bf9de8d1321317c1575183f3e559b9b429bc3b2') return { ok: false, stored: true, bytes: b.length, sha256: h };
var fn = (0, eval)('(' + t + ')');
var api = fn();
return { ok: true, version: api.version, mode: api.mode };
})()
```

预期返回 `ok: true`、`version: 1`、`mode: 'live'`。两组模板答 `ok: false` 或 `stored: false` 时的处理同手册第 2 节。

- 创建页比实例列表长。进创建页后先用 `resize_window` 把视口设成宽 1600、高 2400；脚本不点视口外的东西，拒绝原因里有 `is outside the viewport` 时再加高（2026-10-03 在真实页面上试过，这个高度够用）。克隆的事做完后恢复
- 创建页上从勾型号到最终确认只有两分钟，脚本自己卡着。所以不依赖创建页读数的事都排在进创建页之前
- 页面内部的数据只经 `sshAddress` 读（"认出新实例"第 3 步）。它拒绝或读不到时请用户贴登录指令，不另写脚本去看页面的数据

## 找合适的主机

先只读地进一次创建页，看此刻有没有同原机器配置一样、又有空闲卡的主机。这一遍什么都不创建。

1. 照手册第 2 节刷新实例列表，用上一节的模板放好脚本。`row('<原机器ID>')` 的 `state` 是已关机、`gpuFree` 为假（为真就照开机流程开机，不克隆），记下 `spec` 与 `gpus`。型号是 `spec` 里" * N卡"前面的那一段，"RTX 3080 Ti * 1卡"的型号是 RTX 3080 Ti
2. `menu('<原机器ID>')` 到 `open: true`（做法同手册第 7 节第 1 步），`items` 里有一项的整段文字是"克隆实例新"（"新"是角标）。`startClone('<原机器ID>')`，记下 `ctx`
3. 截小图，`bindCloneDialog(ctx)`（pending 就截小图再试）。脚本要求对话框里看得见的每一段文字都是它写死的原文，多一句少一句都拒绝。原文是：标题"克隆实例"；警示"克隆后源实例不受影响，不会释放也不会清理数据"；"需要克隆的数据："与两个复选框"系统盘"（已勾、不可改）、"数据盘"；"优化稀疏文件拷贝："（开关是关的）与说明"开启则会在拷贝时对稀疏文件进行优化，一般可节省目标实例磁盘空间"；页脚"今天剩余克隆次数：10次"这样的一句；按钮"取消"与"继续"。返回 `dataDisk`（数据盘勾了没有）、`expandGb`、`remaining`（今天剩余的克隆次数）、`spec` 与 `gpus`。`remaining` 为 0 就 `dismiss(ctx)`，见"出错与停下"
4. `tickCloneDataDisk(ctx)`，再 `bindCloneDialog(ctx)` 重读，`dataDisk` 应为真。原机器有付费扩容时对话框这时多出一句"源实例有扩容数据盘：5GB 请扩容目标实例数据盘，以防拷贝失败"（数字随原机器），`expandGb` 就是这个数，没有这一句是 0。稀疏文件的开关不动
5. `continueClone(ctx)`，页面换到创建页，这次操作结束。要放弃就在这之前 `dismiss(ctx)`
6. 设视口，放创建页的脚本（上一节）。`window.__autodlClone.page('<原机器ID>')`，下面创建页的函数都这样调用。每个函数先过同一道门，即地址是这台实例的克隆创建页并带着 `copy_data_disk=1`，页面上"源实例"与"镜像"两处写的是这台实例，计费方式选中的是"按量计费"，地区只有一个可选并已选中，"优惠券"是"请选择"，没有别的框。返回的 `expandGb` 要等于第 4 步读到的数，`models` 里有原机器的型号，`count` 是选中的卡数
7. `tickModel('<原机器ID>', '<型号>')`；卡数不同时 `pickCount('<原机器ID>', <gpus>)`。截小图，`hosts('<原机器ID>', <gpus>, <expandGb>)`（说主机表在加载就截小图再试）。`complete` 为假是还有行没加载，`loadMoreHosts('<原机器ID>')`、截小图、再 `hosts`，至多 10 次。10 次之后仍为假的，`reference` 不为 null 就照已经加载的行往下判断，为 null 就停下告诉用户
8. 看 `hosts` 的回答。`reference` 是原机器所在的主机（主机 ID 是实例 ID 的前半段），判断都拿它那一行作参照；`complete` 为真而 `reference` 为 null 就停下告诉用户，不猜。`referenceFree` 为真是原机器的主机这时有卡了，`leave('<原机器ID>')`，回去开原机器。`suitable` 是此刻合适的主机，驱动同参照行一样的排在前，其次是驱动更新的，各自之内空闲卡多的、可扩容量大的在前；别的在 `unsuitable` 里各有第一条不合适的原因。合适由脚本判断：主机表是"主机ID、算力型号/显存、空闲GPU、每GPU分配、CPU型号、硬盘、驱动/CUDA、价格(单卡)"这几列；型号与显存、每卡的 CPU 与内存、CPU 型号同参照行相同；驱动与 CUDA 上限不低于参照行的（更新的驱动跑得了旧驱动上能跑的程序，更旧的不行；版本按点分的数字逐段比，读不成数字的不算合适）；空闲卡不少于 `gpus`；单价不高于参照行；可扩容量不小于 `expandGb`；不是原机器所在的那一台
9. `suitable` 为空就不克隆。`leave`，回实例列表，把缺的是哪一项告诉用户（没有空闲卡、驱动更旧、可扩容量不够等），照"出错与停下"第一行接着等
10. 有合适的，再只读地看一遍要创建的东西，`pickHost('<原机器ID>', '<suitable 的第一台>')`，`prepareCreate('<原机器ID>', {host: '<主机ID>', model: '<型号>', gpus: <卡数>, expandGb: <扩容量>})`（它核对什么见"创建"第 5 步），记下 `copy` 里的 `price`（单价）与 `daily`（扩容的日常费用）。记下 `suitable` 的全部主机 ID。`leave('<原机器ID>')`（创建页的"取消"去的是算力市场页），再用 `navigate` 回实例列表

## 准备

有合适的主机才做准备。先告诉用户一句要克隆了（等了多久，型号与卡数、单价、扩容的日常费用），不等回答，开启时已经同意过。然后先开记录，再动原机器。

1. `ctl clone-record open --instance <原机器ID> --project <项目根目录> --hosts <主机ID,主机ID>`，得到 `txn`（事务号）。之后改记录、写票、撤票的每条命令都带它，对不上就拒绝。克隆没开启、本任务的次数用完、这台原机器已有一次没了结的克隆时它拒绝（退出 1，`reason` 是原因），什么都不写。项目 `## AutoDL` 段加一行 `clone_pending: <事务号>`。记录以本机的为准，项目里的 `.autodl/clone_pending.json` 只是副本，`ctl clone-record show --project <项目根目录>` 会照本机的重写它
2. 把原机器无卡开机，照 SKILL.md 的开机流程，预算检查、记账、deploy、arm 都照常。用法里没有无卡的，`ctl auth check` 加 `--clone-prep`，开启了克隆又有没了结的记录时它放行
3. `ctl status <原别名>` 里 `autostart_this_boot` 不为空，说明容器启动时带起 `/etc/profile.d/` 下钩子的那条路此刻是通的，克隆票走的是同一条路。为空而自启钩子是这次 deploy 才装上的，关机再无卡开一次再看；仍为空就不克隆
4. 留清单。`ctl manifest <原别名> --project <项目根目录>`，记下 `manifest`（清单文件的路径）、`copy_estimate_s`（估计的拷贝秒数）、`ticket_deadline`、`system_bytes` 与 `data_bytes`。有登记的任务在跑时它退出 3，等任务结束再算。算不出来（退出 1）就加长 `--timeout` 再算一次，仍没有就不克隆，因为估不出拷贝时间，接手时也少一道核对
5. 放票。`ctl clone-record update --txn <事务号> --stage ticket`，`ctl ticket write <原别名> --txn <事务号> --hosts <主机ID,主机ID> --deadline <ticket_deadline>`，再 `ctl ticket read <原别名> --txn <事务号>`，`present`、`mark_matches`、`is_source` 为真，`allowed` 为假。写不上或读回来不对就不克隆
6. 照"关机"关掉原机器并收尾

克隆票是系统盘上 `/etc/profile.d/` 下的一个小文件，写着事务号、原机器的主机名、准许的主机、最晚无人接手的时刻、每次开机至少留出的 15 分钟。系统盘是克隆的模板，新机器带着它启动；容器一启动，票就起一个后台循环，过了最晚时刻、这次开机也满了 15 分钟而票还没被撤掉，循环就执行关机命令。所以新机器没人接手（对话断了）也会自己关机，不靠守护，也不靠用户在场。它只在准许的主机上起作用，留在原机器上的那一张什么都不做。但了结之前用户自己把原机器克隆到那几台主机之一的话，那一台也会被它关掉，这就是"设置"里要向用户说明的第三件事。最晚时刻是放票那一刻加 30 分钟，再加估计拷贝时间的两倍。

票写好超过 15 分钟还没点创建的（等过用户，或重试过），重新做准备，把原机器再无卡开一次，带着同一个事务号重写票（记录已经改到 reserve 的，先 `ctl auth release` 放掉克隆的预留，再 `ctl clone-record update --txn <事务号> --stage ticket` 回到这一步，这是唯一能往回走的一步）。合适的主机换了一批时，在这一步用 `--set hosts=<主机ID,主机ID>` 改记录里准许的主机，票也照新的写。

从开了记录起，不论为什么不往下克隆了（原机器有卡了、合适的主机没了、预算或余额不够、页面对不上、用户叫停），都先撤掉原机器上的票，再了结记录。原机器开着就当场 `ctl ticket clear <原别名> --txn <事务号> --source`；它关着而接下来正要开它，开机后先撤；否则把它无卡开一次来撤。之后 `ctl clone-record close --txn <事务号>`，去掉 `clone_pending` 一行。票还没写过的（记录在 opened）直接 close。

## 创建

创建就是新租一台机器，过的是开机的同一套关口，最后点的是"创建并开机"。

1. 先做不靠创建页读数的事。原机器有付费扩容时 `ctl auth daily --instance <原机器ID> --fee <daily>`（"找合适的主机"第 10 步记下的日常费用），把原机器这笔钱登记进账本；它从本预算周期开头算起，同样的数已经登记过就什么都不写。读余额（手册第 4 节）。票写好已超过 15 分钟的，照上一节重新做准备。`ctl clone-record update --txn <事务号> --stage reserve`
2. 回实例列表，刷新并放好克隆的那份脚本，再读原机器这一行。有"GPU充足"了就开原机器，不克隆，票与记录照上一节末尾了结。`idDigests()`，记下返回的 `digests`，它是各行实例 ID 的摘要，不含 ID 本身，认新实例时用
3. 同"找合适的主机"第 2 到 8 步进创建页、读主机表，`tickModel` 那一下起算两分钟。在记录准许的主机（开记录时的 `--hosts`）里取 `suitable` 中排在最前的一台。准许的主机此刻一台都不合适，就 `leave`，照"出错与停下"第一行接着等
4. `pickHost('<原机器ID>', '<主机ID>')`，返回 `already` 是页面自己已经选中了它。`expansion('<原机器ID>')` 的 `gb` 与 `now` 要等于原机器的扩容量。页面没填或填的不对时 `focusExpansion('<原机器ID>')`，用浏览器工具全选、打数字、回车，再 `expansion` 读回，读回来不对就不克隆。没有扩容时输入框是空的，不用动。"需要扩容"这个勾脚本不点
5. `prepareCreate('<原机器ID>', {host: '<主机ID>', model: '<型号>', gpus: <卡数>, expandGb: <扩容量>})`。它核对：只勾着这一个型号，卡数相同；选中的是这台主机，此刻仍然合适；输入框里的扩容量相同；"实例规格"的型号与卡数、CPU、内存、付费的数据盘同这一行对得上；日常费用是扩容量乘 0.0066 元取到分；配置费用是单价乘卡数、单位是每小时；底部只有"取消"与"创建并开机"。返回 `copy`（准备好的副本，原样记下）与 `age`（离 `tickModel` 过去的秒数）。说日常费用不对的，多半是刚改过扩容量、费用还没跟上，隔十来秒截小图再试。`age` 超过 80 就不往下走，`leave`，从第 2 步重来
6. 比余额，不够付计划时长的，`leave`，不克隆，告诉用户。预算检查 `ctl auth check --instance <原机器ID> --mode gpu --price <copy.price> --gpus <卡数> --hours <计划时长加两倍估计的拷贝时间> --clone-host <主机ID>`（接手时等拷贝最多等到估计的两倍），有扩容时加 `--daily <copy.daily>`。新实例这时还没有 ID，预留先记在原机器名下；带 `--daily` 的预留从这一刻起每天把新机器的扩容费计入预算。退出 0 记下 `req`；别的退出码都 `leave`、不克隆，照 SKILL.md 的"用量与预算"处理
7. `ctl now` 记下 T0。把这次要创建的东西写进记录并改到"要点创建"，一条命令做完，`ctl clone-record update --txn <事务号> --stage click --set host=<主机ID> --set gpus=<卡数> --set price=<copy.price> --set expand-gb=<扩容量> --set daily=<copy.daily> --set req=<req> --set t0=<T0> --set before=<digests 用逗号连起来>`，没有扩容时不写 `daily`
8. 立刻 `confirmCreate('<原机器ID>', copy)`。这是最终确认，脚本点"创建并开机"，一个页面只点一次。返回 refused 时什么都没点（离 `tickModel` 超过了两分钟，或这几秒里主机变了），这时 `ctl clone-record update --txn <事务号> --stage clicked --set answer= --set created=no --set note='<拒绝的原因>'`，`ctl auth release --instance <原机器ID> --req <req>`，`leave`；这一次算查明没建成，照"对话断了之后"的 click 一行了结，要再试就另开一次记录。调用没有返回时按已尝试处理，不再点
9. 截小图，`result('<原机器ID>')`。它只读，在控制台的任何页面上都能调用，`confirmed` 是这个页面上点过创建，`prompts` 是看得见的提示，`dialogs` 是看得见的框，`create` 说创建页还在不在，`ids` 是这些文字与地址里出现的别的实例 ID。把读到的记进记录，`ctl clone-record update --txn <事务号> --stage clicked --set answer='<提示的原文，没有就空着>'`，平台给了新实例 ID 的加 `--set instance=<新ID>`。实测平台不说成功，点完两秒页面已经自己跳回实例列表，没有提示，没有框，也不给新实例的 ID。所以 `confirmed` 为真、`create` 为假、`dialogs` 为空、`prompts` 里没有报错的话，就往下认，建没建成由下一节认出来。`prompts` 里有报错的话，或点了之后弹出确认框（不点它，它的原文没有见过），照"出错与停下"办

## 认出新实例

1. 回实例列表（页面自己跳过去的也刷新一次），放好克隆的那份脚本。`findCreated(digests, '<主机ID>', '<原机器的 spec>')`，平台给过新实例 ID 的把它作第四个参数。候选是这样的行，ID 的摘要不在 `digests` 里，ID 以主机 ID 加连字符开头，规格的文字同原机器的一样。`count` 为 1 就记下它的 ID，平台没给过 ID 的这时 `ctl clone-record update --txn <事务号> --set instance=<候选ID>`。为 0 就隔一分钟再读；多于 1 停下。它这时还只是候选，证实之前不对它做任何改动，不记账，不设定时，不部署
2. 每分钟刷新读一次 `row('<候选ID>')`，等它变成运行中（实测点完约一分钟就是，数据盘很小）。创建当中的状态原文没有见过，不认识的状态只等不点。它一运行中就接着做下面几步与"接手"第 1 步，中间不插别的事，原因见那一步
3. `sshAddress('<候选ID>')` 给出 `host` 与 `port`。在 `~/.ssh/config` 里照 `reference/ssh.md` 的"别名的写法与第一次连接"给它写一个新别名（原别名加后缀，例如 `<原别名>-c1`；密钥用原别名的那一把，SSH 公钥是账号级的；原别名那一条不动）。`sshAddress` 拒绝时请用户把这台新实例的登录指令贴过来
4. `ctl wait <新别名> --mode gpu`，再 `ctl check <新别名> --instance <候选ID>`。主机名对不上，照 SKILL.md 立即停下
5. `ctl ticket read <新别名> --txn <事务号>`。四样都对才认定它是这一次克隆出来的，即点创建之后没有报错（"创建"第 9 步），候选恰好一个，`mark_matches` 为真（票上的标记就是事务号），`allowed` 为真（它所在的主机在票准许之列）。对不上就不碰它，不记账，告诉用户。`loop` 为假是票的循环没在跑，这台机器眼下没有兜底，这时 `ctl ticket start <新别名> --txn <事务号>`；起不来就给这一行设一个 30 分钟后的定时关机，先把打算设的时刻记进记录（`ctl clone-record update --txn <事务号> --set emergency-timer='<那个时刻>'`，记在动手之前，中途断了下一个对话也知道这一行上可能有定时），再照手册第 8 节设上，读回来的时刻不同就改记录；两样都不成就把它关机，告诉用户
6. 认定之后先 `ctl ticket extend <新别名> --txn <事务号> --deadline 30m`，免得接手当中到点（设过应急定时的，把它也改到同一时刻，记录里的跟着改）。然后立刻记账，`ctl auth inherit --from <原机器ID> --to <新ID> --req <req> --alias <新别名>`（有扩容时加 `--daily <copy.daily>`），`ctl log on --instance <新ID> --req <req> --at <T0> --field mode=gpu --field price=<copy.price> --field gpus=<卡数>`，`ctl clone-record update --txn <事务号> --stage adopted`。inherit 给新实例建一份同原机器一样的授权，把预留挪到它名下，并把两台记进同一个预算组，预算只有一份，两台的花费加起来算

## 接手

次序同平时开机不同。先把守护配上，再核对数据盘与规格，都过了才撤票；撤票之前票一直是兜底。

1. 配守护，认定之后立刻做。`ctl deploy <新别名>`，`ctl arm <新别名> ...`（设置同原机器），`ctl status <新别名>` 里 `armed_this_boot=1`、`armed_by=arm`、`needs_rearm=0` 才算配好。新机器带着原机器的自启钩子与按模式存下的设置，容器一启动守护多半已经自己起来（`armed_by=boot`），用的是原机器上一次在这个模式下的空闲时限，机器闲着就按它关机（实测存下的是 2 分钟，新机器开机不到 3 分钟被它关了）。你的 arm 替换它，空闲从这时重新算
2. 数据盘拷完了没有，以清单为准，平台不显示拷贝的进度与完成（实测一次，数据盘很小，清单第一次比就一样；数据盘大的时候没有见过）。`ctl manifest <新别名> --compare <原机器的清单文件> --project <项目根目录>`，`same` 为真才往下，不一样时 `differences` 列出前 20 条，隔 3 分钟再比，到估计拷贝时间（`copy_estimate_s`）的两倍还不一样就照"出错与停下"办。等的时间要超过票的最晚时刻（`ctl ticket read` 的 `deadline_in_s`）时先 `ctl ticket extend`。清单只说明数据盘上有哪些文件、各多大；系统盘不在里面，内容也没有逐个校验
3. 核对规格。`ctl spec <新别名> --gpu-model '<型号>' --gpus <卡数> --driver <copy.driver> --cpu-per-gpu <copy.cpu> --mem-per-gpu-gb <copy.memGb> --min-system-bytes <清单里的 system_bytes> --min-data-bytes <清单里的 data_bytes>`，`match` 为真才往下，不符的在 `diff` 里
4. 撤票。`ctl ticket clear <新别名> --txn <事务号>` 在票的那把锁里确认守护是这次配的并且活着，删掉票，再等循环留下回执，退出 0 才算交接完成。退出 6 是没等到回执，再 `ctl ticket read`，票还在就重撤。退出 4 是循环已经发出了关机，按新机器被关了处理，照"关机"收尾，重新开机后从这一节接着做。退出 3 是守护没配好，票没动
5. 读新机器这一行的定时关机（`row('<新ID>')` 的 `timer`）。记录里有应急定时、或这一行上有定时的，这时处理掉，用户没选控制台定时关机就取消它（手册第 5 节），再 `ctl clone-record update --txn <事务号> --set emergency-timer=none`。用户选了控制台定时关机的，这时照手册第 8 节设上，时刻是 T0 加用户选的时长。然后 `ctl clone-record update --txn <事务号> --stage taken-over`
6. 起任务，预算先 probe。给这次启动取一个请求号（16 位十六进制，`python -c "import secrets; print(secrets.token_hex(8))"`），先记进记录再启动，`ctl clone-record update --txn <事务号> --stage launching --set job=<任务名> --set job-req=<请求号>`，`ctl run <新别名> <任务名> --req <请求号> --cmd '<命令>'`，成功后 `ctl clone-record update --txn <事务号> --stage launched`。带 `--req` 的 run 先问这个请求号起过任务没有，起过就不再起，所以中断之后重发是安全的；单独问用 `ctl job <新别名> <任务名> --req <请求号>`。从原机器拷过来的同名任务记录请求号不同，不会被当成这一次的

## 换过去与了结

- 任务启动后观察 10 分钟。没问题是指这几样同时成立，任务还在跑或已经正常结束；`ctl status` 里 `daemon_alive=1`、`armed_this_boot=1`、`armed_by=arm`、`needs_rearm=0`；GPU 的信号读得到，任务用 GPU 的还要见过它在用；上一节各步都过了
- 满足就换过去。项目 `## AutoDL` 段的 instance_id 与 ssh_alias 改成新机器的，加一行 `previous_instance: <原机器ID>（它的 place；哪天克隆到 <新ID>；数据核对到什么程度；打算怎么处理）`，`ctl clone-record update --txn <事务号> --stage switched`，向用户汇报一次（内容见下一节）。之后开关机与跑任务都用新机器
- 任务自己的错误（代码里的异常、参数写错）照平时处理，改好重跑，观察从重跑算起。机器方面的问题（GPU 用不了、守护起不来、SSH 一直不通）才算克隆没成，这时不换过去，把新机器关机并收尾，告诉用户，两台都留着；了结时 `ctl clone-record close --txn <事务号> --note '<两台各怎么样>'`
- 了结。换过去之后（没建成或用户叫停之后也一样），把原机器无卡开一次（开机流程照常，预算检查加 `--clone-prep`），`ctl ticket clear <原别名> --txn <事务号> --source`，关机并收尾，`ctl clone-record close --txn <事务号>`，去掉 `## AutoDL` 的 `clone_pending` 一行，告诉用户这一次克隆了结了、可以照常克隆这台机器了。原机器这时开不了无卡的，`ctl clone-record update --txn <事务号> --set note='原机器上的票还没撤'`，告诉用户这期间不要手动克隆它，下次能开时补上，在那之前不再克隆
- 内容校验放在了结的那一次无卡开机里。`ctl manifest <原别名> --content --project <项目根目录>` 给每个文件取 SHA-256（估计的时间取清单的 `copy_estimate_s`，超过 30 分钟的先问用户做不做）。新机器上没有登记的任务在跑时 `ctl manifest <新别名> --content --compare <原机器带内容的清单文件> --changed-after <任务启动的时刻> --project <项目根目录>`，时刻取 `ctl status <新别名>` 的 `jobs` 里这个任务的 `start`（重跑过的取第一次启动时读到的）。带上它，新机器比原机器多出来的（任务新建的，守护把同名任务的旧记录挪成的 `.prev-` 目录）与那个时刻之后改过的都不比，单独计数（`not_compared`）；其中原机器也有的另计在 `changed_since`，它们被任务改过，原来那一份拷得对不对没法再校验。原机器有而新机器没有的照旧算不同，哪怕是任务自己删掉或改了名的，这时看 `differences` 里的路径判断，照实告诉用户。`same` 为真并且 `changed_since` 为 0 才算内容逐个校验一致

## 原机器

- 你不点"释放实例"，两份脚本里没有任何函数点得了它
- 核对到什么程度，话就说到什么程度。只比过清单的，汇报写"数据盘上文件的路径与大小都一致，共多少个、多少字节，内容没有逐个校验，系统盘没有比"，不说可以释放。内容校验一致的，才写"数据盘内容逐个文件校验一致，可以释放"。`changed_since` 不为 0 的，写明有几个文件在任务启动后被改过、没法校验（路径在 `changed_since_first`），不说可以释放，这几个文件原来的那一份还要不要由用户定
- 汇报里两台各占一行，让用户在控制台上认得出，各写 `row` 读到的 `place`（第一格里的地区与主机，例如"北京B区 / 123机"）与实例 ID，并写明哪一台是现在用的、哪一台是原来的。两台的型号与规格一样，只说"原机器""新机器"或只给 ID，用户分不清要释放的是哪一行
- `after` 是 `remind` 时，换过去之后的汇报里写清这几样，两台各是哪一行（照上一条）；上面说的核对结果；新机器的驱动同原机器的不一样时两个版本各是什么；原机器有没有付费扩容、每天扣多少（关着也扣）；平台哪天会自动释放它（最后一次关机之后 15 天）；释放的入口（实例列表这一行"更多"里的"释放实例"）
- `after` 是 `leave` 时不提醒释放，只说原机器还在、平台哪天自动释放；有付费扩容时照样写明每天在扣多少，不释放就一直扣、一直计入预算。内容没有校验过或校验不一致时没有这一种说法，那时原机器是唯一确定完整的一份，汇报里写明，并说明要留着它就得在到期前开一次机
- 用户说已经释放了，刷新实例列表核对。原机器那一行不在了、新机器那一行原样，才 `ctl auth released --instance <原机器ID> --at <ctl now 的输出>`，把 `previous_instance` 一行改成已释放。它的日常费用从这时起不再计入，授权撤掉，账本与预算组留着
- 换过去之后原机器不再由你开机，只有了结时的那一次例外。`previous_instance` 没标成已释放之前，每次用控制台都顺带读它这一行的释放倒计时，剩不到 3 天就提醒用户。别的项目若也在用原机器，它们的对话读 `ctl auth show --instance <原机器ID>` 时会看到 `clone` 里的 `note`，先问用户再开

## 对话断了之后

开机流程第 1 步读 `ctl auth show`，`clone` 的 `open_record` 不为 null 就先了结这一次，不等卡，也不另起一次。`ctl clone-record show --txn <事务号> --project <项目根目录>` 给出记录的全部字段。记录里的 `stage` 是动手之前写的，读到它的意思是这一步可能已经做了，它前面的都做了，后面的都没做。

| `stage` | 怎么接 |
|---|---|
| opened | 什么都没动，直接 `ctl clone-record close --txn <事务号>` |
| ticket | 原机器上可能有票。撤票（"准备"末尾的做法），再 close |
| reserve | 另可能有克隆的预留。`ctl auth show` 的 `open_reservations` 里带 `clone_host` 的那一条用 `ctl auth release` 放掉，撤票，close |
| click，记录里没有 `answer` | 点没点成不知道。刷新实例列表，用记录里的 `before` 与 `host` 调 `findCreated`。有候选的，记录改到 clicked（`--set answer=`），从"认出新实例"第 1 步做起，是不是这一次建的由票的标记证实。没有候选的，隔一分钟再读，读 10 次还没有，再看收支明细里 T0 之后有没有新实例的扣费。都没有也不就此了结（数据盘大时新的一行多久出现没有见过），告诉用户，预留、记录与原机器上的票都留着。用户看过控制台、说没建成之后，才 `ctl clone-record update --txn <事务号> --stage clicked --set answer= --set created=no --set note='<用户的话>'`，release，撤票，close |
| clicked，`answer` 里没有报错（空着是平常的） | 从"认出新实例"第 1 步做起，T0、请求号、事务号、摘要都在记录里 |
| clicked，`answer` 是报错的话 | 同 click 一行 |
| adopted | 从"接手"第 1 步接着做，撤票与弄定时重做一次无害 |
| taken-over | 从"接手"第 6 步接着做 |
| launching | 用记录里的 `job` 与 `job_req` 重发 `ctl run ... --req`，它先问起过没有，起过就不再起；再改到 launched |
| launched | 从"换过去与了结"接着做 |
| switched | 只差撤原机器上的票与 close |

没人接手的新机器到票的最晚时刻自己关机（实测过）。之后要接手得先开机，每次开机票给 15 分钟，够连上去 `ctl ticket extend` 或撤票。已认定的（记录在 adopted 及以后）照开机流程开它，连上之后先 extend 与"接手"第 1 步；还没认定的见"出错与停下"。

## 出错与停下

| 情形 | 怎么办 |
|---|---|
| 没有合适的空闲主机 | 不克隆，`leave`，什么都没创建。告诉用户缺的是哪一项。接着等，原机器这一行改成每 10 分钟读一次，一有"GPU充足"就开；每 30 分钟再进一次创建页看。再等 2 小时还没有就停下告诉用户，用户让继续才继续 |
| `remaining` 为 0 | `dismiss(ctx)`，今天克隆不了，告诉用户，照上一行接着等原机器的卡 |
| 本任务的克隆次数用完了 | 不克隆，告诉用户，停止等卡，用户让继续才继续 |
| 原机器无卡开不了，票没写上或读回来不对，或 `autostart_this_boot` 一直为空 | 不克隆。没有验证过的票就没有兜底，告诉用户，接着等原机器的卡 |
| 对话框或创建页的原文、结构与这一份对不上，或脚本拒绝 | 停下，什么都不点，`dismiss` 或 `leave`，告诉用户，控制台可能改版了 |
| 余额或预算不够 | 不克隆，`leave`，告诉用户 |
| 点了创建之后弹出确认框 | 不点。把 `result` 读到的原文告诉用户，按下一行查明 |
| 点了之后平台报错，或 `confirmCreate` 的调用没有返回 | 不再点。照"对话断了之后"的 click 一行查明，查明之前预留与记录保留，告诉用户。没人接手的新机器到票的最晚时刻自己关机 |
| 点成了而一直没有候选 | 隔一分钟再读，读 10 次还没有就告诉用户，预留与记录保留 |
| 候选不止一个 | 停下告诉用户，哪一行都不动，预留与记录保留，等用户指明 |
| 候选一直不变成运行中 | 只等不点，超过估计拷贝时间的两倍就告诉用户 |
| 候选在认定之前已关机（对话断了很久、票到点了，或它带过来的守护先关了它） | 不开它，它还没有授权记录，过不了预算检查。告诉用户这一行多半是这次克隆出来的、要证实得先开机。用户在控制台把它开机之后，从"认出新实例"第 3 步接着做；认定并 inherit 之后先把创建的那一次开机记上并 `log off`（时刻取收支明细里那次的最后一笔），眼下这一次按接手一台开着的实例记账（手册第 17 节） |
| 新机器在守护配好之前自己关机了（已认定） | 照 SKILL.md 的"关机"收尾这一次，再照开机流程把它开有卡（预算检查用新机器的 ID），连上之后先 `ctl ticket extend` 与"接手"第 1 步，再接着做 |
| 拿不到候选的 SSH 地址，或 SSH 不通 | 照 `reference/ssh.md` 的"别名的写法与第一次连接"查；地址拿不到就请用户贴登录指令。这期间它在计费，告诉用户。原机器不动 |
| 候选上没有票，或票的标记不是这一次的 | 它不是这次克隆出来的，或系统盘没有照样拷过来。不碰它，不记账，告诉用户；预留与记录保留 |
| 清单比到估计拷贝时间的两倍仍对不上 | 不跑任务，不换过去。新机器关机并收尾，把不同的路径告诉用户，两台都留着。要多等时先 `ctl ticket extend`（设过应急定时的一并改） |
| 规格核对不过 | 不跑任务。把差在哪告诉用户，新机器关机并收尾，留不留由用户定 |
| 新机器上守护配不好 | 票不撤。照 SKILL.md 的出错处理重发；仍不成就关机并收尾，告诉用户 |
| 观察期里机器方面出了问题 | 不换过去，新机器关机并收尾，告诉用户，两台都留着 |

## 函数与返回

实例列表页上克隆用的七个函数（只在 `console-clone.min.js` 里），调用写成 `window.__autodl.startClone('<原机器ID>')` 这样。

| 函数 | 做什么，返回什么 |
|---|---|
| `startClone(id)` | 这一行已关机、没有"GPU充足"、规格读得出、"更多"菜单开着，才点菜单里整段文字是"克隆实例新"的那一项。返回 `ctx` |
| `bindCloneDialog(ctx)` | 绑定克隆的对话框，绑定之后再调用是重读。返回 `dataDisk`、`expandGb`、`remaining`、`spec`、`gpus`。是克隆的对话框而原文对不上时拒绝，这时仍可以 `dismiss(ctx)` |
| `tickCloneDataDisk(ctx)` | 点"数据盘"复选框的标签，只在它没勾时 |
| `continueClone(ctx)` | 全部重新核对，另要数据盘已勾、`remaining` 不为 0、这一行同开始时一样并且仍然没有"GPU充足"，才点"继续"。操作到此结束 |
| `idDigests()` | 只读。返回 `digests`，各行实例 ID 的摘要 |
| `findCreated(digests, hostId, spec, id)` | 只读。返回 `count` 与 `rows`（候选的 `id`、`state`、`mode`、`timer`），别的行什么都不回答。`id` 可以不给 |
| `sshAddress(id)` | 这一行是运行中才读。从页面自己的数据里只读这一行的 SSH 主机、端口与登录指令三样，互相对得上、主机名以 `.seetacloud.com` 或 `.autodl.com` 结尾，才返回 `host` 与 `port`。拒绝时只说哪一项不过 |

放弃克隆的对话框用平时的 `dismiss(ctx)`。别的函数照旧点不了带"克隆""释放""扩容"的东西。

创建页的十二个函数，调用写成 `window.__autodlClone.page('<原机器ID>')` 这样，第一个参数都是原机器的实例 ID。

| 函数 | 做什么，返回什么 |
|---|---|
| `page(id)` | 只读。`region`、`billing`、`expandBytes`、`expandGb`、`models`（各型号的 `name`、`free`、`total`、`ticked`）、`all`、`count` |
| `tickModel(id, model)` | 点这个型号的复选框，并记下时刻。页面上已经勾着型号或"全部"就拒绝，那不是新进的创建页，`leave` 之后重进 |
| `pickCount(id, n)` | 点卡数 n，已经选中的返回 `already` |
| `hosts(id, gpus, expandGb)` | 只读。`rows`（每台主机的各格）、`complete`、`reference`、`referenceFree`、`suitable`、`unsuitable`。哪一格读不出来就拒绝，说是第几行的哪一列 |
| `loadMoreHosts(id)` | 把主机表的表体滚到底，页面随后加载后面的行 |
| `pickHost(id, hostId)` | 点这台主机的单选钮，已经选中的返回 `already`，不在露出的那一截里时先把表体滚到它 |
| `expansion(id)` | 只读。`need`（"需要扩容"勾了没有）、`value`（输入框里的字）、`gb`、`now`（页面已经取用的数）、`max`（这台主机的可扩容量） |
| `focusExpansion(id)` | 聚焦扩容的输入框，数字由浏览器工具打进去 |
| `prepareCreate(id, want)` | 只读，全部核对，返回 `copy` 与 `age` |
| `confirmCreate(id, copy)` | 最终确认。重新核对并同 `copy` 逐项相同，离 `tickModel` 不超过 120 秒，这个页面上还没有确认过，才点"创建并开机" |
| `result(id)` | 只读，任何页面上都能调用。`path`、`create`、`confirmed`、`prompts`、`dialogs`、`ids` |
| `leave(id)` | 点创建页的"取消" |

计费方式、地区、"优惠券"、"全部"与"需要扩容"，脚本里没有点它们的函数；账户余额它不读。
