<p align="center"><img src="assets/hero.svg" alt="autodl-autogpu: power on, run the job, shut down" width="830"></p>

<h1 align="center">Let your AI power AutoDL instances on and off</h1>

<p align="center">
You say what you want done. Power-on, the job, shutdown and the bookkeeping are its work.<br>
If the conversation dies, the machine still shuts down once it is idle. Near the end of the budget, it asks you first.
</p>

<p align="center">
<a href="#quick-start">Quick start</a> ·
<a href="#usage">What using it looks like</a> ·
<a href="#money">How the spending is kept in check</a> ·
<a href="#nogpu">When no GPU is free</a> ·
<a href="#faq">Questions</a> ·
<a href="README.cn.md">中文</a>
</p>

<p align="center">
<a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2ea44f?style=flat-square"></a>
<a href="#tested"><img alt="Tested on a real account" src="https://img.shields.io/badge/tested-on_a_real_account-1f6feb?style=flat-square"></a>
</p>

<p align="center"><img src="assets/demo.svg" alt="One run from the live tests. You say: start the training. It passes the budget gate, powers on, runs the job, shuts down and books the charge. This run took 8 min 47 s and cost 0.14 yuan" width="830"></p>

<a id="why"></a>

## Why you want it

A pay-as-you-go AutoDL instance is billed by the second from power-on to shutdown, whether the GPU is busy or not. This is how money goes to machines nobody is using.

- The training finished in the middle of the night, and the machine stayed on until you remembered it the next day
- You let an AI run the experiments, its session broke off, and nobody shut the machine down
- Moving data needs only the non-GPU mode at 0.1 yuan an hour, but switching back and forth is a chore, so the GPU mode stays on

autodl-autogpu is a skill: instructions that an AI coding assistant follows, plus a few scripts. It hands the power switch to the AI in your conversation and adds two things, a guard that shuts the machine down even when the AI is gone, and a budget gate in front of every power-on.

- **Hands off.** Power-on with or without GPU, shutdown, changing the mode, reading state and balance: the AI does all of it, nobody has to click in the console
- **A backstop.** A guard on the instance judges idleness by what the GPU, the CPU, the disk and the network are really doing, and shuts the instance down after the idle time you chose. It does so even when the AI's session is gone
- **Accounted for.** The modes and the budget you allowed are kept on your machine and checked before every power-on; near the end of the budget you are asked first. After every shutdown the charge is checked and booked

It only provides the means. When to power on, in which mode, and when to shut down is decided by the AI, from the task and your habits. None of this is an absolute guarantee; what each part covers and what it does not is in [How the spending is kept in check](#money).

<a id="usage"></a>

## What using it looks like

Just say what you want done. There are no commands to remember.

| You say | It does |
|---|---|
| start the training | reads the balance and the instance's state, passes the budget gate, powers on with GPU, makes sure it is the right instance and arms the guard, starts the job, reports in one line |
| upload the data and shut it down once it is copied | powers on in a mode you allowed (non-GPU for moving data when you allowed both), copies the files, shuts down, checks the charge and books it, reports in one line |
| how much of this month's budget is left | answers from the ledger on your machine; nothing is powered on |
| run the experiments of the plan | when another experiment skill reaches the step that needs this instance, the AI brings this skill in first: power-on, starting the jobs and shutdown all go through it |

The last row is the skill inside an automated research or experiment pipeline. The project's instruction file (`CLAUDE.md` for Claude Code, `AGENTS.md` for Codex) has an `## AutoDL` section saying that this instance is run through the skill, so every conversation in the project knows. When the AI does not pick the skill up by itself, type `/autodl-autogpu`, or say "use autodl-autogpu".

It does not narrate each step. You get one line after the power-on and one after the shutdown, like these.

```
Powered on with GPU (RTX 3080 Ti x1, 0.98 yuan/h, billed from 11:36:39). Shuts down after 15 idle minutes; no latest shutdown, no console timer.
Shut down. Billing stopped at 11:45:26: 8 min 47 s this time, 0.14 yuan; 0.60 GPU hours and 0.61 yuan in this project so far.
```

- You do not have to click a power-on or a power-off in the console yourself, except without a browser tool, see [Without a browser tool](#two-levels)
- The AI can act only while the conversation goes on. Let it wait in the background for the job to end, and it stays in charge until the results are fetched and the machine is shut down. When the conversation is gone (the app closed, the network down), the job runs on and the guard on the instance shuts the machine down once it is idle
- The first time an instance is powered on you may see a shutdown timer appear and disappear in the console. It is a provisional backstop the AI sets until the guard is installed, and it is normal

<a id="quick-start"></a>

## Quick start

### 1. Install

```bash
git clone https://github.com/grenbel/autodl-autogpu ~/.claude/skills/autodl-autogpu
```

You can also send this command to the AI and let it do the installing. After that, start a new conversation and the skill is ready. The first time it is used the AI checks the local environment itself and tells you what is missing.

<details>
<summary><b>Requirements</b> · Claude Code, Python 3.8 or later, OpenSSH, bash, a browser tool, a pay-as-you-go instance</summary>

| Needed | Notes |
|---|---|
| Claude Code | The CLI, the desktop app or an IDE extension. For Codex see the next block |
| Python | 3.8 or later, with a recent patch release of its branch: 3.8.17, 3.9.17, 3.10.12, 3.11.4, 3.12 or newer. The AI's check of the environment says so when the version is too old |
| An OpenSSH client | `ssh` on the PATH |
| bash | On Windows that is Git Bash, the shell of Claude Code's Bash tool |
| A browser tool | Powering on happens on the AutoDL console web page: the built-in browser of the Claude desktop app, or the Claude in Chrome extension |
| AutoDL | An account and an ordinary pay-as-you-go container instance |

It also works without a browser tool, see [Without a browser tool](#two-levels).

</details>

<details>
<summary><b>Using it in Codex</b> · the same format, another place and another way to call it; not tested yet</summary>

The skill has the format Codex uses too, and what the AI reads is written to hold for both. Three things differ; the Codex column is taken from OpenAI's documentation.

| | Claude Code | Codex |
|---|---|---|
| Where it is installed | `~/.claude/skills/autodl-autogpu` | `~/.agents/skills/autodl-autogpu` |
| Calling it by name | `/autodl-autogpu` | `$autodl-autogpu`, or `@` in the ChatGPT desktop app |
| Where a project's settings go | the `## AutoDL` section of the project's `CLAUDE.md` | the `## AutoDL` section of the project's `AGENTS.md` |

```bash
git clone https://github.com/grenbel/autodl-autogpu ~/.agents/skills/autodl-autogpu
```

Up to this version every live test was done in Claude Code; nothing has been run in Codex yet. The automatic power-on relies on a browser tool that can run a script in the page, and whether Codex's browser tool can be used that way is not known. If it cannot, use the skill as described in [Without a browser tool](#two-levels): you click the power-on, the rest is automatic.

</details>

### 2. Three things for you to do

Creating the key, writing the SSH configuration, checking the environment and deploying the guard are done by the AI, following `SKILL.md`. Only these three are yours.

1. **Add the public key to AutoDL.** The AI gives you one line, a public key, in the conversation; paste it under "设置SSH免密登录" above the instance list in the AutoDL console. Once per computer, and it holds for every instance of the account. If AutoDL already knows a key of yours, just tell the AI which one
2. **Send the AI the login command.** Copy the instance's "登录指令" in the console and send it; it holds the host and the port. Do not send the password
3. **Log in to AutoDL in the browser the AI uses.** You log in yourself; the AI does not touch passwords or captchas

### 3. Tell the AI which instance the project uses

The first time in each project it settles five things with you.

| To settle | What |
|---|---|
| The instance | which one |
| The use of modes | one of three: non-GPU for moving data and GPU for experiments, GPU only, non-GPU only |
| The budget | whether there is one, how much, in money or in GPU hours, per month or without periods |
| The guard's settings | after how much idle time to shut down (15 minutes is a good start), whether to set a latest shutdown, whether to set a timer in the console |
| When no GPU is free | whether to clone automatically: off by default and never turned on without your word, see [When no GPU is free](#nogpu) |

The AI writes the instance and the guard's settings into the `## AutoDL` section of the project's instruction file; the modes, the budget and the clone settings are kept on your machine. Later conversations use them without asking again.

> The modes and the budget you name are the permission: within them the AI powers on and spends money by itself. The permission has no expiry, is kept per instance on this computer, and holds for conversations in any project. To see it, change it or take it back, just tell the AI.

<a id="how"></a>

## One run, stop by stop

The five stops of the picture at the top, and what happens at each.

1. **Budget.** The balance is read in the console first; a notice about arrears, a low balance or maintenance means no power-on, and you are told. Then the budget gate: not enough means no power-on, and near the end of the budget you are asked first
2. **Power on.** An ordinary AutoDL container instance has no interface for powering on; it can only be clicked on the console web page. The AI does not click by itself: it calls a fixed few functions of the page script, and the final confirmation is clicked once. When the page is not what it expects it stops and tells you; it does not guess or work around. When the instance is up, the AI first makes sure it is this very instance, and stops at once if it is not
3. **Run.** Only then does it deploy the guard and start the job. Like moving files and shutting down, this goes over SSH through `ctl`, the helper on your computer, with key login only. The guard runs on the instance and looks at the real load once a minute; once installed it starts with the instance and does not depend on a conversation being there
4. **Shut down.** There are three ways. The AI, when the job is done. The guard, after the idle time you chose, when the conversation is gone. The platform, at the time of the console timer, if you chose to have one
5. **Booked.** The row is checked to say shut down, the last charge is read from the billing detail and booked in the ledger, and then comes the report. What counts for the cost is the console's billing detail. What you allowed, the usage and the charges are kept on your computer and are not uploaded, see [Privacy](#privacy)

<a id="two-levels"></a>

### Without a browser tool

| | With a browser tool | Without one |
|---|---|---|
| Power-on | automatic | you click in the console; the AI tells you which button of which row, and when. You also read the balance, the notices and the row's shutdown timer to it, and you set or cancel the timer |
| Shutdown, guard, jobs | automatic | automatic |
| Checks and accounting after a shutdown | automatic | you look whether the row says shut down, cancel a timer still on it, and read the last charge in the billing detail to the AI. While you are away the accounting waits for you |

The automatic power-on relies on the browser tool's ability to run a script in the page. An environment or a model that will not use that ability for clicking falls back to the right-hand column.

<a id="money"></a>

## How the spending is kept in check

<p align="center"><img src="assets/safety.svg" alt="Three things keep the spending in check. The budget gate, checked before every power-on. The guard, which shuts down when idle. The console timer, which shuts down on time, if you set it" width="830"></p>

Each of the three covers a part; none is an absolute guarantee.

- **The budget gate** is passed before a power-on and before the machine is kept up longer: not enough means no power-on, and near the end of the budget you are asked first. It is not a hard limit. Nobody checks again when a job runs past the planned time
- **The guard** shuts down when the instance is idle, and it looks at real use. A hung process that still holds the GPU or the CPU counts as use, and so does a signal that cannot be read; in both cases it does not shut down
- **The console timer** (optional): the platform shuts the machine down at that time and cuts whatever runs. It is the only absolute limit, and it is set only if you ask for it

Two more act only at particular times.

- **The latest shutdown** (optional) does not cut a job that is still in use when its time comes; the machine goes down once the work is done
- **The clone ticket** exists only during a clone and covers only the new instance that nobody has taken over yet: at the time on the ticket that instance shuts down. It runs along the same path as the guard's boot hook; before cloning, the AI makes sure that this path works right now, and does not clone when it does not

> If you want an absolute limit, have the AI set the console timer at every power-on.

<a id="nogpu"></a>

## When no GPU is free

An AutoDL instance is tied to one host. When other users hold all the GPUs of that host, your instance cannot be powered on in GPU mode.

<p align="center"><img src="assets/nogpu.svg" alt="Two paths when no GPU is free. Waiting for a free GPU is the default. Cloning to another host is off by default" width="830"></p>

**It waits for a free GPU. That is the default.** The AI looks every few minutes, powers on as soon as a GPU is free, and tells you when none comes.

**It clones to another host. That is off by default.** The feature is called cloning when no GPU is free, and it is used only after you have said so. Once it is on and no GPU has come free within the time you set (30 minutes by default), the AI rents a second instance with the configuration of the original, has the system disk and the data disk copied over, and carries the job on there. None of this needs you to be there. Turning the feature on is your consent to this spending, given in advance: when the time comes the AI does not ask again, it tells you in one line what it is about to rent and for how much, and goes on.

<details>
<summary><b>What "the configuration of the original" means</b> · the same region, the same GPU model and count, a price that is not higher</summary>

The same region; the same GPU model and count, and the same CPU cores and memory per GPU; the same driver or a newer one; the CPU model may differ; a price that is not higher; the same paid expansion of the data disk when the original has one. When there is no such host with a free GPU it does not clone: it keeps waiting and tells you.

</details>

What to know before you turn it on.

- **It costs money.** The new instance is billed from its creation on, passes the same budget gate as a power-on, and shares one budget with the original. When the original has a paid expansion of its data disk, the new one gets the same, and from then on both are charged the daily fee for it, on or off, until you release one of them
- **The original is yours to release.** The AI never clicks release. After the move it tells you how far the data was checked, what the original still costs per day and when the platform will release it by itself; whether and when to release it is your decision, in the console
- **A new instance that nobody takes over shuts itself down.** Before cloning, the AI puts a clone ticket on the original's system disk (`/etc/profile.d/autodl-autogpu-clone-ticket.sh`, removed from both instances when the clone is closed), and the new instance starts with it. If the conversation breaks off and nobody takes over, the new instance shuts down at the time on the ticket instead of running on idle
- **Until a clone is closed, do not clone the original or save an image of it yourself.** The ticket would be copied along and shut that instance down too. The AI tells you when the clone is closed
- **The AI powers the original on without GPU twice**, for a few minutes each: once to prepare the clone, once to remove the ticket at the end. It does so even when you allowed GPU use only
- **How many clones a task may make is yours to set.** 1 by default

The details are in `reference/clone.md`. This feature has been run in full twice on a real account, once with a very small data disk and once with about 20 GB on it, see [Tested, and on what](#tested).

<a id="guard-stays"></a>

## The guard stays on the instance

The one thing to know once the skill is installed. The first deployment leaves two things on the instance: the guard script on the data disk (`/root/autodl-tmp/.autodl-guard/`) and a boot hook on the system disk (`/etc/profile.d/autodl-autogpu-guard.sh`). From then on the guard starts by itself at every power-on, with the settings of the last time.

Most of the time this does not concern you. It matters in four cases.

<details>
<summary><b>The four cases</b> · you power on in the console yourself, a system reset or a new image, saving an image or cloning, not wanting the autostart</summary>

- **When you power on in the console yourself, without the AI** (just to look at some files, say). The guard starts all the same. An open terminal, Jupyter, screen or tmux does not count as use, and very light activity is not seen; so if you only read code without running anything, the machine is shut down once the idle time is over. To keep it up, tell the AI how long to hold it. Also, before you power on, look at the row for a shutdown timer: cancel or change one you find there, or it will shut the machine down when its time comes. A timer the AI has set is cancelled only while the AI is there, so it may still sit on the row after a conversation broke off (what a timer does whose time is long past has not been tested; cancel that one too)
- **After a system reset or a change of image**, tell the AI the next time you have it power on. The system disk is new then and the hook is most likely gone (not tested live); knowing that, the AI first sets a provisional shutdown timer as a backstop
- **When you save an image of this instance or clone it.** The guard does not know which instance it is on; if both of these are carried along, it starts there as well, with the same settings
- **When you do not want it to start with the instance**, have the AI remove the hook. That holds from the next power-on; a guard already running in this one carries on

</details>

<a id="never"></a>

## What it does not do

- **It does not touch credentials.** It does not type passwords, solve captchas or keep tokens, and it does not ask you for a password or the content of a private key
- **It leaves money and the fate of a machine alone.** It does not click release, reset, change image, resize, migrate, switch to monthly billing, recharge or renew, and it does nothing on the pages for costs and bills. The billing detail it only reads, to check the charges. It clones only after you have turned automatic cloning on, and leaves cloning alone otherwise
- **In the console it clicks only a fixed few buttons.** Power-on, power-on without GPU, setting and cancelling the shutdown timer; with automatic cloning on, also a fixed few places in the clone dialog and on the page that creates the instance, the last of them "创建并开机" (create and power on). When the page is not what it expects it stops and tells you; it does not guess or work around
- **A forced shutdown needs your word on the spot.** It cuts running jobs off
- **It does not shut down in the console.** Shutdown goes over SSH; in the rare case that SSH never comes up after a power-on, it asks you to click the power-off in the console yourself. You may then see the power-off confirmation appear and disappear: that is the AI reading its text

<a id="privacy"></a>

## Privacy

The local record is not uploaded and belongs to no repository. The page scripts only read the console page and click their few fixed buttons; they send no requests and read no cookies.

<details>
<summary><b>Point by point</b> · where the local record is, what the page scripts store, what is read during a clone, how the balance and the charges are used</summary>

- The local record (what you allowed, the usage ledger, the clone settings and records, calibrations) is in `~/.autodl-autogpu`. It is not uploaded and belongs to no repository. The environment variable `AUTODL_AUTOGPU_HOME` moves it
- A project's power log is `.autodl/power_log.jsonl` in the project directory; while a clone is not closed yet there is also a copy of its record for people to read, `.autodl/clone_pending.json`
- The page scripts only read the console page and click their few fixed buttons; they send no requests and read no cookies. So that a script need not be pasted again after every reload, the AI keeps the script's own text in the session storage of that console tab (one item per script, three at most, gone when the tab closes) and checks its checksum again before running it
- During a clone, to connect to the new instance, the script reads the SSH host, the port and the login command of the new instance's row from the page's own data (the three are checked against each other). It does not read the password; when it cannot read them, the AI asks you to paste the login command. To recognise which row is the new one, the clone record keeps digests of the instance IDs that were in the list before the creation, not the IDs themselves
- The billing detail page shows the charges of every instance of the account, and the balance. The AI can read all of it and uses only the rows of this instance. The balance is used for the accounting of the moment and is not kept. The imported charge rows (transaction number, time, amount) are stored in the local record, where they feed the budget and let a repeated import be recognised; they are not written into project files

</details>

<a id="tested"></a>

## Tested, and on what

| Environment | State |
|---|---|
| Windows 11, Git Bash, Python 3.13 and 3.9 | ✅ tested; all local tests pass |
| WSL (Python 3.12) | 🟡 partly tested: all of the guard's tests, permissions and locking of the local record, file upload (push), the ssh check of `doctor` |
| macOS, Linux desktop | ⬜ not verified |
| Python 3.8 | ⬜ not verified |
| The built-in browser of the Claude desktop app | ✅ tested, also with its pane hidden: power-on with and without GPU, setting and cancelling the shutdown timer |
| The Claude in Chrome extension | ⬜ not verified |
| Codex | ⬜ not verified, see "Using it in Codex" under [Quick start](#quick-start) |
| Cloning when no GPU is free | ✅ run twice on a real account, details below |

<details>
<summary><b>The two clone runs</b> · 2026-10-03 and 2026-10-06; every file of the two data disks compared the same both times</summary>

Tested twice on a real account (2026-10-03 with a data disk under 200 KB, 2026-10-06 with about 21.5 GB in some 66,000 files): an instance was cloned, the job moved to it and the clone closed, and every file of the two data disks compared the same both times; a clone ticket that nobody took over shut its instance down by itself. Both times the new instance was up about a minute after it was created. With the larger disk the data went on arriving for another five minutes or so (about 340 seconds for 21.5 GB), and the console showed the original as 克隆锁定中 (locked for the clone) meanwhile. How the platform answers when creating fails has not been seen.

</details>

<details>
<summary><b>The live tests of power-on and shutdown</b> · 2026-09-29 to 10-02, one pass by an AI that took no part in the development; checked again on 10-06 after the rename</summary>

Live tests on a real instance were done in several sessions from 2026-09-29 to 10-02. The acceptance of 10-02 used the guard script, ctl and page script as they were released then, and covered: power-on in both modes, a shutdown timer set before the power-on and cancelled afterwards, the start with the instance, shutdown when idle, a latest shutdown that does not cut a running job, and what to do after the guard's daemon has stopped. One pass was done alone by an AI that had read only the released files and had taken no part in the development. A console timer shutting the machine down at its time was seen on 09-29.

On 2026-10-06, after the skill was renamed, the present version powered an instance on twice without GPU. The page script read the row, set, changed and cancelled the shutdown timer and powered on as before; the boot hook the instance still had under the former name was replaced by the guard with one under the new name, and at the next power-on that hook started the guard.

</details>

The instance is an ordinary AutoDL container instance, and the guard needs bash, flock and timeout there. The official PyTorch image used for testing has all three; on an image that lacks one, the guard says which.

<a id="faq"></a>

## Questions people ask

<details>
<summary><b>The conversation is closed. Does the machine stay on?</b></summary>

The job runs on, and afterwards the guard on the instance shuts the machine down once it is idle. The guard looks at real use: a hung process that still holds the GPU or the CPU keeps it waiting, see [How the spending is kept in check](#money). For an absolute limit, have the AI set the console timer at every power-on.

</details>

<details>
<summary><b>After a shutdown the GPU is taken by someone else. What then?</b></summary>

The AI waits: it looks every few minutes, powers on as soon as a GPU is free, and tells you when none comes. You can also turn on automatic cloning; it is off by default, see [When no GPU is free](#nogpu).

</details>

<details>
<summary><b>I powered on in the console to look at some files, and the machine was shut down after a while</b></summary>

The guard started with the instance. If you only read code without running anything, it shuts the machine down once the idle time is over. To keep it up, tell the AI how long to hold it, see [The guard stays on the instance](#guard-stays).

</details>

<details>
<summary><b>Does it work without a browser tool?</b></summary>

Yes. Shutdown, the guard and running jobs stay automatic; for a power-on you click in the console, and the AI tells you which button of which row, and when, see [Without a browser tool](#two-levels).

</details>

<details>
<summary><b>Does it work in Codex?</b></summary>

The format is the same one. Where it is installed, how it is called and which file holds a project's settings differ, see "Using it in Codex" under [Quick start](#quick-start). Up to this version it has not been tested in Codex.

</details>

<details>
<summary><b>Does it touch my password, my balance or my bills?</b></summary>

It does not type passwords or solve captchas, it does not recharge or renew, and it does nothing on the pages for costs and bills. The balance is used for the accounting of the moment and is not kept, see [What it does not do](#never) and [Privacy](#privacy).

</details>

<details>
<summary><b>How do I see, change or take back what I allowed?</b></summary>

Just tell the AI. The permission is kept per instance on this computer, has no expiry, and holds for conversations in any project.

</details>

<details>
<summary><b>What happens to an instance I leave unused?</b></summary>

The platform's rule is that an instance shut down for 15 days in a row is released and its data wiped. Whenever the AI reads the console it looks at the release countdown too, and reminds you when fewer than 3 days are left.

</details>

<a id="tests"></a>

## Tests

Using the skill needs no test run. Run them to verify things yourself or when you change the code.

<details>
<summary><b>How to run the three suites</b> · the local side, the guard on the instance, the console page scripts</summary>

There are three suites, each with its own needs. Only all three together are "all tests".

| What is tested | How to run it | Needs |
|---|---|---|
| The local side: ctl, the local record and its ledger, file transfer, static checks of the documents and the page script | `python -m pytest tests -q` | Python and pytest. Some of these tests also need bash or WSL and are skipped without them; `-rs` lists what was skipped |
| The guard that runs on the instance | `bash tests/test_guard.sh` | bash on Linux; on Windows `wsl.exe -e bash tests/test_guard.sh` |
| The console page scripts, that of the instance list and that of the page that creates a clone (offline, on samples of the pages) | `python tests/console/run_headless.py` | Edge or Chrome on this computer (run headless, with no network). Without one, open `tests/console/run.html` in a browser; how is written at the top of that file. That page tests the instance list's script only |

pytest ends by reminding you of the other two. The guard's tests replace screen, tmux, nvidia-smi and the shutdown command by stubs, so nothing is really shut down. But they end test processes with `pkill -f` on words of the command line (`sleep 600`, for example), and any other process on that Linux whose command line has those words ends with them. So do not run two copies at once, do not run them on a machine that is doing real work, and never on an instance. There is no continuous integration; the state of the tests in the public repository is what you get when you run them.

</details>

<a id="files"></a>

## What is in this repository

<details>
<summary><b>Path by path</b> · using the skill needs every row but the last two</summary>

| Path | What it is |
|---|---|
| `SKILL.md` | what the AI reads first: the flow, the rules, what to do when something goes wrong |
| `reference/console.md`, `reference/console-more.md` | the manual for the console; the second file holds the four sections that are seldom needed |
| `reference/console.js`, `reference/console.min.js` | the page script that does the clicking, and the copy without comments that is pasted into the page |
| `reference/ssh.md`, `reference/guard.md`, `reference/ledger.md` | how ctl connects and its commands; how the guard decides; the local record with grants, the ledger, the clone settings and calibration |
| `reference/clone.md`, `reference/console-clone.min.js`, `reference/clone-page.js`, `reference/clone-page.min.js` | waiting for a free GPU and cloning: how it is done, the copy of the instance list's script that has the clone functions, the script of the creating page and its copy without comments |
| `scripts/` | `ctl` (the launcher), `autodl_ctl.py` (the helper on your computer), `autodl_guard.sh` (the guard that runs on the instance) |
| `assets/` | the four pictures of this page, each in English and in Chinese |
| `tests/`, `dev/analyze_samples.py` | the three test suites, and a tool that summarises the guard's samples |

The AI reads `SKILL.md` each time and the reference files only as far as the task at hand needs them.

</details>

<a id="license"></a>

## License

MIT, see `LICENSE`. Copyright (c) 2026 grenbel.
