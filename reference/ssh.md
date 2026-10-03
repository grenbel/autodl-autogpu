# ctl 与 SSH（ctl v0.9）

本文件写本机助手 ctl v0.9 怎么连实例、有哪些命令。实例端的守护脚本怎么判断见 `reference/guard.md`，本机记录、授权与账本、校准见 `reference/ledger.md`，控制台上的操作见 `reference/console.md`，没有空闲卡时的等卡与克隆见 `reference/clone.md`。

## 认准实例
- 别名只说明怎么连，它连到哪台实例会变（`~/.ssh/config` 被改过，实例重建后换了端口而别名没改）。所以除 `check`、`wait`、`doctor` 外，每条带别名的命令都认定一台实例：命令行给了 `--instance <ID>` 就是它，否则取本机记录里这个别名核实过的对应（`check 别名 --instance ID` 记下的）
- 发往实例的每条远端命令，在开始标记之后先比主机名，不是 `autodl-container-<ID>` 就什么都不做，以退出码 113 与一行标记结束。ctl 认出后退出 13，输出里 `instance_match` 为 false、`hostname` 是别名实际连到的那台（读不出主机名时为 null），后面的步骤（重发、等关机、别的远端命令）都不做。核对与命令在同一次远端 shell 里，中间没有空档
- 别名没核实过、又没给 `--instance`：不连接，退出 13，输出里没有 `instance_match`，`error` 说先 `check 别名 --instance ID`。照做就行，对上之后把原来的命令重发，这一种不用停下来问用户。本机记录不能用、又没给 `--instance`：退出 11；这时给命令加上 `--instance <ID>`，就不需要本机记录来认实例
- 退出 13 而 `instance_match` 为 false 的（别名连到了别的机器，`check` 自己比对不符也是这样）：立即停下，告诉用户，不换别的别名试，也不绕开 ctl 直接用 ssh。别名弄对之后 `check 别名 --instance ID`，再接着做
- `wait` 不核对（它排在核对之前，等关机时实例本来就连不上）：别名指向了别的实例时，它报的是那一台的模式，紧接着的 `check` 会拦住

## 连接与重发
- 每条远端命令前先向 stderr 回显标记 `__AUTODL_CMD_START__`；ssh 自己的消息用 `-E` 写进临时日志文件（LogLevel=VERBOSE），用完即删，出错时把最后几行附在报错里
- 结果分三种
  - started：看到了标记，或 ssh 自己没失败
  - not_run：没有标记，而日志用认证前特有的措辞报失败（"Connection closed by 主机 port 端口"、"Connection reset by 主机 port 端口"、"connect to host 主机 port 端口: "、kex_exchange_identification、banner exchange、Could not resolve hostname、Permission denied (、Host key verification failed），且没有任何会话迹象（Authenticated to、client_loop、channel N、Read from remote host、Timeout, server、Connection to 主机 closed）
  - uncertain：其余一切，包括没见过的失败
- ssh 超时（subprocess 等不到结果）一律算 uncertain，不算没执行
- not_run 时间隔 5 秒重发，连第一次在内最多试 3 次。uncertain 时只重发重复无害的命令，包括查询（status、check 读主机名、arm 之前与 calibrate 里取指纹、arm 与 autostart 成功之后记守护自报的那次 status）、deploy（每次用自己的 mktemp 临时文件，先校验 sha256 再替换）、install-autostart 与 uninstall-autostart、不带 --restart 的 revive、off-when-done、run（带请求号和命令校验）、arm（带请求号）；keep、quiet、deadline、push、revive --restart 与 calibrate 的采样（一次要跑几分钟）返回退出码 6；off-now 与 off-raw 转去等关机。看到标记之后才断开的（包括 subprocess 超时），一律不重发，按不确定处理（转发类命令返回退出码 6，off-now 与 off-raw 转去等关机）。守护脚本自己报的退出码 6（run 说不清任务有没有启动）也原样作为 6 返回
- status 查询本身结果不确定（超时，或看到标记后 ssh 以 255 结束）时返回 6，不返回 2；status 是只读查询，再查一次即可
- not_run 靠 ssh 日志的措辞判断，是启发式：前提是标准的英文 OpenSSH 日志、不用 ProxyJump，并且 "Authenticated to" 会写进日志（见下文，待实机核实）
- 判断实例连不上，要连续 5 次单独探测都失败，每次间隔 10 秒（wait 的 `--every`），等待按 `--wait` 或 `--timeout` 收尾。认证成功的探测算连得上。结果带 `"confirmed": false`，关没关以控制台为准
- off-now 与 off-raw 在命令一直没送到时回答 "not sent"，不会当成已关机。off-now 先认 dry-run 的那一行（dry-run 下守护也会先打印提交行），再按整行认提交行（`shutdown committed (...)`、`shutdown issuing`、`shutdown issued (...)`），有一行就算已提交、去等 SSH 断开，结果里 `shutdown` 为 committed；只收到开始标记、连接随后断开为 started；连标记都没有才是 uncertain；没有提交行时守护的 3、4、7、8 原样返回
- off-raw 直接执行 /usr/bin/shutdown，用在守护不能用的时候（没部署上，或守护进程起不来）。守护脚本在实例上时先用 off-now：守护进程没在运行它会自己把它起来（这次开机没 arm 过也行），起不来才报错。守护进程就待在一个名叫 `autodl-guard` 的 screen 会话里，所以它在运行时 off-raw 的第一道检查一定拒绝，列的是 `screen`（2026-10-02 实测），这时该用的是 off-now。不带 `--force` 时 off-raw 在同一次 SSH 里做两道检查，任何一道说在用或说不清都不关机（退出码 3），`--force` 两道都跳过
  - 先查 screen、tmux 会话和守护脚本登记的在跑任务，每项 10 秒超时（守护脚本不在时没有登记的任务，这一项跳过）；只有明确为空才放行，有东西、输出看不懂或查询失败都拒绝
  - 再做一次 5 秒的当场采样，就是守护的 `idle-check`（见 `reference/guard.md` 的"off-now"一节末尾）。守护脚本由 ctl 从本机经标准输入送过去运行，实例上有没有守护脚本、是什么版本都一样。有在用或读不到的信号就拒绝，输出里逐行列出，如 `cpu:busy`；只有采样自己答了 `idle: ...` 才往下关机，脚本没送到或不完整同样拒绝
  - 第二道是为会话与登记都看不到的工作加的：在 Jupyter 里起的、直接用 ssh 起的计算。它只看这 5 秒，这几秒里恰好没有明显活动的工作（在等待的脚本、交互式的操作）照样会被关掉，同守护按空闲关机是一个口径；采样结束之后、关机之前才起的工作也拦不住（off-now 与守护在状态不能用时重试关机之前的采样同样如此）
- off-raw 查 screen 的细节。screen 只认活着的会话，`screen -ls` 里行尾为 `(Dead ???)` 的套接字（上一次开机留下的）不算在用；列表要完整才信（计数行与会话行数相符，退出码 0 或 1，4.6.2 以后列出会话后退出 0、4.2.0 退出 1），`No Sockets found` 要退出 1，别的一律按说不清拒绝。`(Remote or dead)` 按在用算，核实后用 `--force`
- push 在上传开始之后超时的，报不确定（退出码 6），因为可能已经放到位。push 先在实例上目标的父目录里建临时目录解包，成功后才移到位；被替换的旧目标改名为 `.bak-<时间>-<进程号>`，脚本中途退出（含信号）时 EXIT trap 把它放回并清掉临时目录。pull 先解到本地目标旁边的临时目录，ssh 与 tar 都成功退出后才放到位，旧目标改名为 `.bak-<时间>-<随机串>`，移动失败或被 Ctrl+C 打断时放回。SIGKILL 或断电时可能留下 `.bak-*` 或临时目录
- push 打包时给每一项定权限（实例上的 tar 以 root 解包、按档案原样保留）：目录 755，普通文件 644，本机是 POSIX 而且文件有执行位时 755（Windows 上的执行位随扩展名，不算）；属主与组名清空、编号为 0
- 发往实例的值（push 的父目录、pull 的路径、run 的 --cmd 与 --log、arm 的 --env-setup）以盘符开头时 ctl 拒绝执行，那是 Git Bash 改写了参数的迹象，改用 scripts/ctl 启动器

## 命令一览
- `version` 打印 ctl 的版本（0.9.0）；`now` 打印此刻的 unix 秒（取 T0 用）；`doctor [别名]` 见下文
- 带别名的命令除 `check`、`wait`、`doctor` 外都可以加 `--instance <ID>`（见"认准实例"），下面不逐条重复
- `wait 别名 [--state up|down] [--mode gpu|nogpu] [--timeout 10m] [--every 10]`：`up` 时每隔 `--every` 秒探一次，连上后识别模式，模式识别不出或与 `--mode` 不符退出 1；`down` 时连续 5 次探测都失败才算连不上（`confirmed` 仍是 false）；到 `--timeout` 还没等到退出 1
- `tail 别名 任务名 [-n 行数]` 看任务日志的末尾，任务名写 `guard` 是守护自己的日志；`revive 别名 [--restart]` 把守护进程拉起来、不改任何设置；`push 别名 本地路径 实例上的父目录 [--overwrite] [--timeout 60m]` 与 `pull 别名 实例上的路径 本地目录 [--overwrite] [--timeout 60m]` 传文件，细节在"连接与重发"
- `arm 别名 --idle DUR` 之外只发给了的项（`--deadline`、`--keep`、`--grace`、`--interval`、`--mode auto|gpu|nogpu`、`--gpu-probes`、`--thr-gpu`、`--thr-cpu`、`--thr-io`、`--thr-net`、`--unreliable`、`--calib` 与 `--calib-coverage`、`--env-setup`、`--dry-run`、`--rearm`），取值由守护校验，时长先在本机校验；没有手工给阈值、不可靠信号与 `--calib`，也没给 `--interval` 与 `--gpu-probes` 时先查校准（见 `reference/ledger.md` 的"校准"），输出的第一行 `calibration: ...` 说明用了哪条（有不可靠信号时还写出关掉了哪些）或为什么用默认阈值
- `run 别名 名字 (--cmd 命令 | --cmd-file 文件) [--then-off] [--quiet DUR] [--log 路径]`；`quiet 别名 名字 DUR --reason 理由` 让在跑的任务从现在起算在用 DUR；`keep 别名 DUR --reason 理由`（没有 `--after-job`）；`deadline 别名 DUR`；`off-when-done`；`off-now 别名 --reason 理由 [--force] [--sample 秒] [--wait DUR]`；`off-raw`
- `deploy 别名 [--no-autostart]` 校验和对上后调用守护的 `install-autostart`，输出 `deployed`（守护脚本已就位时为 true）、`path`、`sha256`、`autostart`（installed、already，或 failed、not sent、uncertain 加原话）；装不上退出 1，这条 SSH 没送到退出 2、说不清退出 6，`--no-autostart` 跳过这一步。钩子是新装上的，就清掉这台实例的校准，结果写在 `calibration_forget`（清不掉时 deploy 退出 1，而 `autostart` 是 installed：自启装好了，只是旧校准还在，本机记录修好后 `calibrate 别名 --forget`）。所以 `deployed` 为 true 而退出非 0 时，要看 `autostart` 与 `calibration_forget` 分清是哪一项没成。输出里的 `note` 是一句固定的提醒，每次都有：换了守护脚本之后，正在跑的旧守护进程要到它下一次启动才换成新的（你的下一次 arm 或 `revive --restart` 会重启它，再就是下次开机）；`status` 的 `daemon_version` 与 `version` 相同就不用理会。`autostart 别名 install|uninstall` 单独装卸，退出码照守护
- `status 别名` 把守护给的键（包括 `deadline_in_s`、`keep_in_s`）原样收进结果，另算 `heartbeat_age_s` 与 `booted_at`。`booted_at` 是这次开机容器启动的时刻（unix 秒，按本机的时钟，也就是 T0 与账本用的那个钟）：本机收到回答的时刻，减去容器已经开了多久。后者由守护给的 `up`（内核开机时长）减去 `boot`（1 号进程的启动时刻，单位是时钟滴答）除以实例的 `getconf CLK_TCK` 得出，是一段时长，实例的时钟准不准都不影响；算不出时为 null；接手一台已经开着的实例时用它记账。守护还没部署时 status 退出 1、`guard` 为 `not deployed or failed`，先 deploy；`armed_by=boot` 时加一条 note，说这次开机是自启 arm 的、没有 env_setup，跑任务前先 arm（AI 的 arm 直接替换它）。status 每次成功都把守护报的 `autostart` 与 `boot_settings` 记进本机记录；`arm` 与 `autostart` 成功、在实例上出了错或说不清（退出 0、1、6）之后，ctl 也自己再读一次记下（记不进去只在 stderr 提一句，不影响命令），`auth show` 的 `guard_at_boot` 用的就是它。这一次读不到，或者 status 得到了实例的回答而守护答不出（没部署，脚本不在了），原来记的那条就去掉：宁可下一次开机多设一个临时定时，也不沿用可能已经不成立的旧回答
- `check 别名 [--instance ID] [--config]` 报用的是哪个 ssh、连不连得上。带 `--instance` 时另读主机名，与 `autodl-container-<ID>` 比较，结果写在 `instance_match`；一致时把别名与实例 ID 记进本机记录（这是别名被认作这台实例的唯一途径），不一致退出 13，读不到主机名按 ssh 的结果退出 2 或 6，记不进本机记录退出 11。`--config` 另打印 ssh 把这个别名解析成的主机、端口、用户与密钥文件路径，只在排查连不上时用
- `run` 另可带 `--req <请求号>`（16 位十六进制，不给时 ctl 自己取一个）。给了的话 ctl 先只读地问这个请求号起过这个任务没有，再决定发不发启动：起过，不再发，回答 `already` 与它现在的状态，退出 0；说不清（上一次开机在启动当中结束了），退出 6，什么都不发；没起过或这次开机里正在起，照常发给守护。守护自己只在同一次开机里认得同一个请求号，跨开机的"不重起"靠这一问。`job 别名 任务名 --req 请求号` 是单独的这一问，只读，回答 `started`（真、假，说不清时为 null）、`state`、登记在哪、是不是这次开机的、结束了的退出码
- 克隆时才用的几条，用法都在 `reference/clone.md`。`manifest 别名 [--content] [--compare 清单文件] [--changed-after unix时刻] [--project 目录] [--out 文件] [--timeout DUR]` 只读地列出数据盘上每个文件的路径与大小（带 `--content` 时另取每个文件的 SHA-256），写成项目 `.autodl/` 下的一个清单文件，并给出总字节数、估计的拷贝秒数与由它算出的票的时长；带 `--compare` 时说同那份清单一样不一样（不一样退出 1，列出前 20 条）；有登记的任务在跑或读不到守护的状态时退出 3；不算在内的是守护自己的状态、日志、脚本与平台放在数据盘顶层的 `.autodl/`。`spec 别名 [--gpu-model 型号] [--gpus N] [--driver 版本] [--cpu-per-gpu N] [--mem-per-gpu-gb N] [--min-system-bytes N] [--min-data-bytes N]` 只读地报显卡的型号、数量与驱动、CPU 配额折成的核数、内存上限、两个盘的总容量，给了哪样比哪样，有一样不符退出 1（无卡模式下读到的是无卡的配额，有卡开机时才用它核对）。`ticket write|read|extend|start|clear 别名 --txn 事务号 ...` 写、读、改、起循环、撤克隆票。`read` 只读，不查本机的克隆记录，可以不带 `--txn`，带了就另答票上的标记是不是这一次的（`mark_matches`，不是就退出 1）。其余四条都先在本机的克隆记录里核对事务号，并核对这个别名核实过的实例是这次克隆的原机器还是新机器（写票与 `clear --source` 对原机器，其余对新机器），对不上就不发任何远端命令、退出 1；退出 3 是拒绝（例如守护没配好时撤新机器上的票），4 是票的循环已经发出了关机，6 是撤票之后没等到循环的回执
- 克隆出来的新实例有自己的别名，写法同"别名的写法与第一次连接"，名字取原别名加后缀（如 `autodl-demo-c1`），密钥用同一把；原别名那一条不动。新别名照样要 `check 别名 --instance <新实例ID>` 核实
- `auth ...`、`log ...`、`usage ...`、`calibrate ...`、`clone-record ...` 见 `reference/ledger.md`

## 别名的写法与第一次连接
- 这一节的事都由你做。用户只做三件：把你给的公钥贴进控制台，把登录指令发给你，在浏览器里登录 AutoDL
- 密钥。先问用户有没有已经加进 AutoDL 的密钥，有就用它（要的是私钥文件在哪，不是它的内容）。没有就生成一把专用的，`ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519_autodl -C autodl-gpu`。`-N ''` 是不设口令：ctl 用 BatchMode，不会停下来问口令（用户想要口令的，由用户自己把密钥加进 ssh-agent）。同名文件已经在时不要生成，它问要不要覆盖就不答 y，换个文件名或问用户。然后把 `.pub` 文件里的那一行原样给用户，请用户贴进控制台实例列表上方的"设置SSH免密登录"（账号级，贴一次，对账号下所有实例生效）。私钥的内容不读、不显示
- 主机与端口。控制台上的登录指令是打码的，不点显示它的按钮；请用户复制这台实例的登录指令发给你，形如 `ssh -p <端口> root@<主机>`，从里面取主机与端口。密码不要，用户连密码一起发来的也不用、不存
- `~/.ssh/config` 里的一条别名至少有下面这几行，值换成这台实例的（密钥文件是加进 AutoDL 的那把公钥对应的私钥）

```
Host autodl-demo
    HostName <登录指令里的主机>
    Port <登录指令里的端口>
    User root
    IdentityFile ~/.ssh/id_ed25519_autodl
    StrictHostKeyChecking accept-new
```

- 最后一行不能少。ctl 一律用 BatchMode，ssh 不会停下来问要不要信任一台没见过的主机；新实例的主机密钥还不在 `known_hosts` 里，没有这一行，每次连接都在登录之前失败，ssh 的原话是 `Host key verification failed`。`accept-new` 只在第一次自动记下密钥，以后密钥变了照样拒绝。连的是不是这台实例不靠它判断，靠 `check --instance` 核对主机名
- 别名是你写的就带上这一行。别名是用户早先自己写的，开机之前用 `ssh -G <别名>`（只打印设置，不连接）看一眼 `stricthostkeychecking`：是 `ask` 或 `yes`，而用户又没有手动连过这台实例，就先告诉用户，经同意加上这一行，或请用户开机后自己在终端里 `ssh <别名>` 连一次并回答 yes
- 实例在控制台上已是运行中，而 `ctl wait` 一直等不到、`ctl check` 报连不上：这两条命令不显示 ssh 的原话，先查清原因再谈关机。`ctl check <别名> --config` 核对主机、端口、用户与密钥文件；再直接跑一次 `ssh -o BatchMode=yes -o ConnectTimeout=10 <别名> true` 看原话（只为诊断，它在实例上什么都不做）。`Host key verification failed` 照上一条办；`Permission denied` 是密钥不对或公钥没加进 AutoDL；`Connection refused` 或超时是主机、端口不对，或实例还没起来。修好之后从开机流程第 7 步接着做。这期间实例在计费，告诉用户；修不好才照 SKILL.md 的出错处理在控制台关机
- ssh 说 `REMOTE HOST IDENTIFICATION HAS CHANGED` 是这个主机与端口的密钥同 `known_hosts` 里记的不一样了（重置系统或更换镜像之后可能出现，没有实测过）。不自己删 `known_hosts` 里的条目，把原话告诉用户；用户确认是自己重置或换过，才由用户执行、或经用户同意后执行 `ssh-keygen -R '[主机]:端口'`，再连

## 守着任务到关机
- skill 没有专门等任务结束的命令。任务在跑时，隔一段时间 `ctl status <别名>` 看它的那一行，`job.<任务名>=` 后面的状态从 `running` 变成 `done:<退出码>` 就是结束了，几分钟查一次就够。启动时加了 `--then-off` 的，也可以用 `ctl wait <别名> --state down --timeout <时长>` 等实例关掉
- 你所在的环境能把等待放到后台、到时候再叫醒你的（后台命令、定时唤醒之类），就这样等，不必为了等而结束对话。任务一结束接着做后面的事：取回结果、关机并收尾，或接着跑下一个
- 要取回的结果在关机之前 `ctl pull`，关机后就连不上了；数据仍在数据盘上，下次开机再取也行（可以用无卡）。等的时间会超过上一次预算检查算到的时刻时，先 probe（SKILL.md 的"用量与预算"）
- 你的对话没了，任务照常跑，守护照常按空闲关机。这是兜底：能守着就守着，守护的设置照样要配

## 退出码
- 0 成功；1 出错（包括用法错误，argparse 原本的 2 会被读成"没送到、可以重发"）；2 命令没送到；3 拒绝，还在用；4 拒绝，有待重试的关机；5 拒绝，这次开机已经 arm 过；6 说不清是否执行了，先查 status 再决定要不要重发；7 已过最晚关机；8 off-now 正在准备；10 预算不够，或快用完而这个周期还没有用户的同意；11 本机记录不能用（读不出、格式不对、权限或链接不安全、时钟回拨），一律不自动修；12 没有授权，或授权的用法里没有这种模式；13 别名没有核实过，或它连到的不是这台实例（带别名的命令都会这样退出，见"认准实例"；`check --instance` 比对不符也是它）。守护的其他非零退出码报 1

## doctor 与启动器
- `doctor [别名]` 逐项报告 ok 与说明，全部 ok 退出 0，否则 1：python（3.8 或以上，而且有 `tarfile.data_filter`，pull 解包要用）；bash（Windows 上从 git 所在的目录往上最多三级找 Git Bash，再看默认安装位置；System32 里的 bash.exe 是 WSL 的）；ssh（真跑一次 `ssh -G -E <临时日志> -- <任意名字>`，只打印设置、不联网，退出 0 而且写出了日志才算能用，`ssh -V` 的版本只写进说明；ssh 运行不了或超时也记为这一项不通过，其余各项照样报告）；一个名字带空格和中文的目录与文件能写能读；本机记录能打开；在 Git Bash 里而 `MSYS_NO_PATHCONV` 不是 1（没经 scripts/ctl）时不通过；给了别名时再看它能否连上
- `scripts/ctl` 给了 `AUTODL_PYTHON` 就只用它，否则先试 python3 再试 python，每个先实际运行一次，核对版本与 `tarfile.data_filter`，三样都对才用（Windows 应用商店的占位 python3 静默退出 49，因此被跳过）；都不行时报错说要装什么或怎么设 `AUTODL_PYTHON`，退出 1。它还关掉 Git Bash 的参数改写，把脚本路径以 Windows 形式传给 Python

## 取值约定
- 时长写 90s、30m、2h，纯数字按分钟，最长 30 天；off-now 的 `--sample` 是整秒数；`auth check --hours` 是小时数，至多三位小数，按整秒向上取
- 金额按元写，至多两位小数（单价 0.98，预算 50yuan），内部按整数分存；GPU 小时预算至多三位小数（12.5gpuh），内部按千分之一小时存；负数、NaN、无穷与指数写法一律拒绝
- 实例 ID 照控制台的写法：10 位小写字母或数字、连字符、8 位小写十六进制（如 abcd123456-1234abcd），主机名是 `autodl-container-` 加它
- 按格式核对的取值（实例 ID、别名、任务名、时长、请求号、流水号、金额、小时数、时区、周期名）都整串匹配，结尾带换行的也不认
- 实例上的时长一律按开机时长算，arm、keep、deadline、quiet 只传相对时长；本地开关机记录用本机时钟；补记关机时，时刻取自扣费明细的不标，取自守护的 `last_shutdown_at`（它是最后一次关机尝试开始的时刻，偏早）或用户说的大概时刻的标 time=estimated
- `arm --dry-run` 只写日志不真关，用于测试，而且只对 arm 所在的那次开机有效。dry-run 触发后守护进程退出，要 `arm --rearm --dry-run` 重新配置

## AutoDL 实例上的坑
- 非交互 SSH 不加载 conda 与 CUDA 的 PATH，任务命令依赖 arm 时写入的 env_setup
- 自己用 ssh 起的 nohup 后台任务不在 run 的登记里，守护只能靠它的实际活动看到它：CPU、GPU、磁盘或网络没到阈值的阶段（等待、睡眠）会被当成空闲；一律用 run 启动，会安静的阶段声明安静期
- 关机后标准输出不可见，日志一律写文件
- skill 没有停掉单个登记任务的命令。任务不要了又不想关机，由你自己在实例上结束它的进程（例如再 `ctl run` 一条结束它的命令）；连同关机一起的，是用户当场同意后的 `off-now --force`
- 实例重启后，上一次开机的任务按启动标记判为 lost，不会再当成在跑；上一次开机的安静期、keep、最晚关机与最后一次在用都作废
- 无卡模式下 nvidia-smi 报无权限，cgroup 内存上限为 2 GiB，这两者一起用来判断无卡模式
- 以下两条是 2026-09-28 在另一台 AutoDL 实例上实测到的，尚未在别的实例上复核
  - /usr/bin/shutdown 只是几行 shell，清空回收站、向 1 号进程的输出写一条记录、杀掉 supervisord，容器随之退出。所以关机命令发出后 SSH 连接常常直接断开（ssh 退出码 255），这是正常的
  - 公网 SSH 端口上有扫描连接时，sshd 按 MaxStartups 在认证前随机丢弃新连接，所以一次 SSH 失败说明不了什么
- "Authenticated to" 这句日志在本机两个 ssh 程序里都有，推断在 LogLevel=VERBOSE 下认证成功时会写进日志，还没在实例上核实。没写时，会话建立后的失败多数仍按措辞落到 uncertain；认证前的措辞已收紧到只在那个阶段出现的写法，但文本判断不是证明
- 用 ProxyJump 连接时，跳板机的认证会让目标看起来连得上，等关机只会超时；AutoDL 的连接方式不用它
- AutoDL 容器里 `/proc/uptime` 是宿主机的开机时长（实测显示 230 天，容器才起几分钟），只在同一次开机里前后比较
- 一次检查持状态锁的时间很短（探测都在锁外）；关机时 sync（最多等 60 秒）与关机命令（经 timeout 最多 120 秒）都在锁内，这期间要取状态锁的命令会等，revive 不等这把锁
- 进程数耗尽时，bash 在命令替换 fork 失败后会中止整个脚本（本机 WSL 实测）。发生在守护进程主循环里，守护进程会退出，这时只剩控制台定时关机
