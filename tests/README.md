# Tests

Using the skill needs no test run. Run the tests to verify things yourself, or when you change the code. (中文在下面。)

There are three suites, each with its own needs. Only all three together are "all tests".

| What is tested | How to run it | Needs |
|---|---|---|
| The local side: ctl, the local record and its ledger, file transfer, static checks of the documents and the page script | `python -m pytest tests -q` | Python and pytest. Some of these tests also need bash or WSL and are skipped without them; `-rs` lists what was skipped |
| The guard that runs on the instance | `bash tests/test_guard.sh` | bash on Linux; on Windows `wsl.exe -e bash tests/test_guard.sh` |
| The console page scripts, that of the instance list and that of the page that creates a clone (offline, on samples of the pages) | `python tests/console/run_headless.py` | Edge or Chrome on this computer (run headless, with no network). Without one, open `tests/console/run.html` in a browser; how is written at the top of that file. That page tests the instance list's script only |

pytest ends by reminding you of the other two.

**Before you run the guard's tests.** They replace screen, tmux, nvidia-smi and the shutdown command by stubs, so nothing is really shut down. But they end test processes with `pkill -f` on words of the command line (`sleep 600`, for example), and any other process on that Linux whose command line has those words ends with them. So do not run two copies at once, do not run them on a machine that is doing real work, and never on an instance.

There is no continuous integration. The state of the tests in the public repository is what you get when you run them.

## 中文

只是用这个 skill 的话不用跑测试。想自己验证，或者要改代码时再跑。

测试分三组，各有各的前提，三组都过才算全过。

| 测什么 | 怎么跑 | 需要 |
|---|---|---|
| 本机一侧，有 ctl、本机记录与账本、文件传输、文档与页面脚本的静态检查 | `python -m pytest tests -q` | Python 与 pytest。其中一部分还要 bash 或 WSL，缺了会跳过，加 `-rs` 看跳过了哪些 |
| 实例上的守护脚本 | `bash tests/test_guard.sh` | Linux 的 bash，Windows 上用 `wsl.exe -e bash tests/test_guard.sh` |
| 控制台的页面脚本，实例列表页的与克隆创建页的（离线，用页面样本） | `python tests/console/run_headless.py` | 本机装有 Edge 或 Chrome（用它的无界面模式，不联网）。没有的话在浏览器里打开 `tests/console/run.html`，做法见该文件开头；这一页只测实例列表页的那份脚本 |

pytest 跑完会提醒还有后两组。

**跑守护的测试之前要知道。** 它用桩程序代替 screen、tmux、nvidia-smi 与关机命令，不会真的关机。但它用 `pkill -f` 按命令行里的字样（例如 `sleep 600`）结束测试进程，那台 Linux 上命令行里带这些字样的别的进程也会被结束。所以不要同时跑两份，不要在跑着正事的机器上跑，更不要在实例上跑。

没有持续集成，公开仓库里的测试结果以你自己跑出来的为准。
