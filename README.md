# autodl-gpu

[中文说明](README.cn.md)

A Claude Code skill that lets the AI in your conversation power AutoDL container instances on and off by itself, in GPU mode or in non-GPU mode, and gets a machine that is not in use shut down in good time.

A pay-as-you-go AutoDL instance is billed by the second from power-on to power-off, whether the GPU is busy or not. A training run that ended while nobody was looking, or an AI session that broke off, keeps costing money. The skill does three things.

- **The AI does the switching.** Power-on with or without GPU, power-off, changing the mode, reading state and balance: the AI of the current conversation does all of it, nobody has to click
- **A guard on the instance.** A guard script judges idleness by what the GPU, the CPU, the disk and the network are really doing, and shuts the instance down after the idle time you chose. It does so even when the AI session is gone
- **A budget gate.** What you allowed (which modes, how much) is kept on your machine and checked before every power-on; near the end of the budget you are asked first

It only provides the means. When to power on, in which mode, and when to shut down is decided by the AI, from the task and your habits. None of the three is an absolute guarantee; what each one covers and what it does not is in "Which of them is a hard limit". There is one more feature, off by default: when the host of the instance has no free GPU for a long time, the AI clones an instance of the same configuration and carries on there, see "Cloning when no GPU is free".

## Requirements

| Needed | Notes |
|---|---|
| Claude Code | The CLI, the desktop app or an IDE extension |
| Python | 3.8 or later, with a recent patch release of its branch: 3.8.17, 3.9.17, 3.10.12, 3.11.4, 3.12 or newer. The AI's check of the environment says so when the version is too old |
| An OpenSSH client | `ssh` on the PATH |
| bash | On Windows that is Git Bash, the shell of Claude Code's Bash tool |
| A browser tool | Powering on happens on the AutoDL console web page: the built-in browser of the Claude desktop app, or the Claude in Chrome extension |
| AutoDL | An account and an ordinary pay-as-you-go container instance |

It also works without a browser tool, see "Two levels".

## Install

```bash
git clone https://github.com/grenbel/autodl-gpu ~/.claude/skills/autodl-gpu
```

You can also send this command to the AI and let it do the installing. After that, start a new conversation and the skill is ready. The first time it is used the AI checks the local environment itself and tells you what is missing.

## First use

There are only three things for you to do. Creating the key, writing the SSH configuration, checking the environment and deploying the guard are done by the AI, following `SKILL.md`.

1. **Add the public key to AutoDL.** The AI gives you one line, a public key, in the conversation; paste it under "设置SSH免密登录" above the instance list in the AutoDL console. Once per computer, and it holds for every instance of the account. If AutoDL already knows a key of yours, just tell the AI which one
2. **Send the AI the login command.** Copy the instance's "登录指令" in the console and send it; it holds the host and the port. Do not send the password
3. **Log in to AutoDL in the browser Claude uses.** You log in yourself; the AI does not touch passwords or captchas

Then tell the AI which instance the project uses. The first time in each project it settles five things with you.

- The instance: which one
- The use of modes, one of three: non-GPU for moving data and GPU for experiments, GPU only, non-GPU only
- The budget: whether there is one, how much, in money or in GPU hours, per month or without periods
- The guard's settings: after how much idle time to shut down (15 minutes is a good start), whether to set a latest shutdown, whether to set a timer in the console
- Whether to clone automatically when no GPU is free: off by default and never turned on without your word, see "Cloning when no GPU is free"

The AI writes the instance and the guard's settings into the `## AutoDL` section of the project's CLAUDE.md; the modes, the budget and the clone settings are kept on your machine. Later conversations use them without asking again.

The modes and the budget you name are the permission: within them the AI powers on and spends money by itself. The permission has no expiry, is kept per instance on this computer, and holds for conversations in any project. To see it, change it or take it back, just tell the AI.

## Using it

Just say what you want done: "start the training", "shut it down once the data is copied", "how much of this month's budget is left". The AI follows `SKILL.md`: it powers on, deploys the guard, runs the job and shuts down, without narrating each step; you get one line after the power-on and one after the shutdown. You do not have to click a power-on or a power-off in the console yourself (except without a browser tool, see "Two levels").

- **With automated research or experiment pipelines.** When you say "run the experiments of the plan", or another experiment skill reaches the step that needs this instance, the AI brings this skill in first: power-on, starting the jobs and shutdown all go through it. The `## AutoDL` section of the project's CLAUDE.md says that this instance is run through the skill, so every conversation in the project knows
- **When it does not pick the skill up by itself**, type `/autodl-gpu`, or say "use autodl-gpu"
- **The AI can act only while the conversation goes on.** Let it wait in the background for the job to end, and it stays in charge until the results are fetched and the machine is shut down. When the conversation is gone (the app closed, the network down), the job runs on and the guard on the instance shuts the machine down once it is idle
- The first time an instance is powered on you may see a shutdown timer appear and disappear in the console. It is a provisional backstop the AI sets until the guard is installed, and it is normal

## Cloning when no GPU is free

An AutoDL instance is tied to one host. When other users hold all the GPUs of that host, your instance cannot be powered on in GPU mode. Normally the AI waits: it looks every few minutes, powers on as soon as a GPU is free, and tells you when none comes.

You can also let it clone when the wait runs out. The feature is off by default and is used only after you have said so. Once it is on and no GPU has come free within the time you set (30 minutes by default), the AI rents a second instance with the configuration of the original (the same region; the same GPU model and count, CPU and memory; the same driver or a newer one; a price that is not higher; the same paid expansion of the data disk when the original has one), has the system disk and the data disk copied over, and carries the job on there. When there is no such host with a free GPU it does not clone: it keeps waiting and tells you. None of this needs you to be there. Turning the feature on is your consent to this spending, given in advance: when the time comes the AI does not ask again, it tells you in one line what it is about to rent and for how much, and goes on.

What to know before you turn it on.

- **It costs money.** The new instance is billed from its creation on, passes the same budget gate as a power-on, and shares one budget with the original. When the original has a paid expansion of its data disk, the new one gets the same, and from then on both are charged the daily fee for it, on or off, until you release one of them
- **The original is yours to release.** The AI never clicks release. After the move it tells you how far the data was checked, what the original still costs per day and when the platform will release it by itself; whether and when to release it is your decision, in the console
- **A new instance that nobody takes over shuts itself down.** Before cloning, the AI puts a clone ticket on the original's system disk (`/etc/profile.d/autodl-gpu-clone-ticket.sh`, removed from both instances when the clone is closed), and the new instance starts with it. If the conversation breaks off and nobody takes over, the new instance shuts down at the time on the ticket instead of running on idle
- **Until a clone is closed, do not clone the original or save an image of it yourself.** The ticket would be copied along and shut that instance down too. The AI tells you when the clone is closed
- **The AI powers the original on without GPU twice**, for a few minutes each: once to prepare the clone, once to remove the ticket at the end. It does so even when you allowed GPU use only
- How many clones a task may make is yours to set, 1 by default

The details are in `reference/clone.md`. This feature has been run in full once on a real account, with a very small data disk, see "Platforms".

## The guard stays on the instance

The one thing to know once the skill is installed. The first deployment leaves two things on the instance: the guard script on the data disk (`/root/autodl-tmp/.autodl-guard/`) and a boot hook on the system disk (`/etc/profile.d/autodl-gpu-guard.sh`). From then on the guard starts by itself at every power-on, with the settings of the last time.

Most of the time this does not concern you. It matters in these cases.

- **When you power on in the console yourself, without the AI** (just to look at some files, say). The guard starts all the same. An open terminal, Jupyter, screen or tmux does not count as use, and very light activity is not seen; so if you only read code without running anything, the machine is shut down once the idle time is over. To keep it up, tell the AI how long to hold it. Also, before you power on, look at the row for a shutdown timer: cancel or change one you find there, or it will shut the machine down when its time comes. A timer the AI has set is cancelled only while the AI is there, so it may still sit on the row after a conversation broke off (what a timer does whose time is long past has not been tested; cancel that one too)
- **After a system reset or a change of image**, tell the AI the next time you have it power on. The system disk is new then and the hook is most likely gone (not tested live); knowing that, the AI first sets a provisional shutdown timer as a backstop
- **When you save an image of this instance or clone it.** The guard does not know which instance it is on; if both of these are carried along, it starts there as well, with the same settings
- **When you do not want it to start with the instance**, have the AI remove the hook. That holds from the next power-on; a guard already running in this one carries on

## Which of them is a hard limit

Each of these covers a part; none is an absolute guarantee.

- **Shutdown when idle** looks at real use. A hung process that still holds the GPU or the CPU counts as use, and so does a signal that cannot be read; in both cases it does not shut down
- **The latest shutdown** (optional) does not cut a job that is still in use when its time comes; the machine goes down once the work is done
- **The budget** is checked before a power-on and before the machine is kept up longer; it is not a hard limit. Nobody checks again when a job runs past the planned time
- **The console's shutdown timer** (optional): the platform shuts the machine down at that time and cuts whatever runs. It is the only absolute limit, and it is set only if you ask for it
- **The clone ticket** exists only during a clone and covers only the new instance that nobody has taken over yet: at the time on the ticket that instance shuts down. It runs along the same path as the guard's boot hook; before cloning, the AI makes sure that this path works right now, and does not clone when it does not

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
- It does not click release, reset, change image, resize, migrate, switch to monthly billing, recharge or renew, and it does nothing on the pages for costs and bills. The billing detail it only reads, to check the charges. It clones only after you have turned automatic cloning on, and leaves cloning alone otherwise
- In the console it clicks only a fixed few buttons: power-on, power-on without GPU, setting and cancelling the shutdown timer; with automatic cloning on, also a fixed few places in the clone dialog and on the page that creates the instance, the last of them "创建并开机" (create and power on). When the page is not what it expects it stops and tells you; it does not guess or work around
- A forced shutdown cuts running jobs off; it is used only when you agree on the spot
- It does not shut down in the console. Shutdown goes over SSH; in the rare case that SSH never comes up after a power-on, it asks you to click the power-off in the console yourself. You may then see the power-off confirmation appear and disappear: that is the AI reading its text

## Privacy

- The local record (what you allowed, the usage ledger, the clone settings and records, calibrations) is in `~/.autodl-gpu`. It is not uploaded and belongs to no repository. The environment variable `AUTODL_GPU_HOME` moves it
- A project's power log is `.autodl/power_log.jsonl` in the project directory; while a clone is not closed yet there is also a copy of its record for people to read, `.autodl/clone_pending.json`
- The page scripts only read the console page and click their few fixed buttons; they send no requests and read no cookies. So that a script need not be pasted again after every reload, the AI keeps the script's own text in the session storage of that console tab (one item per script, three at most, gone when the tab closes) and checks its checksum again before running it
- During a clone, to connect to the new instance, the script reads the SSH host, the port and the login command of the new instance's row from the page's own data (the three are checked against each other). It does not read the password; when it cannot read them, the AI asks you to paste the login command. To recognise which row is the new one, the clone record keeps digests of the instance IDs that were in the list before the creation, not the IDs themselves
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
| Cloning when no GPU is free | tested once (2026-10-03): on a real account an instance was cloned, the job moved to it and the clone closed, and every file of the two data disks compared the same; a clone ticket that nobody took over shut its instance down by itself. The data disk was very small (under 200 KB) and the clone was up in about a minute. How long a large data disk takes and what the console shows meanwhile, and how the platform answers when creating fails, have not been seen |

Live tests on a real instance were done in several sessions from 2026-09-29 to 10-02. The acceptance of 10-02 used the very guard script, ctl and page script that are released, and covered: power-on in both modes, a shutdown timer set before the power-on and cancelled afterwards, the start with the instance, shutdown when idle, a latest shutdown that does not cut a running job, and what to do after the guard's daemon has stopped. One pass was done alone by an AI that had read only the released files and had taken no part in the development. A console timer shutting the machine down at its time was seen on 09-29.

The instance is an ordinary AutoDL container instance, and the guard needs bash, flock and timeout there. The official PyTorch image used for testing has all three; on an image that lacks one, the guard says which.

## Tests

Using the skill needs no test run. Run them to verify things yourself or when you change the code. There are three suites, each with its own needs. Only all three together are "all tests".

| What is tested | How to run it | Needs |
|---|---|---|
| The local side: ctl, the local record and its ledger, file transfer, static checks of the documents and the page script | `python -m pytest tests -q` | Python and pytest. Some of these tests also need bash or WSL and are skipped without them; `-rs` lists what was skipped |
| The guard that runs on the instance | `bash tests/test_guard.sh` | bash on Linux; on Windows `wsl.exe -e bash tests/test_guard.sh` |
| The console page scripts, that of the instance list and that of the page that creates a clone (offline, on samples of the pages) | `python tests/console/run_headless.py` | Edge or Chrome on this computer (run headless, with no network). Without one, open `tests/console/run.html` in a browser; how is written at the top of that file. That page tests the instance list's script only |

pytest ends by reminding you of the other two. The guard's tests replace screen, tmux, nvidia-smi and the shutdown command by stubs, so nothing is really shut down. But they end test processes with `pkill -f` on words of the command line (`sleep 600`, for example), and any other process on that Linux whose command line has those words ends with them. So do not run two copies at once, do not run them on a machine that is doing real work, and never on an instance. There is no continuous integration; the state of the tests in the public repository is what you get when you run them.

## What is in this repository

| Path | What it is |
|---|---|
| `SKILL.md` | what the AI reads first: the flow, the rules, what to do when something goes wrong |
| `reference/console.md`, `reference/console-more.md` | the manual for the console; the second file holds the four sections that are seldom needed |
| `reference/console.js`, `reference/console.min.js` | the page script that does the clicking, and the copy without comments that is pasted into the page |
| `reference/ssh.md`, `reference/guard.md`, `reference/ledger.md` | how ctl connects and its commands; how the guard decides; the local record with grants, the ledger, the clone settings and calibration |
| `reference/clone.md`, `reference/console-clone.min.js`, `reference/clone-page.js`, `reference/clone-page.min.js` | waiting for a free GPU and cloning: how it is done, the copy of the instance list's script that has the clone functions, the script of the creating page and its copy without comments |
| `scripts/` | `ctl` (the launcher), `autodl_ctl.py` (the helper on your computer), `autodl_guard.sh` (the guard that runs on the instance) |
| `tests/`, `dev/analyze_samples.py` | the three test suites, and a tool that summarises the guard's samples |

Using the skill needs every row but the last. The AI reads `SKILL.md` each time and the reference files only as far as the task at hand needs them.

## License

MIT, see `LICENSE`. Copyright (c) 2026 grenbel.
