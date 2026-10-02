# 守护脚本与 SSH 细节（autodl_guard.sh 0.8.0，ctl v0.8）

本文件写实例端的守护脚本 0.8.0 与本机助手 ctl v0.8 实际怎么做。控制台上的操作见 `reference/console.md`。

## 守护脚本做什么
实例端的 `/root/autodl-tmp/.autodl-guard/autodl_guard.sh` 只决定什么时候调用官方的 `/usr/bin/shutdown`。它按实例实际在做的事判断是否在用：GPU 利用率、容器的 CPU 时间、磁盘读写字节、网络收发字节。用 run 登记的任务、screen 与 tmux 会话本身都不再算在用，登记的任务只用于日志、状态、off-now 的拒绝、跑完就关和安静期。装了随开机自启时，每次容器启动由 boot 按上一次 arm 为这种模式存下的设置自动 arm 并运行守护，见"随开机自启"一节。

守护进程每个间隔（默认 60 秒）检查一次，按下面的次序判断，前一条成立就不再往下看
1. 状态版本不是 2、这次开机没有 arm 完（包括 arm 中途被打断）、开机标记读不到、状态残缺或取值越界（status 的 `needs_rearm=1`）：不做任何新的决定，只重试这次开机已经发出的关机。`off-now --force` 发出的照样重试；其余的先放锁做一次当场采样（off-now 的那一种，这时按当场识别的模式与默认阈值，不读策略），有在用或读不到的信号、或读不到开机时长，就撤销这次关机（日志 `PENDING SHUTDOWN CANCELLED ... (a live sample: the state cannot be used)`），都空闲才重试；采样期间被重新 arm、或这次关机已被撤销的，这一次检查什么都不做。上一次开机留下的待重试标记绝不重试。状态残缺的原因按类记一次日志（`STATE CORRUPT ...` 或 `STATE OF ANOTHER VERSION ...`）
2. 这次开机已经发出过关机（`shutdown_pending`）：`off-now --force` 发出的照样重试；其余的先按第 3、4 行看是否在用，在用就撤销（日志 `PENDING SHUTDOWN CANCELLED`）。守护自己决定的三类（空闲、最晚关机、跑完就关）还要等 keep 与安静期结束，并且要有可靠的信号；AI 要求的 off-now 不等 keep 与安静期
3. 读不到开机时长：算在用；时钟恢复后的第一次检查也算在用（最后一次在用记为那时）
4. 任一信号在用或读不到、keep 没到期、有活着的任务的安静期没到期：算在用，最后一次在用记为此刻
5. 当前模式下没有一个既适用又可靠的信号：第 6 到 8 行的关机一律不做，只剩控制台定时关机与 AI
6. 过了最晚关机：不在用满 grace（默认 120 秒）就关
7. 设了跑完就关，且登记的任务都已结束：不在用满 grace 就关
8. 其余：不在用满 `--idle` 就关

空闲时长从最后一次在用算起。keep 或安静期在两次检查之间到期时，最后一次在用记为它的到期时刻；几个保护一起到期时取最晚的，而且只向前推进，不会把更晚的活动拉回去。grace 只是在最晚关机之后、跑完就关时把所需的空闲时长从 `--idle` 缩短为 grace，起算点不变：到点时已经空闲满 grace 的，在到点那次检查就关。

## 信号与阈值
- GPU：有卡模式下每个间隔做 K 次 `nvidia-smi` 探测（`--gpu-probes`，默认 3，0 到 20），任一次利用率不低于 `--thr-gpu`（默认 5%）为在用。有一次失败、回答的 GPU 数与第一次不同、或一次都没做成，算读不到。无卡模式不探测，GPU 不适用。有卡时 `--gpu-probes 0` 必须同时给 `--unreliable gpu`，否则 GPU 永远读不到、永远算在用
- CPU：cgroup v2 `cpu.stat` 的 `usage_usec`，占单核的比例不低于 `--thr-cpu`（有卡默认 5%，无卡 3%，最多一位小数）为在用。守护自己的开销不扣：实测守护与每分钟 3 次探测合计约占单核 0.5%，离阈值还远
- 磁盘：cgroup v2 `io.stat` 各设备读写字节之和，不低于 `--thr-io`（默认 5e5 B/s）为在用。AutoDL 实例上数据盘阵列与成员盘各算一次，计数约为实际写入的 3 倍
- 网络：`/proc/net/dev` 除 lo 以外的收发字节之和，不低于 `--thr-net`（默认 1e4 B/s）为在用
- CPU、磁盘、网络按与上一次检查的差算速率，基线存在状态文件 `counters`。某一项现在或基线读不到、比基线小（回退或被重置）、两次读数相隔不到 1 秒（太早）或超过 1.5 个间隔加 min(10 秒, 0.4 个间隔)（太晚），这一项算读不到，也就算在用。一次检查写不进新基线时也算在用（原因 `counters:unstored`），否则下一次会在更长的时间上平均，把一段突发的工作摊到阈值以下
- `--unreliable gpu,cpu,io,net` 标出的信号不参与判断，status 的 `sig.*` 显示为 off；一个可靠的信号都没有时 status 标 `no_reliable_signal=1`，第 5 行生效。校准会自动给出该关掉的信号；手工给是在放宽判断，这些信号上的活动守护就看不到了，只在某个信号在这台实例上一直读不到、又得到用户同意时用
- 这些默认阈值来自在一台实例上的初步校准，偏向判为在用；别的机器与镜像的底噪可能不同，用 `ctl calibrate` 校准（见"校准"一节）。只动 GPU 的短促任务在每分钟 3 次探测下可能漏看，这类任务要声明安静期或 keep
- status 的 `sig.*` 写成 `状态:读数`。状态是 unknown 时冒号后面是读不到的原因
  - `unread`：这一项现在或基线读不出来。一直如此，多半是这台实例的 cgroup 不是 v2，或没有挂出 `cpu.stat`、`io.stat`
  - `went-back`：计数比基线小，被重置过。`too-soon`、`too-old`：两次读数相隔太近或太远（守护刚启动，或上一次检查被拖住过），下一次检查通常就好
  - `probe-failed`：这个间隔里有一次 `nvidia-smi` 探测失败或超时，或回答的 GPU 数变了。多半是驱动有问题，或这次其实是无卡模式而被当成了有卡（比一下 `mode` 与 `mode_now`）。`no-probe`：这个间隔一次探测都没做成
  - 一直读不到的信号让守护不会按空闲关机。能修就修（模式认错了就 `arm --rearm` 让它重新识别）；这台实例上就是读不出的，先告诉用户并得到同意，再把它关掉（用 `--unreliable`，或跑一次校准让它自动给出，两种都要先同意），之后只在这个信号上看得出的工作要靠安静期或 keep

## 时间与守护的节奏
- 空闲、keep、最晚关机、安静期这些时长一律按内核的开机时长（`/proc/uptime`，整秒）计，墙钟被调整不影响它们。AutoDL 容器里这是宿主机的开机时长，容器重启不归零，所以存下的值只对 `armed_boot` 那次开机有效，开机标记一变就当没有 arm。状态与 status 里的 `*_at` 是墙钟时刻，只作显示；`*_up` 是开机时长
- 每个间隔是一个窗口：K 次探测摊在窗口里，第一次在窗口开始时做；窗口里的每段等待最多 5 秒，醒来重读最晚关机，中途调早的最晚关机几秒内生效（代价是每分钟最多醒 12 次）；每次探测的时限不超过它的份额、5 秒与窗口剩余的时间（默认上限 10 秒，`AUTODL_PROBE_TIMEOUT`），也不会是 0：timeout 把 0 当作不限时，不是正数的时限直接算探测失败。最晚关机落在窗口之内时窗口在最晚关机处结束，还没做的探测摊到剩下的时间里（每秒最多一次），来不及做的不算失败
- 探测在不持锁时做，检查本身才拿状态锁
- 守护启动时先写心跳；这次开机有待重试的关机、或已过最晚关机时，当场检查一次，否则等满一个间隔再做第一次检查（速率要一个间隔才有意义）
- 每次写入新设置的 arm（含 `--rearm`；同一请求的重发不算）之后，正在跑的守护会被停掉再起一个。每次 arm 在写完全部设置之后写一个代号（`arm_gen`），窗口开始时先读它；检查时代号已经变了（窗口开始于上一次 arm 之前），就丢弃这次检查、不动基线（日志 `CHECK SKIPPED`）

## keep、最晚关机、跑完就关、安静期
- `keep DUR [--reason TEXT]`：从现在起 DUR 内算在用，到期后空闲从到期时刻算起；它取消跑完就关。最晚关机让 keep 提前结束：keep 的到期取它与最晚关机中较早的
- `deadline DUR`：最晚关机改为现在加 DUR（至少 1 秒），可以提前也可以推后，但到点之后不能再改。到点后拒绝新的 run、keep、quiet、deadline（退出码 7），已有的 keep 失效，到点前声明的安静期照旧有效到它的到期时刻；之后只要不在用满 grace 就关，在用就一直等。所以它不切断还在干活、或处于安静期里的任务。没有声明安静期、又没有任何活动的任务按空闲处理；卡死却一直占满资源的进程会让它一直等，要绝对的上限就另设控制台定时关机。到点之后用户还要多留，只剩一条路：`arm --rearm` 带全部参数重新配置（它替换这次开机的全部设置，新的 `--deadline` 从这时起算）。最晚关机是用户定的上限，用户当场要求才这样做，之前先 probe。`--rearm` 之后 keep 与跑完就关都没有了（还要的话 keep 写成 arm 的 `--keep`，跑完就关再发一次 `off-when-done`），待重试的关机被撤销；登记的任务不属于配置，照旧在，它们声明过的安静期也照旧算到原来的到期时刻
- `off-when-done [--reason TEXT]`：登记的任务都结束后，不在用满 grace 就关。它清掉 keep（keep 当时还没到期的，最后一次在用记为此刻）。它只让实例提前关，不保护任务。run 登记新任务会取消它，除非带 `--then-off`
- 安静期：启动时 `run NAME --quiet DUR ...`，或对在跑的任务 `quiet NAME DUR [--reason TEXT]`（重新声明可以延长也可以缩短，至少 1 秒）。到期时刻是声明那一刻加 DUR（`run --quiet` 在任务启动时声明），不是从安静的那一段开始时起算，所以 DUR 要盖到最后一个安静阶段结束。任务活着、没到到期时刻，就算在用（原因 `quiet:NAME`），别的活动不会刷新它，所以卡死的任务最多拖到到期时刻；到期后空闲从到期时刻算起；任务一结束声明就失效。任务是否还活着说不清时按活着处理；声明读不到、或第一行不是"正整数的结束时刻加开机标记"时，任务活着期间按在用算（原因 `quiet:NAME:unknown`，日志 `STATE CORRUPT quiet of job(s) [...]`）。`quiet` 要求已 arm、没有待重试的关机（4）、没有正在准备的 off-now（8）、没过最晚关机（7）、任务在跑；声明是任务目录里的一个文件 `quiet`（第一行 `UNTIL_UP BOOT`，第二行原因），一次原子写入，写不进去时旧声明原样保留，日志 `QUIET job=NAME until_up=N reason=[...]`，status 每个活着的、有安静期的任务一行 `quiet.NAME=剩余秒数`（到期后仍在跑为 0，读不到为 unknown）

## off-now
`off-now [--force] [--sample SECONDS] [--reason TEXT]` 按事务处理：
0. 不持锁：先确保有 0.8 守护在跑，它负责在关机命令失败、或 off-now 死在提交点之后时重试这次关机；起不来（包括这次开机里有 0.7 守护在跑）就什么都不改、报错退出（1）。已有待重试的关机而没有守护时（例如没 arm 的 off-now 死在提交点之后），这一步拉起的守护启动时就看到它，当场重试
1. 持状态锁：这次开机已有待重试的关机，就如实报告并退出 4（`shutdown already pending (kind=... reason=[...])`）；已有别的 off-now 在准备，就拒绝（8）；否则写下自己的"准备"（状态文件 `prep`：随机令牌、开机时长、开机标记），放锁。准备在的这段时间里，arm、revive、run、keep、quiet、deadline、off-when-done 与另一个 off-now 都拒绝（8，闸门），status、logtail、sample 照常；准备超过 120 秒（按开机时长计，读不到开机时长期间一直有效）或属于别的开机就作废，下一个 off-now 接手，守护检查时顺手删掉（日志 `OFF-NOW PREPARATION VOID`）
2. 不持锁（`--force` 跳过这一步）：有登记的任务在跑就拒绝（3，输出 `refused: still in use`，之后每行一个 `job:NAME`）；再当场采样 `--sample` 秒（整数，1 到 60，默认 5，环境变量 `AUTODL_OFFNOW_SAMPLE` 可改默认），按与检查相同的规则与阈值判断，有在用或读不到的信号就拒绝（3，之后每行一个原因，如 `cpu:busy`）。CPU 扣掉 off-now 自己与守护这段时间的用量（读不到就不扣；它们的读数夹在两次计数读数之间，扣的只会少不会多）；速率的时长取睡足的采样时长与开机时长之差中较大者；GPU 探测每秒最多一次，最后一次紧挨采样末尾。没 arm 时按当场识别的模式与默认阈值判断，识别不出按有卡探测。一个可靠的信号都没有时不因此拒绝，输出一行说明
3. 再持状态锁：准备仍是自己的（被别的 off-now 接手则退出 8，不删它的）、仍在 120 秒内（按开机时长计，读不到开机时长时算在内；过期就放弃，3：闸门失效期间别的命令可能改了 keep 或最晚关机）、守护仍活着（否则删掉准备、不提交，1）、没有别人已提交的关机（4）、（不带 `--force` 时）仍没有任务在跑、采样结束到此刻不超过 1 秒。最后一项不过就放锁重采一次，仍不过就拒绝（3）
- 然后提交：`record_shutdown` 先把 `shutdown_pending` 写 0，再暂存开机标记、原因与类型（`shutdown_pending` 为 0 时不起作用），最后写 1，这是提交点，之后才写 `last_shutdown_reason`，输出 `shutdown committed (...)`；之后才清 keep、删自己的准备、执行关机。sync 之后、调用关机命令之前输出一行 `shutdown issuing`（dry-run 时没有这一行），ctl 据此区分"已经开始关机、连接随之断开"与"说不清"。记录写不进去时删掉准备、报告没有提交关机（2），策略状态不变
- 任何一步拒绝或出错，只删令牌相同的准备。在提交点之前死掉的，策略状态不变，只留下一个准备（按第 1 步所说的规则作废）与暂存的、不起作用的关机细节；在提交点之后死掉的，守护照常重试这次关机，off-now 类型的重试不等 keep
- 直接用 SSH 或 Jupyter 临时起的任务无法完全拦住（包括采样结束到提交之间不到 1 秒的空档）。`--force` 跳过任务与采样两项检查，只在用户当场同意时用
- `idle-check [--sample SECONDS]` 把第 2 步的当场采样单独做成一条只读命令：不拿锁、不写状态、连守护目录也不建，不需要 flock（有卡时探测要 timeout，没有就算读不到）。都空闲时输出一行 `idle: ...`、退出 0；有在用或读不到的信号、读不到开机时长，或一个可靠的信号都没有，退出 3，第一行说明，之后每行一个原因（如 `cpu:busy`、`gpu:unknown`）。判断的依据同第 2 步：这次开机 arm 过而且状态可用，就按它的模式、阈值与不可靠信号，否则按当场识别的模式与默认阈值。ctl 的 `off-raw` 用它，守护在状态不能用时重试关机之前用的也是这一种采样

## 命令的约束
- arm：`--idle DUR` 必填（至少 1 秒）；`--deadline DUR`、`--keep DUR` 可选；`--grace DUR`（默认 120 秒，最多 1 小时）、`--interval DUR`（1 秒到 1 小时，默认 60 秒）、`--mode auto|gpu|nogpu`、`--gpu-probes K`、`--thr-gpu P`（0 到 100 的整数）、`--thr-cpu P`（0 到 100，最多一位小数）、`--thr-io N`、`--thr-net N`、`--unreliable LIST`、`--calib ID [--calib-coverage verified|unverified]`、`--env-setup STR`、`--dry-run`、`--rearm`、`--req ID`。所有参数先校验，再动状态。成功时输出一行 `armed mode=M idle=Ns deadline_in=Ns keep_in=Ns dry_run=0|1`
- arm 只在每次开机后执行一次，同一次开机里再 arm 会被拒绝（5），要替换全部配置用 `--rearm`（它也撤销待重试的关机）；boot 的 arm 除外，AI 的 arm 直接替换它。带同一个 `--req` 的重发直接报成功、不改任何设置。写入次序：先把 `shutdown_pending` 写 0，再写 `arm_incomplete`（`T KIND MODE`）、`schema` 与全部设置、这一刻的计数基线、代号、请求号、`armed_by`、（AI 的 arm）这种模式存给下次开机的设置 `boot.<模式>`、本次开机的标记，最后删掉 `arm_incomplete`。标记还在就算 arm 没完成：status 显示 `arm_incomplete=1`、`armed_this_boot=0`，守护不做新的决定，run、keep 等命令拒绝，再发一次 arm（同一个或新的请求号，带不带 `--rearm` 都行）会重写全部配置。配置写入后守护起不来时 arm 报错并说明要 revive
- 这次开机里有 0.7 的守护在跑时 arm 与 revive 都拒绝，任何命令也都不在它旁边启动 0.8 守护，见"从 0.7 升级"
- 本次开机的标记是 1 号进程的启动时刻（`/proc/1/stat`），每条命令开始时读一次，status 的 `boot` 一行显示它。读不到时 arm 拒绝，run、keep 等也拒绝，已有的配置不算本次开机的
- 守护进程没在跑时用 revive，它不改任何设置；revive 起不来时明确报出守护进程现在没在跑
- revive、arm、run、keep、quiet、deadline、off-when-done 都持有一把守护进程不参与的生命周期锁（先取它，再取状态锁），并在锁内检查本次开机是否已配置完成。revive 从不等状态锁（守护卡在检查或 sync 里持着它时，`revive --restart` 仍要能换掉守护），它看闸门时也不拿状态锁：准备是整份改名写入的，看完之后才写进来的准备只会碰上一次守护启动。revive --restart 先发 TERM，15 秒后还没停就 KILL，只杀守护进程本身，而且只向 PID 与启动时刻都与记录一致的进程发信号；对不上时不发，报守护进程停不下来
- 持锁期间不启动任何带着锁描述符的外部程序。状态文件与任务文件用 bash 内建命令读，时刻用 printf 内建取；mv、rm、mkdir、sleep、探测、sync、关机命令都先关掉 6、8、9 号描述符；耗时的命令替换在子 shell 里先关掉它们；等某把锁的 flock 进程也不带另一把锁。所以守护进程或调用者被杀后，没有留下的子进程还占着锁或让守护进程看起来活着；这些子进程会自己跑完（比如一条已经发出的关机命令）
- 关机前只刷守护目录所在的那个文件系统（`sync -f`；它失败时，比如 coreutils 8.24 以前没有 `-f`，改刷全部），放在后台，最多等 60 秒（`AUTODL_SYNC_WAIT`）。某个文件系统没有回应时，sync 会停在内核里，任何信号都结束不了它；等满之后不再等它，照样关机，日志里记一行 `sync did not finish`。磁盘正常、只是 60 秒还没刷完时也一样。关机没成功、守护进程重试时，上一次的 sync 还没结束就不再另起（日志 `an earlier sync is still running`）
- flock（util-linux）与 timeout（coreutils）都是必需的，缺了任一个守护脚本就拒绝运行（version、help、sample、uninstall-autostart 除外；sample 要做 GPU 采样时也要 timeout），连状态目录也不建。install-autostart 同样要求两者，但要等路径核对过后才建守护目录。探测一律经 timeout，没有它探测直接算失败，从不无时限地跑
- 退出码：3 = off-now 被拒（有任务在跑、当场采样有活动，或判断依据不再可靠：准备过期、采样两次都超过 1 秒才核对）；4 = 已经发出关机，run、keep、quiet、deadline、off-when-done 都拒绝，arm --rearm 可以取消（off-now 此时如实报告已有的关机）；5 = 本次开机已 arm；6 = run 说不清任务有没有启动；7 = 已过最晚关机，run、keep、quiet、deadline 拒绝；8 = 有 off-now 正在准备，除只读命令外都拒绝；2 = 关机的记录写不进去，没有关机
- run、keep、quiet、deadline、off-when-done 在改状态或启动任务之前先确保守护进程在跑，拉不起来就报错、什么都不改。实例还没 arm 时这些命令直接报错
- run 登记时把最后一次在用记为此刻（给 AI 的下一条命令留时间），任务结束时再记一次。run 经 stdin 收命令时核对 `--cmd-sha256`，不符就拒绝、什么都不登记。登记的最后一步写 req.pending。启动用一次性令牌，runner 在状态锁内核对令牌、确认没有别的 runner 后才认领；它在把命令交给进程之前先写 spawning，交给进程之后写 running。启动以 running 为准，之后 req.pending 改名为 req。等不到 running 时分三种：runner 还活着，或者 spawning 已写而 runner 死了，命令都可能已经在跑，报退出码 6，这个请求以后也不会再启动；runner 死在写 spawning 之前，命令肯定没跑，才永久放弃这次启动（rc 记为 launch-failed），迟到的 runner 什么都不做。同一请求再来时按同样的规则处理，只有确定没启动过的，才由它在锁内接手重新启动。命令已经跑完、只是 running 没写进去时，run 与重发都报已启动、已结束和 rc，退出码 0。spawning 写不进去时 runner 不启动命令。任务名 guard 保留给守护日志
- "任务在跑"的判断（off-now、跑完就关、安静期用）：任务的进程组里还有进程，或者还有进程的环境里带着这个任务的标签（`AUTODL_GUARD_JOB`），或者包装进程还活着（按进程号加启动时刻核对）。标签会传给任务的所有子孙，离开进程组的也带着；包装进程等这两样都没了才写结束标记。每次检查用 bash 内建命令把 /proc 读一遍；某个进程的 environ 读不到就跳过；这一遍连自己的条目都读不到时，所有登记的任务都按可能在跑处理。任务目录里的文件在却打不开、或内容不合格，这个任务也按可能在跑处理
- env_setup 与任务命令分开存放，先执行 env_setup，失败就不跑任务，退出码记为 97
- 关机时先把 `shutdown_pending` 写 0，再写开机标记、原因、类型与次数，最后写 1；除最晚关机外，任何一步写不进去都不关机
- 状态值的核对：`idle_s`、`grace_s`、`interval_s`、`gpu_probes`、各阈值、`deadline_up`、`keep_until_up`、`last_active_up` 必须是不带前导零的十进制整数且在各自的范围内，时刻不得早于 `armed_up`，`mode` 与 `unreliable` 取值合法；不合格时 status 标 `needs_rearm=1`，守护不做新的决定（第 1 行），要 `arm --rearm`。`last_active_up` 晚于此刻时记 `STATE CORRUPT` 并按在用处理，下一次就恢复正常

## 模式识别
`nvidia-smi -L` 能列出 GPU 为 gpu；列不出且 cgroup 内存上限不超过 3 GiB（无卡模式是 2 GiB）为 nogpu；其余为 unknown。arm 遇到 unknown 会拒绝，只有用户核实了模式才用 `arm --mode gpu` 或 `--mode nogpu` 指定。无卡模式下 `nvidia-smi` 报无权限，检查时不探测 GPU（识别模式时仍会调用一次 `nvidia-smi -L`，经 timeout）。off-now 在没 arm 时识别不出模式，按有卡探测（探测失败即算在用）。status 的 `mode` 是 arm 时定的，`mode_now` 是此刻识别的。

## 随开机自启
- `install-autostart` 在 `/etc/profile.d/` 里写一个钩子 `autodl-gpu-guard.sh`（首行 `# autodl-gpu guard autostart`，权限 644，先写临时文件再改名，写后读回核对）。AutoDL 容器的 1 号进程是 `bash /init/boot/boot.sh`，它 source `/etc/profile`，后者执行 `/etc/profile.d/*.sh`；登录 shell 也执行它们，所以钩子只在 `BASHPID` 为 1 且 `$0` 为 `/init/boot/boot.sh` 时动作：经 `/usr/bin/env` 给出 `AUTODL_GUARD_HOME` 与固定的 `PATH`，用 `setsid` 起一个脱离的 `bash -p -c`，输入输出都接 `/dev/null`，立刻返回。它在 1 号进程里不设变量、不改选项、不读数据盘、自己不输出、不失败。脱离的 shell 每秒看一次守护脚本可不可读，最多 60 秒，可读就 `exec bash -p <守护脚本> boot`，一直不可读就安静退出
- 钩子里写死守护脚本的绝对路径与 `AUTODL_GUARD_HOME`，都加单引号。两者有一个不是绝对路径、或含单引号或换行时 install 拒绝（1），此前什么都不建、不写；钩子要运行的东西缺了也拒绝，并列出缺什么：守护脚本本身要是可读的普通文件（`bash -s` 从标准输入读进来的不行），`/usr/bin/env` 与 `/bin/bash` 要可执行，钩子的 `PATH` 里要有可执行的 `setsid`、`sleep`、`flock` 与 `timeout`（后两个是 boot 自己要用的，只在安装者的 `PATH` 里有不算）；`/etc/profile.d` 不存在也拒绝。装好（或已装好）后输出下次开机会用的设置，与 status 的 `boot_settings` 相同。再装一次时内容相同报已安装、不动文件；首行是我们的标记而内容不同（旧路径、旧版本）就替换；首行不是这个标记就拒绝、不动它。`uninstall-autostart` 只删我们的，不是就拒绝，没有就报未安装。两者都可重复执行。install 需要 flock 与 timeout（boot 要用），路径核对过后才建守护目录；uninstall 两者都不需要，也不建目录
- boot 先把启动环境里的 `BASH_ENV`、`ENV`、`SHELLOPTS`、`BASHOPTS`、`CDPATH`、`GLOBIGNORE` 与导出的函数去掉，重新运行自己一次（`bash -p` 只管 bash 自己，管不到它启动的 bash 脚本，比如关机脚本），日志记 `BOOT start`。之后每一圈取生命周期锁与状态锁，按下面的次序查，第一条成立的决定这一圈；结果写进 `state2/autostart`，status 的 `autostart_this_boot` 显示本次开机的结果
  1. 这次开机有 0.7 守护在跑：`skipped:07`
  2. 这次开机已经 arm 过（AI 的 arm，或先跑的 boot）：`skipped:armed`，只确保守护在跑
  3. 有待重试的关机：`skipped:pending`，只确保守护在跑，由它重试
  4. 有没做完的 arm（`arm_incomplete` 在）：`skipped:incomplete`
  5. 有 off-now 正在准备：放锁等 `min(2, 余量)` 秒再回到开头；余量从 `AUTODL_BOOT_GATE_WAIT`（默认 130 秒）起每次减去等的秒数，不看时钟，不为正时为 `skipped:gated`
  6. 两种模式都没存过可用的设置：`skipped:no-settings`
  7. 还没识别模式：放锁识别，再回到开头；识别不出为 `skipped:mode-unknown`
  8. 否则用这种模式存下的设置 arm（`armed:saved`），这种模式没存过时用切换后的取法（`armed:fallback`），记 `armed_by=boot`，然后变成守护（`exec bash -p <守护脚本> daemon`，不经 screen）
- 读不到开机时长时不 arm（`skipped:no-uptime`）；读不到开机标记时只记一行日志，不写结果。除第 2、3 种外，不 arm 时都不起守护，等 AI arm，这期间不会因空闲关机。每种不 arm 的情形日志都记一行 `BOOT does not arm: 原因`；arm 时记 `BOOT armed mode=... from=saved|fallback ...`，存下的是 dry-run 时另记一行
- 识别模式不持锁，有预算（`AUTODL_BOOT_MODE_BUDGET`，默认 60 秒）：每次探测的时限取 10 秒与剩余中较小者，识别不出就等 `AUTODL_BOOT_MODE_WAIT`（默认 5 秒）再试。探测按开机时长的实际推进扣预算（至少 1 厘秒），超时或读不到开机时长时才扣满时限；等待扣它请求的时长与实际推进中较大的。这三个环境变量不合格时用默认值，日志 `BOOT ignores ...`
- 存给下次开机的设置：AI 的 arm 与 rearm 在提交之前，把这次的空闲时长、grace、间隔、探测次数、四个阈值、校准标识与覆盖、不可靠信号、dry-run 按这次的模式写进 `state2/boot.gpu` 或 `state2/boot.nogpu`（一行 12 个字段），写不进去 arm 就没做完；boot 的 arm 不写它。读时照 arm 的全部校验，不合格按没有处理，只有 boot 读到时记一次 `STATE CORRUPT boot.<模式>`，status 读到时不记
- 切换后的取法：这种模式没存过设置时，空闲时长、grace、间隔与 dry-run 取另一种模式存下的，阈值与探测次数用这种模式的默认值，不可靠信号为空，校准为 default（阈值与可靠性按模式校准，不跨模式沿用）
- dry-run 原样继承，自启不会把它改成真关机；`env_setup` 不存，boot 的 arm 不带它，AI 开始工作时照常 arm
- AI 的 arm 直接替换 boot 的 arm，不用 `--rearm`（退出码 5 只挡 AI 自己的第二次 arm），并照常重启守护
- 一次 arm 没做完时 `arm_incomplete` 记着 `T KIND MODE`（时刻、arm 或 boot、这次的模式），boot 见到它就不 arm；AI 的下一次 arm 见到 KIND 为 arm 的，先删掉那种模式存下的设置（可能写了还没提交），KIND 为 boot 时什么都不删，内容读不懂（不是恰好一行三个合格字段）时两份都删。所以一次失败的 arm 不会让下次开机用上它的设置
- status 的四个键：`autostart`（installed；stale，是我们的但内容与现在要写的不同；foreign，不是我们的；none）、`armed_by`（本次开机已 arm 时为 arm 或 boot，否则为空）、`boot_settings`（下次开机各模式会用的设置，每种开机时会 arm 的模式写成 `模式:空闲秒数s`：自己存了可用设置的用它；自己没存或存的不可用、另一种模式存了可用设置的，按切换后的取法再加 `:fallback`（另一种模式的时长与 dry-run，这种模式的默认阈值）；是 dry-run 的再加 `:dry-run`，如 `gpu:120s:fallback nogpu:120s`、`gpu:900s nogpu:120s:dry-run`；两种都没有可用设置时为空，有没做完的 arm 时也为空，因为这时开机不 arm）、`autostart_this_boot`（本次开机 boot 的结果，没有为空）
- 钩子要到第一次 deploy 才装上，所以第一次开机时还没有它；本机记录看不出钩子在的那次开机（`auth show` 的 `guard_at_boot` 里没有要开的模式），由开机前设的临时定时关机兜底，arm 成功后取消（SKILL.md 开机流程第 2、8 步）
- 已知限制：系统盘被重置或换镜像后钩子会消失（`autostart=none`，要重新 install），而本机记录的 `guard_at_boot` 这时还是旧的，所以用户说换过镜像或重置过系统时，下一次开机照它为空办，开机前设临时定时（SKILL.md 开机流程第 2 步）；平台改了开机脚本的路径或不再 source `/etc/profile`，自启就不再生效（`autostart_this_boot` 一直为空）；识别不出模式、两种模式都没存过设置、或有没做完的 arm 时，这次开机没有守护，等 AI arm

## 状态与日志
- 0.8 的状态在 `state2/` 下，一个值一个文件；0.7 的 `state/` 不读不写（只看它的守护锁是否被占用：arm、revive 与每次启动 0.8 守护时）。配置：`schema`、`armed_boot`、`arm_req`、`arm_incomplete`（只在 arm 进行中或被打断时存在，内容 `T KIND MODE`）、`armed_by`（arm 或 boot）、`armed_at`、`armed_up`、`arm_gen`、`mode`、`idle_s`、`grace_s`、`interval_s`、`gpu_probes`、`thr_gpu`、`thr_cpu`（0.1% 为单位）、`thr_io`、`thr_net`、`unreliable`、`calib`、`calib_coverage`、`env_setup`、`dry_run`。时刻：`deadline_up` 与 `deadline_at`、`keep_until_up` 与 `keep_until_at`（没设为 0）、`last_active_up`、`last_active_at`、`active_why`、`clock_lost`。计数：`counters`（`UP_CS CPU_USEC IO_BYTES NET_BYTES`，读不到的一项写 `u`）与最后一次检查的 `signals`。关机：`shutdown_pending`、`shutdown_boot`、`shutdown_kind`（idle、deadline、when-done、now、forced）、`shutdown_reason`、`shutdown_attempts`、`last_shutdown_at`、`last_shutdown_reason`、`dry_run_fired`。off-now 的准备 `prep`。跑完就关：`off_when_done`、`off_when_done_reason`。守护：`heartbeat`、`daemon_pid`、`daemon_pstart`、`daemon_version`、`guard_sty` 与锁文件。随开机自启：`boot.gpu`、`boot.nogpu`（存给下次开机的设置）、`autostart`（`BOOT RESULT`，boot 的结果）
- `jobs/<任务名>/` 下有 cmd、env.sh、tag、owner、req.pending 或 req、start、boot、launch、pid、pstart、spawning、pgid、running、end、rc、logpath，有安静期的另有 quiet；日志默认是同目录的 log；死在启动命令之前的 runner 留下的 pid 改名为 pid.dead-<时间>。同名任务再次 run 时，旧目录改名为 `<任务名>.prev-<时间>-<进程号>` 保留
- `guard.log` 记每次决定与原因，行首是：`ARM`、`RUN`、`QUIET`、`KEEP`、`DEADLINE`、`OFF-WHEN-DONE`、`OFF-NOW ...`、`SHUTDOWN attempt=...`、`DRY_RUN`、`PENDING SHUTDOWN CANCELLED`、`CHECK SKIPPED`、`STATE CORRUPT ...`、`STATE OF ANOTHER VERSION ...`、`DAEMON ...`、`JOB END`、`BOOT ...`、`AUTOSTART ...`。用 `logtail guard`（ctl 的 `tail <别名> guard`）看
- status 一行一个 `key=value`：`version`、`now`、`up`、`mode`、`mode_now`、`boot`、`schema`、`needs_rearm`、`armed_at`、`armed_this_boot`、`armed_by`、`autostart`、`boot_settings`、`autostart_this_boot`、`arm_incomplete`、各项设置、`no_reliable_signal`、`sig.gpu` 到 `sig.net`（`STATE:VALUE`，来自最后一次检查，STATE 为 busy、idle、unknown、na、off）、`deadline_at`、`deadline_in_s`（没设为空）、`past_deadline`、`keep_until_at`、`keep_in_s`、`last_active_at`、`idle_for_s`、`shutdown_in_s`（按当时适用的 grace 或 `--idle` 算的剩余秒数，在用、有 keep 或安静期、或不会关时为空）、`active_why`、`gated`（prep 或空）、`off_when_done`、`shutdown_*`、`dry_run`、`dry_run_fired`、`heartbeat`、`daemon_alive`、`daemon_version`、`last_shutdown_*`、`state_corrupt_logged`，以及每个任务一行 `job.NAME=STATE|START|END|LOG`，有安静期的另有 `quiet.NAME=剩余秒数`。`idle_for_s` 与 `shutdown_in_s` 按与检查相同的起算点算。status 只读：不改也不建任何文件（守护目录还不存在时只建目录）
- 在实例上看帮助 `bash /root/autodl-tmp/.autodl-guard/autodl_guard.sh help`

## 从 0.7 升级
- 不做实时迁移。0.7 从未发布、没有随开机自启，新开机时不会有 0.7 守护在跑：直接部署 0.8 再 arm。0.8 的状态在 `state2/`，0.7 的 `state/` 原样留着，0.8 不读它
- 同一次开机里 0.7 的守护锁（`state/.daemon.lock`）被占用时，0.8 的 arm 与 revive 都拒绝，因为两个守护会各按各的规则关机，提示在新开机时升级；任何途径都不在它旁边启动 0.8 守护（run、keep 等拉守护时，off-now 拉守护时，守护自己启动时都查）。只看锁是否被占用，不看 `daemon_pid` 这类元数据；锁文件不存在或没被占用（上一次开机的残留）就照常继续，也不在 0.7 的目录里新建文件；锁试不了时同样拒绝
- 保证的范围：只拒绝检查那一刻已经拿着 0.7 守护锁的 0.7 守护，一个正在启动、还没拿到锁的不在其内，所以升级期间不能同时运行任何 0.7 的命令

## 与 0.7 不同的行为
- 空闲按四类信号判断；登记的任务、screen 与 tmux 会话本身不再算在用，0.7 的 `--util-signal`、`busy_now`、`util_*` 都去掉了
- 最晚关机不再切断在用的任务：到点后只要不在用满 grace 就关，在用就一直等
- arm 的 `--idle` 必填，`--deadline`、`--keep` 可选；`keep --after-job` 去掉（0.8 里任务结束后本来就按空闲时长倒数，想多留就 `keep DUR`）；新增安静期（`run --quiet`、`quiet`）
- 时长按开机时长计；状态目录换成 `state2/`
- 守护启动先写心跳，第一次检查在一个间隔之后（有待重试的关机或已过最晚关机时当场检查）
- off-now 按事务处理，有闸门与当场采样；新退出码 7 与 8
- 新增随开机自启（`install-autostart`、`uninstall-autostart`、`boot`），arm 按模式存下给下次开机用的设置
- status 的键换了（见上），0.7 的 `deadline`、`keep_until`、`last_busy`、`busy_now`、`post_job_keep_s`、`util_*` 都去掉了

## ctl 与 SSH（ctl v0.8）

### 认准实例
- 别名只说明怎么连，它连到哪台实例会变（`~/.ssh/config` 被改过，实例重建后换了端口而别名没改）。所以除 `check`、`wait`、`doctor` 外，每条带别名的命令都认定一台实例：命令行给了 `--instance <ID>` 就是它，否则取本机记录里这个别名核实过的对应（`check 别名 --instance ID` 记下的）
- 发往实例的每条远端命令，在开始标记之后先比主机名，不是 `autodl-container-<ID>` 就什么都不做，以退出码 113 与一行标记结束。ctl 认出后退出 13，输出里 `instance_match` 为 false、`hostname` 是别名实际连到的那台（读不出主机名时为 null），后面的步骤（重发、等关机、别的远端命令）都不做。核对与命令在同一次远端 shell 里，中间没有空档
- 别名没核实过、又没给 `--instance`：不连接，退出 13，输出里没有 `instance_match`，`error` 说先 `check 别名 --instance ID`。照做就行，对上之后把原来的命令重发，这一种不用停下来问用户。本机记录不能用、又没给 `--instance`：退出 11；这时给命令加上 `--instance <ID>`，就不需要本机记录来认实例
- 退出 13 而 `instance_match` 为 false 的（别名连到了别的机器，`check` 自己比对不符也是这样）：立即停下，告诉用户，不换别的别名试，也不绕开 ctl 直接用 ssh。别名弄对之后 `check 别名 --instance ID`，再接着做
- `wait` 不核对（它排在核对之前，等关机时实例本来就连不上）：别名指向了别的实例时，它报的是那一台的模式，紧接着的 `check` 会拦住

### 连接与重发
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
  - 再做一次 5 秒的当场采样，就是守护的 `idle-check`（见"off-now"一节末尾）。守护脚本由 ctl 从本机经标准输入送过去运行，实例上有没有守护脚本、是什么版本都一样。有在用或读不到的信号就拒绝，输出里逐行列出，如 `cpu:busy`；只有采样自己答了 `idle: ...` 才往下关机，脚本没送到或不完整同样拒绝
  - 第二道是为会话与登记都看不到的工作加的：在 Jupyter 里起的、直接用 ssh 起的计算。它只看这 5 秒，这几秒里恰好没有明显活动的工作（在等待的脚本、交互式的操作）照样会被关掉，同守护按空闲关机是一个口径；采样结束之后、关机之前才起的工作也拦不住（off-now 与守护在状态不能用时重试关机之前的采样同样如此）
- off-raw 查 screen 的细节。screen 只认活着的会话，`screen -ls` 里行尾为 `(Dead ???)` 的套接字（上一次开机留下的）不算在用；列表要完整才信（计数行与会话行数相符，退出码 0 或 1，4.6.2 以后列出会话后退出 0、4.2.0 退出 1），`No Sockets found` 要退出 1，别的一律按说不清拒绝。`(Remote or dead)` 按在用算，核实后用 `--force`
- push 在上传开始之后超时的，报不确定（退出码 6），因为可能已经放到位。push 先在实例上目标的父目录里建临时目录解包，成功后才移到位；被替换的旧目标改名为 `.bak-<时间>-<进程号>`，脚本中途退出（含信号）时 EXIT trap 把它放回并清掉临时目录。pull 先解到本地目标旁边的临时目录，ssh 与 tar 都成功退出后才放到位，旧目标改名为 `.bak-<时间>-<随机串>`，移动失败或被 Ctrl+C 打断时放回。SIGKILL 或断电时可能留下 `.bak-*` 或临时目录
- push 打包时给每一项定权限（实例上的 tar 以 root 解包、按档案原样保留）：目录 755，普通文件 644，本机是 POSIX 而且文件有执行位时 755（Windows 上的执行位随扩展名，不算）；属主与组名清空、编号为 0
- 发往实例的值（push 的父目录、pull 的路径、run 的 --cmd 与 --log、arm 的 --env-setup）以盘符开头时 ctl 拒绝执行，那是 Git Bash 改写了参数的迹象，改用 scripts/ctl 启动器

### 命令一览
- `version` 打印 ctl 的版本（0.8.0）；`now` 打印此刻的 unix 秒（取 T0 用）；`doctor [别名]` 见下文
- 带别名的命令除 `check`、`wait`、`doctor` 外都可以加 `--instance <ID>`（见"认准实例"），下面不逐条重复
- `wait 别名 [--state up|down] [--mode gpu|nogpu] [--timeout 10m] [--every 10]`：`up` 时每隔 `--every` 秒探一次，连上后识别模式，模式识别不出或与 `--mode` 不符退出 1；`down` 时连续 5 次探测都失败才算连不上（`confirmed` 仍是 false）；到 `--timeout` 还没等到退出 1
- `tail 别名 任务名 [-n 行数]` 看任务日志的末尾，任务名写 `guard` 是守护自己的日志；`revive 别名 [--restart]` 把守护进程拉起来、不改任何设置；`push 别名 本地路径 实例上的父目录 [--overwrite] [--timeout 60m]` 与 `pull 别名 实例上的路径 本地目录 [--overwrite] [--timeout 60m]` 传文件，细节在"连接与重发"
- `arm 别名 --idle DUR` 之外只发给了的项（`--deadline`、`--keep`、`--grace`、`--interval`、`--mode auto|gpu|nogpu`、`--gpu-probes`、`--thr-gpu`、`--thr-cpu`、`--thr-io`、`--thr-net`、`--unreliable`、`--calib` 与 `--calib-coverage`、`--env-setup`、`--dry-run`、`--rearm`），取值由守护校验，时长先在本机校验；没有手工给阈值、不可靠信号与 `--calib`，也没给 `--interval` 与 `--gpu-probes` 时先查校准（见下文），输出的第一行 `calibration: ...` 说明用了哪条（有不可靠信号时还写出关掉了哪些）或为什么用默认阈值
- `run 别名 名字 (--cmd 命令 | --cmd-file 文件) [--then-off] [--quiet DUR] [--log 路径]`；`quiet 别名 名字 DUR --reason 理由` 让在跑的任务从现在起算在用 DUR；`keep 别名 DUR --reason 理由`（没有 `--after-job`）；`deadline 别名 DUR`；`off-when-done`；`off-now 别名 --reason 理由 [--force] [--sample 秒] [--wait DUR]`；`off-raw`
- `deploy 别名 [--no-autostart]` 校验和对上后调用守护的 `install-autostart`，输出 `deployed`（守护脚本已就位时为 true）、`path`、`sha256`、`autostart`（installed、already，或 failed、not sent、uncertain 加原话）；装不上退出 1，这条 SSH 没送到退出 2、说不清退出 6，`--no-autostart` 跳过这一步。钩子是新装上的，就清掉这台实例的校准，结果写在 `calibration_forget`（清不掉时 deploy 退出 1，而 `autostart` 是 installed：自启装好了，只是旧校准还在，本机记录修好后 `calibrate 别名 --forget`）。所以 `deployed` 为 true 而退出非 0 时，要看 `autostart` 与 `calibration_forget` 分清是哪一项没成。输出里的 `note` 是一句固定的提醒，每次都有：换了守护脚本之后，正在跑的旧守护进程要到它下一次启动才换成新的（你的下一次 arm 或 `revive --restart` 会重启它，再就是下次开机）；`status` 的 `daemon_version` 与 `version` 相同就不用理会。`autostart 别名 install|uninstall` 单独装卸，退出码照守护
- `status 别名` 把守护给的键（包括 `deadline_in_s`、`keep_in_s`）原样收进结果，另算 `heartbeat_age_s` 与 `booted_at`。`booted_at` 是这次开机容器启动的时刻（unix 秒，按本机的时钟，也就是 T0 与账本用的那个钟）：本机收到回答的时刻，减去容器已经开了多久。后者由守护给的 `up`（内核开机时长）减去 `boot`（1 号进程的启动时刻，单位是时钟滴答）除以实例的 `getconf CLK_TCK` 得出，是一段时长，实例的时钟准不准都不影响；算不出时为 null；接手一台已经开着的实例时用它记账。守护还没部署时 status 退出 1、`guard` 为 `not deployed or failed`，先 deploy；`armed_by=boot` 时加一条 note，说这次开机是自启 arm 的、没有 env_setup，跑任务前先 arm（AI 的 arm 直接替换它）。status 每次成功都把守护报的 `autostart` 与 `boot_settings` 记进本机记录；`arm` 与 `autostart` 成功、在实例上出了错或说不清（退出 0、1、6）之后，ctl 也自己再读一次记下（记不进去只在 stderr 提一句，不影响命令），`auth show` 的 `guard_at_boot` 用的就是它。这一次读不到，或者 status 得到了实例的回答而守护答不出（没部署，脚本不在了），原来记的那条就去掉：宁可下一次开机多设一个临时定时，也不沿用可能已经不成立的旧回答
- `check 别名 [--instance ID] [--config]` 报用的是哪个 ssh、连不连得上。带 `--instance` 时另读主机名，与 `autodl-container-<ID>` 比较，结果写在 `instance_match`；一致时把别名与实例 ID 记进本机记录（这是别名被认作这台实例的唯一途径），不一致退出 13，读不到主机名按 ssh 的结果退出 2 或 6，记不进本机记录退出 11。`--config` 另打印 ssh 把这个别名解析成的主机、端口、用户与密钥文件路径，只在排查连不上时用
- `auth ...`、`log ...`、`usage ...`、`calibrate ...` 见下文

### 别名的写法与第一次连接
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

### 守着任务到关机
- skill 没有专门等任务结束的命令。任务在跑时，隔一段时间 `ctl status <别名>` 看它的那一行，`job.<任务名>=` 后面的状态从 `running` 变成 `done:<退出码>` 就是结束了，几分钟查一次就够。启动时加了 `--then-off` 的，也可以用 `ctl wait <别名> --state down --timeout <时长>` 等实例关掉
- 你所在的环境能把等待放到后台、到时候再叫醒你的（后台命令、定时唤醒之类），就这样等，不必为了等而结束对话。任务一结束接着做后面的事：取回结果、关机并收尾，或接着跑下一个
- 要取回的结果在关机之前 `ctl pull`，关机后就连不上了；数据仍在数据盘上，下次开机再取也行（可以用无卡）。等的时间会超过上一次预算检查算到的时刻时，先 probe（SKILL.md 的"用量与预算"）
- 你的对话没了，任务照常跑，守护照常按空闲关机。这是兜底：能守着就守着，守护的设置照样要配

### 退出码
- 0 成功；1 出错（包括用法错误，argparse 原本的 2 会被读成"没送到、可以重发"）；2 命令没送到；3 拒绝，还在用；4 拒绝，有待重试的关机；5 拒绝，这次开机已经 arm 过；6 说不清是否执行了，先查 status 再决定要不要重发；7 已过最晚关机；8 off-now 正在准备；10 预算不够，或快用完而这个周期还没有用户的同意；11 本机记录不能用（读不出、格式不对、权限或链接不安全、时钟回拨），一律不自动修；12 没有授权，或授权的用法里没有这种模式；13 别名没有核实过，或它连到的不是这台实例（带别名的命令都会这样退出，见"认准实例"；`check --instance` 比对不符也是它）。守护的其他非零退出码报 1

### 本机记录
- 位置是 `~/.autodl-gpu`（Windows 上在用户目录下），环境变量 `AUTODL_GPU_HOME` 可以另指，但必须是绝对路径（相对路径会随工作目录变，拒绝；Windows 上还要带盘符或 UNC 共享，如 `C:/Users/NAME/.autodl-gpu` 或 `/c/Users/NAME/.autodl-gpu`，只以 `/` 或 `\` 开头的路径跟着当前盘走，同样拒绝）。解析成真实路径后，它自己或任何上级目录里有 `.git`（目录，或工作树里的文件）就拒绝，判断不了（上级目录读不了）也拒绝，退出 11，报错里建议另指一个不在任何仓库里的目录
- 一个数据文件 `store.json`，一个锁文件 `store.lock`。第一次建立时先在上级目录里建一个私有的临时目录，放好锁文件再改名成正式目录；两个进程同时第一次打开，后改名的那个用先到的目录。建到一半被杀时，上级目录里会留下一个名为 `.<目录名>.new-<随机串>` 的临时目录（默认位置时是 `..autodl-gpu.new-*`，不自动删），可以手工删掉
- 目录里既没有 `store.lock` 也没有 `store.json`（多半是手工建的）时，报错说可以删掉这个目录让 ctl 重新建；只缺 `store.lock` 时照样不自动补
- 每次打开都查，不符就退出 11 并给出改法，从不自动改：目录与两个文件都不能是符号链接或目录联接；POSIX 上属主是自己、目录 700、文件 600（给出 chmod 命令；chmod 改不动时是文件系统不保存权限，例如 WSL 的 `/mnt/c`，要把 `AUTODL_GPU_HOME` 指到保存权限的文件系统）；Windows 上用 SDDL 读所有者与 DACL，所有者是自己或 Administrators，每条允许项（包括只作继承的）只能给当前用户、SYSTEM、Administrators，拒绝项不管（给出 icacls 命令）。第一次建立时用 icacls 以 SID 设一个不继承上级的 DACL（Python 3.13 的 `mkdir(0o700)` 在 Windows 上给的是"所有者权限"而不是用户的 SID，所以不用它）
- 读写都在排他锁里（POSIX 用 fcntl，Windows 用 msvcrt 锁第一个字节），最多等 30 秒；写入先写同目录里私有的临时文件、flush、fsync，再 os.replace 换上，换上之前出错旧文件原样留着。被杀的写入者留下的临时文件，下次打开时在锁内删掉
- 读时严格校验：不是 JSON、有重复的键、有 NaN 或无穷、schema 不是 1、多了或少了哪一部分、授权与账本与校准的字段不合格（取值整串匹配，结尾带换行的也不认；校准的 CPU 与 GPU 阈值至多 100，预留的时间窗至多 30 天）、同一编号两次、同一实例两个开着的会话，都算不能用，退出 11，文件原样不动。文件不存在当作空记录（把坏文件挪开就从空记录开始，授权也没了，要重新 grant）
- 写入之前按读时的同一套规则校验整个记录，不合格就什么都不写，按本机记录不能用处理（退出 11；`log on|off` 照样写项目日志），所以任何命令都写不出下次读不了的记录。输入都事先查过，只有程序的错误才会走到这一步
- `last_seen` 是 ctl 见过的最晚时刻，每次打开时在同一把锁里更新为较大的那个，时钟回拨时不降低
- 记录里另有一部分 `guards`（早先建的记录里没有，照样能读）：每台实例最近一次守护自报的 `autostart` 与 `boot_settings`，由 `status` 与 `arm`、`autostart` 之后的那次读取写入，读不到时去掉（见"命令一览"的 status 一条）。`auth show` 的 `guard_at_boot` 据此列出下次开机会由自启的钩子真的 arm 起来的模式：钩子是 installed，这种模式在 `boot_settings` 里有一项，而且不是 dry-run。没有记过、或记的不满足这几条，就是空的
- 本机记录不能用时：`auth check` 退出 11、不开机；带别名的命令也退出 11，给它加上 `--instance <ID>` 就照常做（arm 这时用默认阈值）；`log on|off` 照样写项目日志、再退出 11；`check --instance` 照样报比对结果、再退出 11

### 授权、开机前的关口与账本
- `auth grant --instance ID --alias 别名 --usage both|gpu|nogpu --budget none|<元>yuan|<小时>gpuh --period month|none [--period-tz +08:00] --quote '<用户原话>'` 写入或替换这台实例的授权；重新 grant 清掉已有的"快用完"同意，账本不动；第一次 grant 时建一本空账本，撤销之后照样认得这台实例。输出里的 note 按预算的种类说明它从哪里算起、还要做什么（下一条）。`--alias` 只作显示，别名要靠 `check --instance` 核实
- 预算从哪里算起，三种各不相同
  - 按月的金额预算（`<元>yuan`、`--period month`）管的是这台实例本月的全部扣费，不只是 AI 开的那些。每次 grant 之后、以及每个月的头一次检查之前，要把收支明细里这台实例本月已有的扣费导入一次（手册第 12 节），一笔都没有就导入空列表 `'[]'`。授权里记着最近一次导入的时刻（`charges_read`），新的 grant 不带它；没有它、或它落在更早的月份时，`auth check`（含 `--probe`）退出 1、说明先导入，`auth show` 里也有一项 `baseline` 写着还欠这一步
  - 不分周期的预算（`--period none`）是"从现在起总共多少"，从授权那一刻所在的整点起算（授权里的 `since`），之前的会话与扣费不算，也不用先导入。之后重新 grant 改数额是改总额，沿用原来的起点，已经花掉的照算；要从头重新算，先 `auth revoke` 再 grant（原先没有预算、单位从金额换成 GPU 小时或反过来、从按月换过来，也都从这次授权起算）。早先的版本没有记起点，那时授权的不分周期预算把账本里有过的全算上；这样的授权只改数额时照旧全算（`since` 记为 0，`note` 里写着），要从现在起算同样先 revoke 再 grant
  - GPU 小时预算（`<小时>gpuh`）只算本机账本里记下的开机：这台电脑记的，加接手时补记的。扣费行补不出 GPU 小时，所以授权之前、别的电脑上、用户手动开而没被接手的用量都不在内。授权时用一句话告诉用户；用户要把之前的算进去，就按真实的时刻补记一段，`log on --instance ID --at <开始> --field mode=gpu --field price=<单价> --field gpus=<卡数> --field time=estimated`，再 `log off --instance ID --at <结束>`，或者把预算改成还想给的数。之前开过几次就逐次补，补完一段（`log off`）再补下一段。每一段的时刻从收支明细取：结束是那次开机最后一笔扣费的时刻，时长按那次开机的扣费合计除以单价估，开始是结束减去时长（同下面 `--void` 一条的估法）；单价照接手时的取法（手册第 17 节：跨过两个整点的那次开机，第二笔整点扣费的金额就是单价，没有就问用户）；明细里分不清各次开机时，请用户说
- `auth show [--instance ID] [--booted-at T]` 打印授权、本周期的已用、未了结的预留（`open_reservations`）、开着的会话（`open_session`：会话号、开始时刻、模式、卡数、单价，没有为 null）与余量，还有 `guard_at_boot`（下次开机会由自启的钩子 arm 起来的模式，见"本机记录"；要开的模式不在里面，开机前就要先有一个控制台定时关机，开机流程第 2 步）与 `baseline`（只在按月的金额预算还没导入过扣费时出现）。带 `--booted-at`（`status` 的 `booted_at`，要同时给 `--instance`）时另答 `this_boot`：`recorded` 是开着的会话算这次开机的（它的开始不早于启动之前 120 秒），`not recorded` 是没有开着的会话，`an earlier session is still open` 是开着的会话开始得更早。这个判断只看时刻，两边都可能判错：`recorded` 时要再比对模式与卡数，`an earlier session is still open` 时要靠收支明细分清它是更早的一次开机（关机没记上）还是就是这一次（T0 记得早），办法在手册第 17 节。`auth clock --quote '<用户原话>'` 见下面说本机时钟的那一条；`auth revoke --instance ID` 删掉授权，账本留着、照常记账；`auth approve --instance ID [--period YYYY-MM|all] --quote '<原话>'` 记下用户对某个周期"快用完"的同意。同意按周期分别存（授权里的 `approvals`），不写 `--period` 时为当前周期，只接受当前周期和从现在起一次最长时间窗（30 天加 10 分钟）碰得到的周期。同意一个周期，就是这个周期里之后的开机都不再因 20% 拒绝（超出预算照样拒绝），问用户时要说清同意的是整个周期，不只是这一次开机
- 开机的关口：打开确认框、先不点确定，读出这次的模式、单价与卡数（卡数在实例行的规格里），运行 `auth check --instance ID --mode gpu|nogpu --price <元/时> --gpus <卡数，无卡为 0> --hours <计划的小时数>`。退出 0 才用 `ctl now` 记下 T0 并点确定，1、10、11、12 都点取消。开机成功后用同一个请求号 `log on --req <请求号> --at <T0>`，重发时带同一个 `--at`；没开成或取消了就 `auth release --instance ID --req <请求号>`
- `auth check` 的判定：没有授权或用法里没有这种模式退出 12；本机时钟比 `last_seen` 早，而且两者按授权的时区落在不同周期、或早 10 分钟以上，退出 11（怎么办见下一条）；按月的金额预算在这次 grant 之后、这个月里还没导入过扣费，退出 1，什么都不判断（导入之后再检查，开机时先 `dismiss`、导入完从头再开）；否则对这次时间窗（现在到结束后 10 分钟）碰到的每个周期算"已用加上这次的需要"，超出预算退出 10、不预留，余量不到预算的 20% 而这个周期还没有同意时同样退出 10（先问用户，用户同意后 `auth approve --period <拒绝里写的周期>`；时间窗跨两个周期时可能要分别同意）；都过了就在同一把锁里记一条预留，输出请求号、每个周期的已用与余量，以及按这个单价还能开多少小时。`--hours` 只是计划的窗口，关口不是硬上限；守护的最晚关机到点后不再接受新的工作、干完就关，但不切断在用的任务，绝对的上限只有控制台的定时关机。加 `--probe` 时做同样的判定、给同样的退出码与各周期的数，但不记预留、输出里没有请求号（有 `probe: true`）：用在实例已经开着的时候。开机时的检查（或上一次 probe）只算到它的 `--hours` 为止，凡是会让实例开得比那更久的事，接手、再 `run` 一个任务、声明安静期、keep、推后最晚关机，之前都 probe 一次，`--hours` 给从现在到新的预计结束时刻的时长；还在已检查的时间窗里时它不会拒绝开机时放行的东西。这几样是例子，长时间的 `push`、`pull` 也一样；拿不准上一次检查算到什么时候（上下文断过，或换了对话），就 probe 一次。输出里的 `open_session` 是它算进去的那段开着的会话，应当就是这次开机的那一段。账本里没有开着的会话时 probe 拒绝（退出 1，`open_session` 为 null）：判断里会缺这次开机已经用掉的部分，先记上这次开机（`status` 取 `booted_at`，`log on --booted-at`，手册第 17 节）再 probe。一台实例同一时间只有一段会话，开着时没有别的开机来分它的预算，所以这时不需要预留
- 退出 11、说本机时钟比记录见过的最晚时刻早时，先分清是哪个钟不对。把 `ctl now` 的输出同你自己读得到的别的钟比：控制台定时关机对话框里的服务器时间（手册第 8 节的 `readTimer`，读完 `dismiss`；它是北京时间，见那一节第 3 步），实例开着时 `ctl status` 的 `now`；收支明细里最近一笔扣费的交易时间只是下限，本机时钟比它还早就是慢了。本机时钟不对，请用户改时钟。本机时钟是对的（之前某次运行时时钟超前），与用户确认后 `auth clock --quote '<用户原话>'`，它在本机记录自己的锁里把 `last_seen` 降到现在的时刻，不要手工改 `store.json`
  - 它拒绝（退出 1，`lowered` 为 false）是因为账本里有一笔导入的扣费比现在晚 5 分钟以上。扣费的时刻是平台给的，导入时也不可能比当时的本机时钟晚这么多，所以本机时钟现在确实慢了，用户怎么说都不降：改时钟。关机时刻不作这种证据，它可能是本机时钟写的
  - 降下来之后，输出里的 `dated_ahead` 列出时钟超前时写下、现在还算数的记录：开着的会话（`kind` 为 on）与没了结的预留（reserve）。这样一段会话要到它的时刻才开始计数，也没法按真实时刻关掉，还会挡住之后的 `log on`。告诉用户，用户同意后用 `log off --instance ID --void --quote '<原话>'` 关掉它，再导入扣费，之后照下面 `--void` 一条把真实的用量补回账上；这样的预留用 `auth release` 放掉。时钟超前时写下、已经关掉的会话不在 `dated_ahead` 里：它们算在各自日期所在的周期，跨了月界时 GPU 小时会记到下一个月（金额有扣费校正），没有命令去改
- 已用怎么算：会话在北京时间的整点处切段（还开着的会话算到现在，所以没记上的关机要先补，否则预算一直多算），每段按秒数乘单价除以 3600、向上取整到分、至少 1 分，关机时刻正好是整点时再多 1 分（可能还有一笔关机扣费；整点正好是周期起点时算在新周期）；取这个估算与"本周期导入的扣费合计，加上本周期最后一笔扣费之后的估算"中较大的；没被开机用掉、也没释放的预留（过期的也算）在它的时间窗碰到的每个周期都全额计入。GPU 小时预算一律按整数 GPU 秒算（有卡的秒数乘卡数），比较时用"已用 GPU 秒 × 5 ≤ 预算千分之一小时 × 18"。月界按授权的 `--period-tz`（默认 +08:00，AutoDL 账单的时区）算，与本机时区无关；不分周期的预算只有一个周期（名字是 `all`），从授权里的 `since` 起算，更早开始的小时段与更早的扣费不在内
- `log on|off --instance <实例 ID 或核实过的别名> ...` 先在锁内记账本，再追加项目日志；项目日志写不进去退出 1（账本已记，预算不会少算）。`log on` 必须带 `--field mode=gpu|nogpu`、`--field price=<元/时>`，有卡还要 `--field gpus=<卡数>`（无卡为 0 或不写），缺了就什么都不写、退出 1。`--at` 要晚于 1970 年、至多比现在晚 5 分钟（与扣费一致），不给时取现在；`--field` 不能用 t、event、instance、req 这几个键（项目日志自己的）；不合格都什么都不写、退出 1
- 会话号：带 `--req` 且模式、单价、卡数都与那条预留一致时，就是这个请求号，预留算用掉；对不上时（或预留已经没了）会话号为 `m<请求号>`、记下 `attempted_req`，那条预留原样照算，退出 1，请核对后 `auth release`。不带 `--req` 的开机为 `t<开机时刻>-<序号>`，标 unreserved，照记照算
- 重发只记一次：带 `--req` 的按请求号认（时刻、模式、单价、卡数都要与第一次相同，所以要带同一个 `--at`，否则拒绝、退出 1），不带的按"当前开着的那段、时刻、模式、单价、卡数都相同"认（同一秒先开后关配平之后同样的值再开，是新的一段）；项目日志里已有一模一样的一行时也不再追加。这台实例已有开着的会话时 `log on` 拒绝、什么都不写，拒绝里写着那段会话的开始时刻，并说明是哪一种：早于你的 T0 就先按收支明细补记上一次的关机，不早于 T0 是这次开机已被接手的对话记上（手册第 10 节）；关机时刻早于开机也拒绝（见下面的 `--void`）。接手已经开着的实例用 `log on --instance ID --booted-at T --field ...`（T 是 `status` 的 `booted_at`，不能同时给 `--req` 或 `--at`）：没有开着的会话就从 T 起记一段不带预留的会话；开着的会话算这次开机的（开始不早于 T 之前 120 秒），回答 `already`、什么都不加，项目日志也不写，这时不需要给 `--field`；给了 `--field` 而卡数与那段会话的不同（有卡与无卡不同也在内，单价不同不算）时拒绝，说明两种可能与分辨的办法；开着的会话更早，拒绝并说明怎么分清它是更早的一次开机还是这一次。不带 `--field` 而本机记录不能用时，判断不了记没记过，同样什么都不写、退出 1
- `log off` 关这台实例唯一开着的会话（也可用 `--req` 指明）；没有开着的会话时只写项目日志并说明。认不出实例（不是实例 ID，也不是核实过的别名）时只写项目日志，并提示用 `check 别名 --instance ID` 核实一次
- `log off --instance ID --void --quote '<用户原话>'`（不能带 `--at`）按开着的会话自己的开始时刻关掉它，这一段的时长记为零，项目日志里标 `time=void` 并记下用户的话。只用于没有哪个时刻能关掉的会话：最后一笔扣费比它的开始还早（`log off --at` 因此被拒绝），或它的开始比本机时钟还晚（时钟超前时写的）。用之前告诉用户、得到同意（`--quote` 必填），用之后导入扣费，钱以扣费为准。没有开着的会话、认不出实例或本机记录不能用时什么都不写（前两种退出 1，后一种退出 11）
  - 关掉之后这一段在账上是零。那台实例还开着时（时钟是在开机期间改对的），这次开机就不在账上了，照接手的办法重新记：`ctl status` 取 `booted_at`，再 `log on --instance ID --booted-at <booted_at> --field ...`（原来的请求号不能再用来重发）
  - 那次开机已经结束、而预算按 GPU 小时算时，扣费补不回 GPU 小时。按真实的时刻补记一段：`log on --instance ID --at <关机那一笔的时刻减去这次开机的时长> --field ... --field time=estimated`，再 `log off --instance ID --at <关机那一笔的时刻>`；开机的时长按这次开机的扣费合计除以单价估
- `auth charges --instance ID (--json '<JSON 列表>' | --file 路径)` 导入控制台收支明细里这台实例的扣费行，每行恰好有 `serial`、`instance`（要与 `--instance` 一致）、`time`（不带时区的按 UTC+8）、`amount`（正数，至多两位小数）；任何一行不合格、时刻晚于现在 5 分钟以上或早于 1970 年、同一流水号内容不同，整批拒绝、一行都不写；同一流水号内容相同算重发。空列表 `'[]'` 也收，意思是读过收支明细、没有要导入的行。有授权时每次成功都记下导入的时刻（输出里的 `charges_read`），按月的金额预算靠它放行。账本里没有这台实例（没 grant 过、也没记过开机）时拒绝，防止写错 ID
- `auth release --instance ID --req <请求号>` 释放没用上的预留；已经被开机用掉的预留拒绝释放，要关机后 `log off`
- `usage --instance <ID 或核实过的别名>` 读账本，汇总这台实例在所有项目里的有卡 GPU 小时（乘卡数）、无卡小时、估算费用（不低于已导入的扣费）与已导入的扣费，并列出开着的会话（`open_session`）；不给 `--instance` 时照旧读项目日志，记录按时刻排序后配对（同一时刻按写入先后），开着时又开机的那段算到这次开机为止、列在 `closed_by_next_on`。项目日志只有在本项目里发出的 `log on|off`：别的项目的对话接手并关了这台实例时，本项目那一段没有关机记录，会一直算到现在（`includes_running_time`），确认实例已关机、`auth show` 里没有开着的会话后，在本项目里补一条 `log off --at <那次关机的时刻>`（账本答没有开着的会话，只写项目日志）；接手别的项目开的机，本项目的日志里没有这次开机，这一段不算在本项目的累计里

### 校准
- `calibrate 别名 [--minutes N]`（N 默认 5，2 到 30）：别名要先经 `check 别名 --instance ID` 核实；有登记在跑的任务就拒绝（退出 3），校准期间也不要用这台实例。用守护的只读 `sample --every 60s --count N`（有卡带 3 次 GPU 探测，无卡为 0），这条 SSH 最多等 N 分钟加 2 分钟；只收完整的一次（表头的 every、count、gpu_samples 与请求一致，列与行数都对，计数不回退，uptime 递增），采样前后读的 status 里任务记录（名字、状态、开始、结束）、`armed_at`、`deadline_at`、`keep_until_at`、`off_when_done` 有一样变了就不存，报错里写出变了的是哪一项。`last_active_at` 不比：守护每次检查发现信号超过当前阈值都会更新它，而底噪超过当前阈值正是要校准的情况
- 采样之前先取指纹（见下文），这条 SSH 没送到退出 2、说不清退出 6、其余失败退出 1；指纹探测的最后一段是 `uname -n`，别名已经指向别的实例（主机名不是 `autodl-container-<核实时的 ID>`）时不采样、退出 13，要重新 `check 别名 --instance ID`；主机名读不出来时判断不了，同样不采样、退出 1
- arm 的空闲时长短于校准时长时，守护可能在采样中途按空闲关机：先 `keep` 覆盖校准的时长再校准（keep 在校准开始前设好，采样期间不变）
- 各间隔的 CPU（占单核的百分比）、磁盘与网络（每秒字节数）按相邻两行 uptime 的实差算，不假定正好 60 秒；GPU 读数只用那个间隔所有探测都成功的
- 阈值：每个信号取守护默认值（GPU 5%、CPU 有卡 5.0% 无卡 3.0%、磁盘 500000 B/s、网络 10000 B/s）与"空闲最高读数的 1.5 倍"中较大的，CPU 保留一位小数、其余取整，都向上取，CPU 与 GPU 封顶 100（守护的取值范围）。阈值达到在一台测试实例上量到的最轻工作（60 秒窗口：有卡 CPU 8.523%、无卡 CPU 6.693%、磁盘 2.665e6 B/s、网络 9.024e5 B/s、GPU 99%）时，这个信号标为不可靠（它对应的工作靠安静期或 keep）；一个读数都没有的信号（GPU 探测全失败）也标为不可靠。这组"最轻工作"只是测试实例上的参考值
- 结果按实例、模式、环境指纹、守护脚本摘要（sha256 前 12 位）存进本机记录。环境指纹取自一条只读命令的带键名的几行：`/etc/os-release` 的 NAME 与 VERSION_ID、每张卡的型号与驱动（按行排序）、cgroup 的 `cpu.max` 与 `memory.max`，取 sha256 前 12 位；任何一项读不出（无卡时的 GPU 除外）或摘要、主机名为空，就不存也不查。镜像与常驻服务不进指纹（开机后头几十秒服务还在起）。探测里的 nvidia-smi 有 `timeout 10`（实例上没有 timeout 命令时照旧直接运行），超时后列不出卡，模式按内存上限判断，有卡的实例判为说不清而不是无卡
- arm 时用：没有手工给阈值、不可靠信号与 `--calib`，也没给 `--interval` 与 `--gpu-probes`（校准是按 60 秒一次、有卡 3 次 GPU 探测量的，别的节奏不适用）时，arm 之前多发一条只读 SSH，同时取模式、指纹、摘要与主机名，找同一实例、模式、指纹、摘要、30 天内的最新一条，带上 `--thr-*`、`--unreliable`、`--calib <标识> --calib-coverage unverified`（只量了空闲，工作一侧没有验证）；有不可靠信号时第一行写出关掉了哪些，把它告诉用户，只在这些信号上看得出的工作要用 quiet 或 keep。找不到、取指纹的 SSH 失败、别名没核实、别名已经指向别的实例或本机记录不能用，都照样用默认阈值 arm，并在第一行说明原因
- 换镜像会让系统盘上的自启钩子消失，下一次 deploy 的钩子就是"新装上"，这时清掉这台实例的全部校准；新镜像也可能自带同样的钩子（那样报"已安装"），所以知道换了镜像就 `calibrate 别名 --forget`

### doctor 与启动器
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

## 只读采样命令（0.7.1 起）
- `autodl_guard.sh sample [--every DUR] [--count N] [--gpu-samples K]` 先输出参数行（版本、间隔、次数、K、`getconf CLK_TCK`）与列名，都以 `#` 开头；再输出一行基线，之后每个间隔一行。每行是以制表符分隔的 9 列：epoch、uptime_cs（`/proc/uptime`，单位百分之一秒）、cpu_usec（cgroup `cpu.stat` 的 usage_usec）、io_bytes（cgroup `io.stat` 各设备读写字节之和）、net_bytes（`/proc/net/dev` 除 lo 外的收发字节之和）、gpu_max（这个间隔里 K 次 `nvidia-smi` 采样的最高利用率）、gpu_fail（失败或跳过的次数）、guard_ticks（守护进程及其已回收子进程的 CPU 时钟滴答，进程号与启动时刻都对得上才读）、self_ticks（采样命令自身的 CPU 时钟滴答）
- 每行的 GPU 两列覆盖它之前那个间隔，其余各列是这一行时刻的累计值；基线行的 GPU 两列为 `na`。K 为 0 时不调用 `nvidia-smi`，GPU 两列都是 `na`；一个间隔里有一次采样失败或被跳过、有一行不是 0 到 100 的整数、或回答的 GPU 数与这次运行的第一次不同，gpu_max 就为空
- 排程按 `/proc/uptime` 对准绝对时刻；每次 GPU 探测最多用间隔的 1/K（至少 1 秒，不超过 `AUTODL_PROBE_TIMEOUT`），到间隔末尾还没做的探测跳过，所以 `nvidia-smi` 慢或挂起不会让后面的行越推越晚；读不到 `/proc/uptime` 时拒绝运行。`--count 0` 只输出基线行
- 不拿锁、不写状态、不需要 arm，也不需要 flock；要做 GPU 采样（K 大于 0）时需要 timeout。某一项读不到或格式不对时那一列为空，与真正的 0 区分开
- 供实机校准与 `ctl calibrate` 使用
