# 守护脚本（autodl_guard.sh 0.8.0）

本文件写实例端的守护脚本 0.8.0 实际怎么做：什么时候关机、看哪些信号、各条命令的约束、随开机自启、状态与日志。本机助手 ctl 的命令、连接与别名见 `reference/ssh.md`，本机记录、授权与账本、校准见 `reference/ledger.md`，控制台上的操作见 `reference/console.md`。

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
- 这些默认阈值来自在一台实例上的初步校准，偏向判为在用；别的机器与镜像的底噪可能不同，用 `ctl calibrate` 校准（见 `reference/ledger.md` 的"校准"一节）。只动 GPU 的短促任务在每分钟 3 次探测下可能漏看，这类任务要声明安静期或 keep
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
- status 一行一个 `key=value`：`version`、`now`、`up`、`mode`、`mode_now`、`boot`、`schema`、`needs_rearm`、`armed_at`、`armed_this_boot`、`armed_by`、`autostart`、`boot_settings`、`autostart_this_boot`、`arm_incomplete`、各项设置、`no_reliable_signal`、`sig.gpu` 到 `sig.net`（`STATE:VALUE`，来自最后一次检查，STATE 为 busy、idle、unknown、na、off）、`deadline_at`、`deadline_in_s`（没设为空）、`past_deadline`、`keep_until_at`、`keep_in_s`、`last_active_at`、`idle_for_s`、`shutdown_in_s`（按当时适用的 grace 或 `--idle` 算的剩余秒数，在用、有 keep 或安静期、或不会关时为空）、`active_why`、`gated`（prep 或空）、`off_when_done`、`shutdown_*`、`dry_run`、`dry_run_fired`、`heartbeat`、`daemon_alive`、`daemon_version`、`last_shutdown_*`、`state_corrupt_logged`，以及每个任务一行 `job.NAME=STATE|START|END|LOG`，有安静期的另有 `quiet.NAME=剩余秒数`。`idle_for_s` 与 `shutdown_in_s` 按与检查相同的起算点算（keep 或安静期到期后，空闲从它的到期时刻算起，见"keep、最晚关机、跑完就关、安静期"）。status 只读：不改也不建任何文件（守护目录还不存在时只建目录）
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

## 只读采样命令（0.7.1 起）
- `autodl_guard.sh sample [--every DUR] [--count N] [--gpu-samples K]` 先输出参数行（版本、间隔、次数、K、`getconf CLK_TCK`）与列名，都以 `#` 开头；再输出一行基线，之后每个间隔一行。每行是以制表符分隔的 9 列：epoch、uptime_cs（`/proc/uptime`，单位百分之一秒）、cpu_usec（cgroup `cpu.stat` 的 usage_usec）、io_bytes（cgroup `io.stat` 各设备读写字节之和）、net_bytes（`/proc/net/dev` 除 lo 外的收发字节之和）、gpu_max（这个间隔里 K 次 `nvidia-smi` 采样的最高利用率）、gpu_fail（失败或跳过的次数）、guard_ticks（守护进程及其已回收子进程的 CPU 时钟滴答，进程号与启动时刻都对得上才读）、self_ticks（采样命令自身的 CPU 时钟滴答）
- 每行的 GPU 两列覆盖它之前那个间隔，其余各列是这一行时刻的累计值；基线行的 GPU 两列为 `na`。K 为 0 时不调用 `nvidia-smi`，GPU 两列都是 `na`；一个间隔里有一次采样失败或被跳过、有一行不是 0 到 100 的整数、或回答的 GPU 数与这次运行的第一次不同，gpu_max 就为空
- 排程按 `/proc/uptime` 对准绝对时刻；每次 GPU 探测最多用间隔的 1/K（至少 1 秒，不超过 `AUTODL_PROBE_TIMEOUT`），到间隔末尾还没做的探测跳过，所以 `nvidia-smi` 慢或挂起不会让后面的行越推越晚；读不到 `/proc/uptime` 时拒绝运行。`--count 0` 只输出基线行
- 不拿锁、不写状态、不需要 arm，也不需要 flock；要做 GPU 采样（K 大于 0）时需要 timeout。某一项读不到或格式不对时那一列为空，与真正的 0 区分开
- 供实机校准与 `ctl calibrate` 使用
