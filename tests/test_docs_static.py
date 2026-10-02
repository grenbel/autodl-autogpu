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
SSH_MD = ROOT / "reference" / "ssh.md"


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


def test_ssh_reference_names_only_ctl_commands_and_options_that_exist():
    # this file also describes the guard's own commands, whose options differ; only spans that say ctl are checked
    assert _problems(SSH_MD, bare=False) == []


def test_skill_has_the_sections_the_console_manual_points_to():
    manual, skill = _text(CONSOLE_MD), _text(SKILL)
    assert "SKILL.md 的开机流程" in manual and "SKILL.md 的出错处理" in manual and "调用方式见 SKILL.md" in manual
    heads = re.findall(r"^## (.+)$", skill, re.M)
    assert "开机流程" in heads and "出错处理" in heads and any("调用" in h for h in heads), heads


# What SKILL.md expects behind each section number of the manual. Renumbering the manual fails here, so the
# numbers SKILL.md cites get looked at again.
MANUAL_SECTIONS = {1: "基本规则", 2: "每次用控制台之前", 3: "调用与返回", 4: "读状态与有没有卡", 5: "取消定时关机",
                   6: "有卡开机", 7: "无卡开机", 8: "定时关机", 9: "结算与查明", 10: "预算的预留", 11: "刷新页面前后",
                   12: "读扣费", 13: "控制台关机", 14: "对不上就停下", 15: "原文与结构备查", 16: "没有浏览器工具时",
                   17: "接手已经开着的实例"}


def test_skill_points_only_to_manual_sections_that_exist():
    heads = dict((int(n), title) for n, title in re.findall(r"^## (\d+)\. (.+)$", _text(CONSOLE_MD), re.M))
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
    # read in one go: at the 0.41 tokens a byte measured on these files, 35000 bytes are about 14.5k tokens (the Read
    # tool returns 25k at most). It was 30000 until the phase 7 review added the timer before a power-on, the budget
    # baselines and exit 13: things to know before acting, which is why they are not left to the reference files
    assert len(skill.encode("utf-8")) <= 35000 and skill.count("\n") <= 220
    assert "\r" not in skill and skill.endswith("\n")


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
    assert "照 `reference/ssh.md`" in first_use, first_use
    rows = [line for line in skill.splitlines() if line.startswith("| 开机后 `ctl wait` 一直等不到")]
    assert len(rows) == 1 and "`reference/ssh.md`" in rows[0], rows
