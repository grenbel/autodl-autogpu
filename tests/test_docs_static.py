"""Static checks that keep SKILL.md and the reference files in step with ctl and with each other.

Every ctl command and option the documents name must exist in ctl's own parser; SKILL.md has the sections the
console manual points to; the manual sections SKILL.md points to exist; and SKILL.md stays short enough to be read
in one go, without the rules of the first version that the scenario tests showed to be harmful.
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import autodl_ctl as ctl  # noqa: E402

SKILL = ROOT / "SKILL.md"
CONSOLE_MD = ROOT / "reference" / "console.md"
CONSOLE_MORE_MD = ROOT / "reference" / "console-more.md"   # the four sections of the manual that are seldom needed
SSH_MD = ROOT / "reference" / "ssh.md"                     # ctl: connecting, the commands, the alias
GUARD_MD = ROOT / "reference" / "guard.md"                 # the guard on the instance
LEDGER_MD = ROOT / "reference" / "ledger.md"               # the local record: grants, the ledger, calibration
REFERENCES = [CONSOLE_MD, CONSOLE_MORE_MD, SSH_MD, GUARD_MD, LEDGER_MD]


def _text(path: pathlib.Path) -> str:
    return path.read_bytes().decode("utf-8")


def _subparsers(parser) -> dict:
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices


def ctl_commands() -> dict:
    """'arm' -> its parser, ..., 'auth check' -> its parser."""
    top = dict(_subparsers(ctl.build_parser()))
    out = {name: sp for name, sp in top.items() if name != "auth"}
    for name, sp in _subparsers(top["auth"]).items():
        out["auth " + name] = sp
    return out


def options_of(sp) -> set:
    return {o for a in sp._actions for o in a.option_strings if o.startswith("--")}


def code_spans(text: str) -> list:
    """Inline code and the lines of fenced blocks."""
    spans, fenced = [], False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            spans.append(line)
        else:
            spans += re.findall(r"`([^`]+)`", line)
    return spans


LONG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")
# a command runs to the end of the span, or to a shell separator: ; & or a | that stands alone
CTL = re.compile(r"(?:^|[\s;&(（，、：])(?:scripts/)?ctl\s+([a-z][a-z-]*)((?:\s+(?!\|(?:\s|$))[^\s;&]+)*)")


def commands_in(span: str, bare: bool) -> list:
    """(command, options) for each ctl command a code span names. With bare, a span that starts with a
    subcommand's name counts too, as in `arm --rearm`."""
    found = []
    names = ctl_commands()
    tops = {n.split()[0] for n in names}
    for m in CTL.finditer(span):
        found.append((m.group(1), m.group(2)))
    if bare and not found:
        m = re.match(r"([a-z][a-z-]*)(\s.*)?$", span)
        if m and m.group(1) in tops and LONG.search(span):
            found.append((m.group(1), m.group(2) or ""))
    out = []
    for first, rest in found:
        if first == "auth":
            second = re.match(r"\s+([a-z]+)", rest)
            out.append(("auth " + second.group(1) if second else "auth", LONG.findall(rest)))
        else:
            out.append((first, LONG.findall(rest)))
    return out


def _problems(path: pathlib.Path, bare: bool) -> list:
    names = ctl_commands()
    every = set().union(*(options_of(sp) for sp in names.values()))
    bad = []
    for span in code_spans(_text(path)):
        cmds = commands_in(span, bare)
        for cmd, opts in cmds:
            if cmd not in names:
                bad.append(f"{path.name}: no such ctl command: {cmd!r} in `{span}`")
                continue
            for o in opts:
                if o not in options_of(names[cmd]):
                    bad.append(f"{path.name}: ctl {cmd} has no option {o} in `{span}`")
        if bare and not cmds:
            for o in LONG.findall(span):
                if o not in every:
                    bad.append(f"{path.name}: no ctl command has the option {o} in `{span}`")
    return bad


def test_the_parser_helpers_see_what_they_should():
    names = ctl_commands()
    assert "auth check" in names and "arm" in names and "auth" not in names
    assert {"--idle", "--deadline", "--rearm", "--env-setup"} <= options_of(names["arm"])
    assert commands_in("ctl auth check --instance <ID> --mode gpu --hours 2", False) == [
        ("auth check", ["--instance", "--mode", "--hours"])]
    assert commands_in('AT=$(python -c "print(1)") && ctl log off --instance <ID> --at "$AT"', False) == [
        ("log", ["--instance", "--at"])]
    assert commands_in("ctl auth grant --usage both|gpu|nogpu --budget none|<元>yuan --quote '<原话>' | tee x --append",
                       False) == [("auth grant", ["--usage", "--budget", "--quote"])]
    assert commands_in("模式用法在本机的授权记录里（ctl auth show）", False) == [("auth show", [])]
    assert commands_in("arm --rearm", True) == [("arm", ["--rearm"])]
    assert commands_in("arm --rearm", False) == []
    assert commands_in("scripts/ctl", True) == [] and commands_in("ctl", True) == []
    assert code_spans("a `ctl now` b\n```\nctl x --y\n```\n`z`") == ["ctl now", "ctl x --y", "z"]


def test_a_wrong_command_or_option_is_reported(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_bytes("`ctl arm <别名> --util-signal`、`ctl keep <别名> 15m --after-job`、`ctl nonesuch x`、`--no-such-flag`\n"
                    .encode("utf-8"))
    bad = _problems(doc, bare=True)
    assert len(bad) == 4 and "--util-signal" in bad[0] and "--after-job" in bad[1] and "nonesuch" in bad[2] \
        and "--no-such-flag" in bad[3], bad


def test_skill_names_only_ctl_commands_and_options_that_exist():
    assert _problems(SKILL, bare=True) == []


def test_console_manual_names_only_ctl_commands_and_options_that_exist():
    assert _problems(CONSOLE_MD, bare=True) == []
    assert _problems(CONSOLE_MORE_MD, bare=True) == []


def test_the_other_references_name_only_ctl_commands_and_options_that_exist():
    # these files also describe the guard's own commands, whose options differ; only spans that say ctl are checked
    for path in (SSH_MD, GUARD_MD, LEDGER_MD):
        assert _problems(path, bare=False) == []


def _headings(path: pathlib.Path) -> set:
    return set(re.findall(r"^#{2,3} (.+)$", _text(path), re.M))


def test_every_pointer_between_the_documents_finds_its_target():
    """The reference is several files, so that only the part that is needed gets read. A pointer names the file and,
    in quotes, the section; a section named without a file is one of the same file."""
    docs = [SKILL] + REFERENCES
    bad = []
    for doc in docs:
        text = _text(doc)
        for name in re.findall(r"reference/[\w.-]+\.(?:md|js)", text):
            if not (ROOT / name).is_file():
                bad.append(f"{doc.name}: no file {name}")
        for m in re.finditer(r"`reference/([\w.-]+\.md)` 的\"([^\"\n]+)\"", text):
            target = ROOT / "reference" / m.group(1)
            if target.is_file() and m.group(2) not in _headings(target):
                bad.append(f"{doc.name}: {m.group(1)} has no section {m.group(2)!r}")
    # the three files that were one: a quoted name of a section of another of them must come with that file's name
    three = {p: _headings(p) for p in (SSH_MD, GUARD_MD, LEDGER_MD)}
    for doc, own in three.items():
        text = _text(doc)
        for other, theirs in three.items():
            for name in theirs - own:
                for m in re.finditer(re.escape(f"\"{name}\""), text):
                    if not text[:m.start()].endswith(f"`reference/{other.name}` 的"):
                        bad.append(f"{doc.name}: \"{name}\" is a section of {other.name}, and the file is not named")
    assert bad == [], bad
    for path in REFERENCES:                         # each is read in one go (the Read tool returns 25k tokens at most)
        assert len(path.read_bytes()) <= 45000, path.name
    assert not three[SSH_MD] & three[GUARD_MD] and not three[SSH_MD] & three[LEDGER_MD] and not three[GUARD_MD] & three[LEDGER_MD]


def test_skill_has_the_sections_the_console_manual_points_to():
    manual, skill = _text(CONSOLE_MD) + _text(CONSOLE_MORE_MD), _text(SKILL)
    assert "SKILL.md 的开机流程" in manual and "SKILL.md 的出错处理" in manual and "调用方式见 SKILL.md" in manual
    heads = re.findall(r"^## (.+)$", skill, re.M)
    assert "开机流程" in heads and "出错处理" in heads and any("调用" in h for h in heads), heads


# What SKILL.md expects behind each section number of the manual. Renumbering the manual fails here, so the
# numbers SKILL.md cites get looked at again.
MANUAL_SECTIONS = {1: "基本规则", 2: "每次用控制台之前", 3: "调用与返回", 4: "读状态与有没有卡", 5: "取消定时关机",
                   6: "有卡开机", 7: "无卡开机", 8: "定时关机", 9: "结算与查明", 10: "预算的预留", 11: "刷新页面前后",
                   12: "读扣费", 13: "控制台关机", 14: "对不上就停下", 15: "原文与结构备查", 16: "没有浏览器工具时",
                   17: "接手已经开着的实例"}


MOVED_SECTIONS = [13, 15, 16, 17]        # in reference/console-more.md, with their numbers kept


def test_skill_points_only_to_manual_sections_that_exist():
    main = dict((int(n), title) for n, title in re.findall(r"^## (\d+)\. (.+)$", _text(CONSOLE_MD), re.M))
    more = dict((int(n), title) for n, title in re.findall(r"^## (\d+)\. (.+)$", _text(CONSOLE_MORE_MD), re.M))
    assert sorted(more) == MOVED_SECTIONS and not set(main) & set(more)
    where = "第 13、15、16、17 节在 `reference/console-more.md`"      # said where the manual is introduced, in both
    assert where in _text(SKILL) and "`reference/console-more.md`" in _text(CONSOLE_MD).split("\n## 1. ")[0]
    heads = {**main, **more}
    assert sorted(heads) == list(range(1, len(heads) + 1))
    assert sorted(heads) == sorted(MANUAL_SECTIONS)
    for n, word in MANUAL_SECTIONS.items():
        assert word in heads[n], (n, word, heads[n])
    cited = []
    for m in re.finditer(r"手册第 (\d+(?:(?:、| 到 )\d+)*) 节", _text(SKILL)):
        cited += [int(n) for n in re.findall(r"\d+", m.group(1))]
    assert len(cited) >= 8 and all(n in heads for n in cited), cited
    assert "手册第" not in re.sub(r"手册第 \d+(?:(?:、| 到 )\d+)* 节", "", _text(SKILL))   # every citation is in that form


def test_skill_front_matter_and_length():
    skill = _text(SKILL)
    m = re.match(r"---\nname: autodl-gpu\ndescription: (Use when [^\n]+)\n---\n", skill)
    assert m, skill[:200]
    assert len(m.group(0)) <= 1024 and len(m.group(1)) <= 500
    # read in one go: at the 0.41 tokens a byte measured on these files, 36000 bytes are about 14.8k tokens (the Read
    # tool returns 25k at most). It was 30000 until the phase 7 review added the timer before a power-on, the budget
    # baselines and exit 13: things to know before acting, which is why they are not left to the reference files.
    # 35000 until the description named automated experiment runs and the project section named the skill: both
    # decide whether the skill is found at all, so they cannot move to a reference file either. 36000 until the
    # reference was split into five files and a first reader of them was asked twelve situations: three pointers
    # now name the section and not only the file, and an alias the user wrote is looked at before the power-on
    assert len(skill.encode("utf-8")) <= 36500 and skill.count("\n") <= 220
    assert "\r" not in skill and skill.endswith("\n")


def test_the_skill_is_found_when_experiments_run_by_themselves():
    """The skill is for an AI that runs experiments by itself. The description names that situation (a pipeline, another
    experiment-running skill, a request to run the plan), the section the skill writes into a project names the skill,
    and jobs that other workflows start on the instance go through ctl run as well."""
    skill = _text(SKILL)
    desc = re.match(r"---\nname: autodl-gpu\ndescription: (Use when [^\n]+)\n---\n", skill).group(1)
    for needed in ("experiment", "pipeline", "自动跑实验"):
        assert needed in desc, needed
    # the condition comes before the situations it governs: a blind reader took "按计划自动跑实验" on its own as a pull
    assert desc.index("where the project's GPU is an AutoDL instance") < desc.index("pipeline") < desc.index("自动跑实验")
    block = skill.split("```\n## AutoDL\n", 1)[1].split("```", 1)[0]
    assert "- skill: autodl-gpu" in block, block
    run = [line for line in skill.splitlines() if line.startswith("- 长任务一律 `ctl run")]
    assert len(run) == 1 and "别的 skill 或流程" in run[0], run


def test_skill_does_not_bring_back_the_rules_of_the_first_version():
    skill = _text(SKILL)
    for gone in ("封顶", "合计时长上限", "log consent", "gpu_limit", "0.7"):
        assert gone not in skill, gone


def test_the_first_connection_to_a_new_instance_is_written_down():
    """ctl connects in batch mode, so ssh cannot ask whether to trust a host it has not seen: an alias without
    accept-new fails before the login, and `wait` and `check` only say that they did not get through. The reference
    says how an alias is written and how to tell this failure from a dead instance; SKILL.md sends the reader there
    where the alias is written and where a power-on is followed by no connection."""
    ssh_md, skill = _text(SSH_MD), _text(SKILL)
    # the README tells a person only what a person must do; making the key and writing the alias is the reader's work
    for needed in ("ssh-keygen -t ed25519 -N ''", "StrictHostKeyChecking accept-new", "Host key verification failed",
                   "REMOTE HOST IDENTIFICATION HAS CHANGED", "ssh-keygen -R"):
        assert needed in ssh_md, needed
    first_use = skill.split("\n## 第一次使用\n", 1)[1].split("\n- **每个项目。**", 1)[0]
    section = '照 `reference/ssh.md` 的"别名的写法与第一次连接"'
    assert section in first_use, first_use
    # an alias the user wrote earlier is looked at before the power-on: after it, the instance is billed while the
    # reason for the silence is looked for (a reader of the released files found no way to this check before that)
    assert "别名是用户早先写的" in first_use and "头一次开机前" in first_use and "主机密钥设置" in first_use, first_use
    rows = [line for line in skill.splitlines() if line.startswith("| 开机后 `ctl wait` 一直等不到")]
    assert len(rows) == 1 and section in rows[0], rows


def test_what_a_first_reader_of_the_five_files_missed_has_its_pointer():
    """A reader who had never seen the skill answered twelve situations from the released files (2026-10-02). The split
    sent it to no wrong file. These are the places where it had to look around: a file named without the section, a
    word of the manual in a file that did not say what the manual is, and two answers that sat in a neighbouring
    section."""
    skill, ledger, guard, manual = _text(SKILL), _text(LEDGER_MD), _text(GUARD_MD), _text(CONSOLE_MD)
    clock = [line for line in skill.splitlines() if line.startswith("| 退出 11，说本机时钟比记录里的早")]
    assert len(clock) == 1 and '`reference/ledger.md` 的"授权、开机前的关口与账本"' in clock[0], clock
    # the ledger file cites the manual by section number, so it says which files the manual is
    head = ledger.split("\n## ", 1)[0]
    assert '"手册"是 `reference/console.md`' in head and "`reference/console-more.md`" in head, head
    # forgetting the calibrations is done in the local record: it needs no running instance
    forget = [line for line in ledger.splitlines() if "`calibrate 别名 --forget`" in line]
    assert len(forget) == 1 and "不连实例" in forget[0], forget
    # the keys of status are one section; from when the idle time counts after a keep is another
    status = [line for line in guard.splitlines() if line.startswith("- status 一行一个 `key=value`")]
    assert len(status) == 1 and '见"keep、最晚关机、跑完就关、安静期"' in status[0], status
    # which menu item stops a power-on without GPU; the list of the others is kept for reference only
    step = [line for line in manual.splitlines() if line.startswith("1. `menu('实例ID')`")]
    assert len(step) == 1 and '第一项不是"无卡模式开机"也停下' in step[0] and "第 15 节" in step[0], step
