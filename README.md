# autodl-gpu

[中文说明](README.cn.md)

A Claude Code skill that lets the AI in your conversation power AutoDL container instances on and off by itself, in GPU mode or in non-GPU mode, and gets a machine that is not in use shut down in good time.

A pay-as-you-go AutoDL instance is billed by the second from power-on to power-off, whether the GPU is busy or not. A training run that ended while nobody was looking, or an AI session that broke off, keeps costing money. The skill does three things.

- **The AI does the switching.** Power-on with or without GPU, power-off, changing the mode, reading state and balance: the AI of the current conversation does all of it, nobody has to click
- **A guard on the instance.** A guard script judges idleness by what the GPU, the CPU, the disk and the network are really doing, and shuts the instance down after the idle time you chose. It does so even when the AI session is gone
- **A budget gate.** What you allowed (which modes, how much) is kept on your machine and checked before every power-on; near the end of the budget you are asked first

It only provides the means. When to power on, in which mode, and when to shut down is decided by the AI, from the task and your habits. None of the three is an absolute guarantee; what each one covers and what it does not is in "Which of them is a hard limit".

## Requirements

| Needed | Notes |
|---|---|
| Claude Code | The CLI, the desktop app or an IDE extension |
| Python | 3.8 or later with `tarfile.data_filter`, that is 3.8.17, 3.9.17, 3.10.12, 3.11.4, 3.12 or newer |
| An OpenSSH client | `ssh` on the PATH |
| bash | On Windows that is Git Bash, the shell of Claude Code's Bash tool |
| A browser tool | Powering on happens on the AutoDL console web page: the built-in browser of the Claude desktop app, or the Claude in Chrome extension |
| AutoDL | An account and an ordinary pay-as-you-go container instance |

It also works without a browser tool, see "Two levels".

## Install

```bash
git clone https://github.com/grenbel/autodl-gpu ~/.claude/skills/autodl-gpu
```

Then check the local environment.

```bash
bash ~/.claude/skills/autodl-gpu/scripts/ctl doctor
```

It reports, item by item, whether python, bash, ssh, paths with spaces and Chinese characters, the local record and the launcher work. The `python` item names the interpreter the launcher picked and its version; to use another one, point the environment variable `AUTODL_PYTHON` at it.

## First use

Once per computer.

1. Create an SSH key, for example `ssh-keygen -t ed25519`. Leave the passphrase empty or put the key into ssh-agent: ctl never stops to ask for a passphrase
2. Add the public key under "设置SSH免密登录" above the instance list in the AutoDL console; it holds for every instance of the account
3. Give the instance an alias in `~/.ssh/config`; host and port come from the instance's "登录指令", and the password is not needed. You can leave this step to the AI: it only needs the host and the port from you. If you write it yourself, follow the pattern below; the last line must be there, or the first connection fails because the host's key is not known yet

   ```
   Host autodl-demo
       HostName <host from the login command>
       Port <port from the login command>
       User root
       IdentityFile ~/.ssh/id_ed25519
       StrictHostKeyChecking accept-new
   ```

4. Log in to AutoDL in the browser Claude uses. You log in yourself; the AI does not touch passwords or captchas

Once per project. Tell the AI which instance the project uses; it will settle four things with you.

- The instance: its ID and the SSH alias
- The use of modes, one of three: non-GPU for moving data and GPU for experiments, GPU only, non-GPU only
- The budget: whether there is one, how much, in money or in GPU hours, per month or without periods
- The guard's settings: after how much idle time to shut down (15 minutes is a good start), whether to set a latest shutdown, whether to set a timer in the console

The instance and the guard's settings go into the `## AutoDL` section of the project's CLAUDE.md; the modes and the budget are kept on your machine. Later conversations use them without asking again.

The modes and the budget you name are the permission: within them the AI powers on and spends money by itself. The permission has no expiry, is kept per instance on this computer, and holds for conversations in any project. To look at it, have the AI run `ctl auth show`; to change it, state the new range; to take it back, have it run `ctl auth revoke --instance <instance ID>`.

## Using it

Just say what you want done: "start the training", "shut it down once the data is copied", "how much of this month's budget is left". The AI follows `SKILL.md`: it powers on, deploys the guard, runs the job and shuts down, without narrating each step; you get one line after the power-on and one after the shutdown.

The first time an instance is powered on, the guard is not installed yet. The AI first sets a shutdown timer in the console, 30 minutes ahead, and cancels it as soon as the guard is configured. Should the conversation break in those one or two minutes, the platform still shuts the machine down. Seeing that timer appear and disappear in the console is normal.

A console timer can only be cancelled while the AI is there. If a conversation breaks off, or the guard shuts the machine down after the conversation has ended, a timer may still sit on that row. So before you power the instance on by hand, look at its row: cancel or change a timer you find there, or it will shut the machine down when its time comes. What an old timer does whose time passed while the machine was off has not been tested; cancel that one too.

## The guard stays on the instance

The one thing to know once the skill is installed.

- On its first deployment the AI puts the guard script on the data disk, in `/root/autodl-tmp/.autodl-guard/`, and writes a boot hook on the system disk, `/etc/profile.d/autodl-gpu-guard.sh`
- From then on **it starts by itself at every power-on**, including the ones you do by hand in the console, with the settings of the last time. An open terminal, Jupyter, screen or tmux does not count as use, and very light activity is not seen. So if you power on by hand and only read code without running anything, the machine is shut down once the idle time is over
- To keep it from shutting down this time, have the AI hold it for a while (`ctl keep`, with a duration)
- To stop it from starting with the instance, have the AI remove the hook (`ctl autostart <alias> uninstall`). That holds from the next power-on; a guard already running in this one carries on
- After a system reset or a change of image the system disk is new, so the hook is gone (not tested live; it follows from the disk being replaced). The power-on after that has no guard until the AI deploys it again. So tell the AI when you have done either: it will first set a provisional shutdown timer as a backstop
- The guard does not know which instance it is on. An image saved from this instance, or a clone of it, that carries both places along will start the guard there as well, with the same settings

## Which of them is a hard limit

Each of these covers a part; none is an absolute guarantee.

- **Shutdown when idle** looks at real use. A hung process that still holds the GPU or the CPU counts as use, and so does a signal that cannot be read; in both cases it does not shut down
- **The latest shutdown** (optional) does not cut a job that is still in use when its time comes; the machine goes down once the work is done
- **The budget** is checked before a power-on and before the machine is kept up longer; it is not a hard limit. Nobody checks again when a job runs past the planned time
- **The console's shutdown timer** (optional): the platform shuts the machine down at that time and cuts whatever runs. It is the only absolute limit, and it is set only if you ask for it

If you want an absolute limit, have the AI set the console timer at every power-on.

## Two levels

| | With a browser tool | Without one |
|---|---|---|
| Power-on | automatic | you click in the console; the AI tells you which button of which row, and when. You also read the balance, the notices and the row's shutdown timer to it, and you set or cancel the timer |
| Shutdown, guard, jobs | automatic | automatic |
| Checks and accounting after a shutdown | automatic | you look whether the row says shut down, cancel a timer still on it, and read the last charge in the billing detail to the AI. While you are away the accounting waits for you |

The automatic power-on relies on the browser tool's ability to run a script in the page. An environment or a model that will not use that ability for clicking falls back to the right-hand column.

## What it does not do

- It does not type passwords, solve captchas or keep tokens, and it does not ask you for a password or the content of a private key
- It does not click release, reset, change image, resize, migrate, clone, switch to monthly billing, recharge or renew, and it does nothing on the pages for costs and bills. The billing detail it only reads, to check the charges
- Clicks in the console go only through the fixed functions of `reference/console.js`. When the page does not match the manual it stops and tells you; it does not guess or work around
- A forced shutdown cuts running jobs off; it is used only when you agree on the spot
- It does not confirm a power-off in the console. Shutdown goes over SSH; in the rare case that SSH never comes up after a power-on, this version at most opens the power-off confirmation, reads its text and cancels it, and the last click is yours

## Privacy

- The local record (what you allowed, the usage ledger, calibrations) is in `~/.autodl-gpu`. It is not uploaded and belongs to no repository. The environment variable `AUTODL_GPU_HOME` moves it
- A project's power log is `.autodl/power_log.jsonl` in the project directory
- The page script only reads the console page and clicks its few fixed buttons; it sends no requests and reads no cookies. So that the script need not be pasted again after every reload, the AI keeps the script's own text in the session storage of that console tab (this one item, gone when the tab closes) and checks its checksum again before running it
- The billing detail page shows the charges of every instance of the account, and the balance. The AI can read all of it and uses only the rows of this instance. The balance is used for the accounting of the moment and is not kept. The imported charge rows (transaction number, time, amount) are stored in the local record, where they feed the budget and let a repeated import be recognised; they are not written into project files

## Platforms

| Environment | State |
|---|---|
| Windows 11, Git Bash, Python 3.13 and 3.9 | tested; all local tests pass |
| WSL (Python 3.12) | partly tested: all of the guard's tests, permissions and locking of the local record, file upload (push), the ssh check of `doctor` |
| macOS, Linux desktop | not verified |
| Python 3.8 | not verified |
| The built-in browser of the Claude desktop app | tested, also with its pane hidden: power-on with and without GPU, setting and cancelling the shutdown timer |
| The Claude in Chrome extension | not verified |

Live tests on a real instance were done in several sessions from 2026-09-29 to 10-02. The acceptance of 10-02 used the very guard script, ctl and page script that are released, and covered: power-on in both modes, a shutdown timer set before the power-on and cancelled afterwards, the start with the instance, shutdown when idle, a latest shutdown that does not cut a running job, and what to do after the guard's daemon has stopped. One pass was done alone by an AI that had read only the released files and had taken no part in the development. A console timer shutting the machine down at its time was seen on 09-29.

The instance is an ordinary AutoDL container instance, and the guard needs bash, flock and timeout there. The official PyTorch image used for testing has all three; on an image that lacks one, the guard says which.

## Tests

There are three suites, each with its own needs. Only all three together are "all tests".

| What is tested | How to run it | Needs |
|---|---|---|
| The local side: ctl, the local record and its ledger, file transfer, static checks of the documents and the page script | `python -m pytest tests -q` | Python and pytest. Some of these tests also need bash or WSL and are skipped without them; `-rs` lists what was skipped |
| The guard that runs on the instance | `bash tests/test_guard.sh` | bash on Linux; on Windows `wsl.exe -e bash tests/test_guard.sh` |
| The console page script (offline, on samples of the page) | `python tests/console/run_headless.py` | Edge or Chrome on this computer (run headless, with no network). Without one, open `tests/console/run.html` in a browser; how is written at the top of that file |

pytest ends by reminding you of the other two. The guard's tests replace screen, tmux, nvidia-smi and the shutdown command by stubs, so nothing is really shut down. But they end test processes with `pkill -f` on words of the command line (`sleep 600`, for example), and any other process on that Linux whose command line has those words ends with them. So do not run two copies at once, do not run them on a machine that is doing real work, and never on an instance. There is no continuous integration; the state of the tests in the public repository is what you get when you run them.

## What is in this repository

| Path | What it is |
|---|---|
| `SKILL.md` | what the AI reads first: the flow, the rules, what to do when something goes wrong |
| `reference/console.md`, `reference/console.js` | the manual for the console and the page script that does the clicking |
| `reference/ssh.md` | how the guard and ctl work, with every command and exit code |
| `scripts/` | `ctl` (the launcher), `autodl_ctl.py` (the helper on your computer), `autodl_guard.sh` (the guard that runs on the instance) |
| `tests/`, `dev/analyze_samples.py` | the three test suites, and a tool that summarises the guard's samples |

Using the skill needs only the first four rows.

## License

MIT, see `LICENSE`. Copyright (c) 2026 grenbel.
