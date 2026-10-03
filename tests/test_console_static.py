"""Static checks for the page scripts that run inside the user's logged-in AutoDL console.

tests/console/skeleton.js is a development tool that reads the page's structure; reference/console.js is the page
script of the skill for the instance list, and reference/clone-page.js the one for the page that creates a cloned
instance. These checks are the first line of defence; the offline runs in the browser (tests/console/skeleton_test.js,
tests/console/run.js and tests/console/clone_run.js) trap the same entries at run time.
"""
import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKELETON = ROOT / "tests" / "console" / "skeleton.js"
CONSOLE = ROOT / "reference" / "console.js"

PASTE = ROOT / "reference" / "console.min.js"   # the copy that is pasted into the page: the code without comments
PASTE_CLONE = ROOT / "reference" / "console-clone.min.js"   # the copy for cloning an instance: the whole code
PAGE_JS = ROOT / "reference" / "clone-page.js"              # the script of the page that creates a cloned instance
PAGE_MIN = ROOT / "reference" / "clone-page.min.js"         # its copy without comments


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / "console" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fn_digest = _load("fn_digest")
paste_copy = _load("paste_copy")

# Entries no page script may name: network, storage, navigation, dynamic code, URL-bearing properties.
NEVER = [r"\bfetch\b", r"\bXMLHttpRequest\b", r"\bsendBeacon\b", r"\bWebSocket\b", r"\bEventSource\b", r"\bWorker\b",
         r"\blocalStorage\b", r"\bsessionStorage\b", r"\bindexedDB\b", r"\bcookie\b", r"\beval\b", r"\bFunction\s*\(",
         r"\bimport\s*\(", r"\bpostMessage\b", r"\bwindow\.open\b", r"\blocation\.(assign|replace|reload|href)\b",
         r"\bhistory\.", r"\bdocument\.write", r"\.submit\s*\(", r"\brequestSubmit\b", r"\bsetTimeout\b", r"\bsetInterval\b",
         r"\bImage\b", r"\bAudio\b", r"\.src\b", r"\.href\b", r"\bsrcset\b", r"\.action\b", r"\bping\b", r"\bformAction\b"]
# Entries that change the page or read values. The skeleton extractor only reads structure, so it names none of them.
WRITES = [r"\binnerHTML\b", r"\bouterHTML\b", r"\binsertAdjacent", r"\bsetAttribute\b", r"\bremoveAttribute\b",
          r"\bcreateElement\b", r"\bappendChild\b", r"\binsertBefore\b", r"\bremoveChild\b", r"\breplaceChild\b",
          r"\.remove\(", r"\.value\b", r"\bdataset\b", r"\bclick\s*\(", r"\bfocus\s*\(", r"\bdispatchEvent\b",
          r"\bscrollIntoView\b", r"\.textContent\b", r"\.innerText\b", r"\.style\.", r"\bclassList\.(add|remove|toggle|replace)\b",
          r"\battachShadow\b", r"\baddEventListener\b"]


def code_of_text(text):
    """The text without full-line // comments (the style these files use)."""
    return "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith("//"))


def code_of(path):
    return code_of_text(path.read_bytes().decode("utf-8"))


def hits(code, patterns):
    return [p for p in patterns if re.search(p, code)]


def test_skeleton_names_nothing_forbidden():
    code = code_of(SKELETON)
    assert hits(code, NEVER) == []
    assert hits(code, WRITES) == []


def test_skeleton_can_be_pasted_verbatim():
    raw = SKELETON.read_bytes()
    assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8")
    assert "`" not in text and "${" not in text


def test_skeleton_is_one_function_expression():
    text = fn_digest.function_text(SKELETON.read_bytes().decode("utf-8"))
    assert text.startswith("function (targetId, mode) {")
    assert SKELETON.read_bytes().endswith(b"})\n")


# console.js reads text and input values. Its effects are a click (the action table's and the clone's own), a focus, and
# a hover or focus event sent through one helper, each in one place.
CONSOLE_WRITES = [w for w in WRITES
                  if w not in (r"\.textContent\b", r"\.value\b", r"\bclick\s*\(", r"\bfocus\s*\(", r"\bdispatchEvent\b")]
ASSIGNMENTS = [r"\.textContent\s*=(?!=)", r"\.value\s*=(?!=)", r"\.nodeValue\s*=(?!=)",
               r"\.(checked|selected|disabled|hidden|id|className|title|name|type|tabIndex)\s*=(?!=)",
               r"\bnew\s+(?!MouseEvent\b|FocusEvent\b)\w*Event\b", r"\binitEvent\b"]


def test_console_names_nothing_forbidden():
    code = code_of(CONSOLE)
    assert hits(code, NEVER) == []
    assert hits(code, CONSOLE_WRITES) == []
    assert hits(code, ASSIGNMENTS) == []


def test_console_has_each_effect_once_and_hovers_only():
    code = code_of(CONSOLE)
    # two clicks: the one of the action table, behind the refusal list, and the clone's own, behind its three fixed texts
    assert len(re.findall(r"\.click\s*\(", code)) == 2
    assert ("function pressClone(el, text) {\n    if (CLONE_CLICKS.indexOf(text) < 0 || textOf(el) !== text) throw new "
            "Error('not a click of the clone');\n    el.click();\n  }") in code
    assert "var CLONE_CLICKS = ['克隆实例新', '数据盘', '继续'];" in code
    assert re.findall(r"\bpressClone\(([^)]*)\)", code)[1:] == ["cands[0], CLONE.item", "x.r.diskLabel, CLONE.disk", "b, CLONE.go"]
    assert len(re.findall(r"\.focus\s*\(", code)) == 1
    assert len(re.findall(r"\bdispatchEvent\b", code)) == 1
    assert len(re.findall(r"\bnew\s+MouseEvent\b", code)) == 1
    assert len(re.findall(r"\bnew\s+FocusEvent\b", code)) == 1
    assert re.search(r"function press\(el\) \{ deny\(textOf\(el\)\); el\.click\(\); \}", code)
    assert re.search(r"function send\(el, e\) \{ el\.dispatchEvent\(e\); \}", code)
    assert re.search(r"function hover\(el, type\) \{ send\(el, new MouseEvent\(type, \{ bubbles: false, cancelable: false, "
                     r"view: window \}\)\); \}", code)
    assert re.search(r"function focusOn\(el\) \{\n    el\.focus\(\{ preventScroll: true \}\);\n    if \(document\.activeElement "
                     r"=== el && !document\.hasFocus\(\)\) send\(el, new FocusEvent\('focus'\)\);\n  \}", code)
    assert len(re.findall(r"\bsend\(", code)) == 3
    calls = re.findall(r"\bhover\(([^)]*)\)", code)
    assert calls[0] == "el, type"
    assert calls[1:] and all(re.fullmatch(r"m\.trigger, '(mouseenter|mouseleave)'", c) for c in calls[1:])


def test_the_older_functions_still_cannot_click_what_the_clone_clicks():
    """The refusal list keeps the words of the clone, and the click of the action table still goes through it."""
    code = code_of(CONSOLE)
    deny = re.search(r"var DENY = \[(.*?)\];", code, re.S).group(1)
    for word in ("克隆", "释放", "扩容"):
        assert f"'{word}'" in deny
    assert code.count("press(") == 1 + 5   # its definition, and the five clicks of the action table and the dialogs
    assert "pressClone" not in re.search(r"function start\(op, id\) \{.*?\n  \}\n", code, re.S).group(0)


def function_body(code, name):
    """The text of a function declared at the script's top level (two spaces in), up to its closing brace."""
    return re.search(r"\n  function %s\([^)]*\) \{\n.*?\n  \}\n" % re.escape(name), code, re.S).group(0)


def test_console_reads_the_page_s_own_data_in_one_function_only():
    """sshAddress is the only function that leaves what the page shows: it walks from the app element to the instance
    table's data and reads three keys of one object. Nothing else in the file names those entries, and the file names
    none of the keys that hold a password, a token or a phone number, not even in a comment."""
    source = CONSOLE.read_bytes().decode("utf-8")
    for name in ("root_password", "jupyter_token", "phone", "token"):
        assert name not in source
    assert source.count("password") == 1 and "input[type=\"password\"]" in source   # how the login page is known
    code = code_of(CONSOLE)
    body = function_body(code, "sshAddress")
    rest = code.replace(body, "\n")
    for entry in (r"_vnode\b", r"\bsubTree\b", r"\.props\b", r"\.component\b", r"\bsuspense\b", r"\buuid\b", r"\bproxy_host\b",
                  r"\bssh_port\b", r"\bssh_command\b", r"__vue", r"\bsetupState\b"):
        assert not re.search(entry, rest), entry
    # of an object of the page's data it reads four keys and no other; no key is reached by a computed name
    assert sorted(set(re.findall(r"\bmine\[0\]\.(\w+)", body))) == ["proxy_host", "ssh_command", "ssh_port"]
    assert re.findall(r"\br\.(\w+)", body) == ["uuid"]
    assert "mine[0][" not in body and "r[" not in body and "Object.keys" not in body and "JSON" not in body
    assert "for (var k in" not in code and "Object.keys" not in code and "Object.entries" not in code


def test_console_writes_one_global_only():
    code = code_of(CONSOLE)
    assert re.findall(r"\bwindow\.(\w+)\s*=(?!=)", code) == ["__autodl"]
    assert re.findall(r"\b(document|location|navigator|history)\.\w+\s*=(?!=)", code) == []
    assert re.findall(r"\b(window|self|globalThis)\[", code) == []


def test_console_can_be_pasted_verbatim():
    raw = CONSOLE.read_bytes()
    assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8")
    assert "`" not in text and "${" not in text


def test_console_is_one_function_expression():
    text = fn_digest.function_text(CONSOLE.read_bytes().decode("utf-8"))
    assert text.startswith("function (mode) {")
    assert CONSOLE.read_bytes().endswith(b"})\n")


# reference/console.md tells the AI how to check the pasted script and which functions to call; it must match the file.
MANUAL = ROOT / "reference" / "console.md"


def console_names():
    """The functions of the script: those of the everyday copy, then those the copy for cloning adds."""
    code = CONSOLE.read_bytes().decode("utf-8")
    base = re.findall(r"'(\w+)'", re.search(r"var NAMES = \[(.*?)\];", code, re.S).group(1))
    more = re.findall(r"'(\w+)'", re.search(r"NAMES = NAMES\.concat\(\[(.*?)\]\);", code, re.S).group(1))
    return base + more


def test_the_copies_that_are_pasted_are_the_script_without_comments_and_indentation():
    """The AI writes the script out for every new tab, so what it pastes carries no comments. Each copy is made from
    the source and by nothing else: the same code, line for line. The everyday copy also leaves out the clone blocks."""
    import pytest
    source = CONSOLE.read_bytes().decode("utf-8")
    for path, basis in ((PASTE_CLONE, source), (PASTE, paste_copy.everyday(source))):
        copy = path.read_bytes()
        assert copy.decode("utf-8") == paste_copy.strip(basis)
        assert b"\r" not in copy and not copy.startswith(b"\xef\xbb\xbf") and copy.endswith(b"})\n")
        assert fn_digest.function_text(copy.decode("utf-8")).startswith("function (mode) {")
        assert len(copy) < 0.8 * len(source.encode("utf-8"))
        # what the stripping takes away is comment and indentation only: line by line the code is the same
        code = [ln.strip() for ln in code_of_text(basis).split("\n") if ln.strip()]
        assert [ln for ln in copy.decode("utf-8").split("\n") if ln] == code
    assert paste_copy.copies(source) == {PASTE: PASTE.read_bytes(), PASTE_CLONE: PASTE_CLONE.read_bytes()}
    for refused in ("var a = `x\n  y`;\n", "/* c */\nvar a = 1;\n", "var a = 'x\\\n  y';\n"):
        with pytest.raises(ValueError):
            paste_copy.strip(refused)


def test_the_everyday_copy_has_nothing_of_the_clone():
    """What only cloning needs is not in the copy pasted every day: no function of the clone, no second click, no
    reading of the page's own data. Its size stays that of the script before the clone was added."""
    import pytest
    source = CONSOLE.read_bytes().decode("utf-8")
    everyday = PASTE.read_bytes().decode("utf-8")
    full = PASTE_CLONE.read_bytes().decode("utf-8")
    assert source.count("// clone-begin\n") == 2 and source.count("// clone-end\n") == 2
    # the word appears only where a page that already holds a copy is asked which copy it is
    assert re.findall(r"\w*[Cc]lone\w*", everyday) == ["clone", "clone"]
    assert "(prior.clone === true || api.clone !== true)" in everyday
    for name in CLONE_NAMES + ["pressClone", "CLONE", "_vnode", "proxy_host", "ssh_port", "ssh_command", "uuid", "克隆实例"]:
        assert name not in everyday, name
        assert name in full, name
    assert len(re.findall(r"\.click\s*\(", everyday)) == 1 and len(re.findall(r"\.click\s*\(", full)) == 2
    assert re.findall(r"'(\w+)'", re.search(r"var NAMES = \[(.*?)\];", everyday, re.S).group(1)) == console_names()[:23]
    # the everyday copy's budget; raised from 34000 for version 9, whose rows also tell their place (the region and the
    # host of the first cell), so that a report can name a row the way the console shows it
    assert len(everyday.encode("utf-8")) < 34500 < len(full.encode("utf-8"))
    assert paste_copy.everyday("a\n  // clone-begin\nb\n  // clone-end\nc\n// clone-begin\nd\n// clone-end\n") == "a\nc\n"
    for bad in ("// clone-begin\n// clone-begin\n// clone-end\n", "a\n// clone-end\n", "// clone-begin\na\n"):
        with pytest.raises(ValueError):
            paste_copy.everyday(bad)


def test_manual_names_the_published_script():
    text = MANUAL.read_bytes().decode("utf-8")
    n, h = fn_digest.digest(PASTE)
    version = re.search(r"var VERSION = (\d+);", CONSOLE.read_bytes().decode("utf-8")).group(1)
    assert f"`reference/console.js` 第 {version} 版" in text
    assert f"`reference/console.min.js`（函数文本 {n} 字节，SHA-256 `{h}`）" in text
    assert f"if (b.length !== {n} || h !== '{h}') return" in text
    assert f"`version: {version}`" in text


def js_blocks():
    return re.findall(r"```js\n(.*?)```", MANUAL.read_bytes().decode("utf-8"), re.S)


def test_manual_has_a_full_paste_and_a_short_loader_that_both_check_before_running():
    """Pasting the script after every reload is slow, so the full paste leaves the checked text in the tab's
    sessionStorage and a short loader takes it from there. Neither runs anything it has not checked."""
    n, h = fn_digest.digest(PASTE)
    check = f"if (b.length !== {n} || h !== '{h}') return"
    key = "'__autodl_console_text'"
    full, loader = js_blocks()[:2]
    # the full paste: the text is kept only once it has passed the check, and before the script runs
    assert full.count(check) == 1 and "/* 这里放 reference/console.min.js 的全文 */" in full
    assert full.index(check) < full.index(f"sessionStorage.setItem({key}, t)") < full.index("var api = fn();")
    # the loader: what was kept is checked again, byte for byte, before it is turned back into the function
    assert loader.count(check) == 1 and "这里放" not in loader
    assert loader.index(f"sessionStorage.getItem({key})") < loader.index(check) < loader.index("(0, eval)('(' + t + ')')")
    assert loader.index("(0, eval)") < loader.index("var api = fn();")
    for block in (full, loader):   # that one key is all the storage either touches, and neither sends anything
        assert re.findall(r"(?:local|session)Storage\.\w+\(([^,)]*)", block) == [key]
        assert hits(block, [p for p in NEVER if p not in (r"\bsessionStorage\b", r"\beval\b")]) == []
        assert len(block.encode("utf-8")) < 1200


CLONE_NAMES = ["startClone", "bindCloneDialog", "tickCloneDataDisk", "continueClone", "idDigests", "findCreated", "sshAddress"]


def test_manual_calls_every_function_of_the_script_and_no_other():
    # the manual is two files: reference/console-more.md holds the sections that are seldom needed. The seven functions
    # of the clone are told in reference/clone.md, the file read only when an instance is cloned.
    text = MANUAL.read_bytes().decode("utf-8") + (MANUAL.parent / "console-more.md").read_bytes().decode("utf-8")
    clone = MANUAL.parent / "clone.md"
    names = console_names()
    assert len(names) == 30 and names[23:] == CLONE_NAMES
    called = set(re.findall(r"`(?:window\.__autodl\.)?(\w+)\(", text))
    assert called - set(names) == set()
    assert [n for n in names[:23] if n not in called] == []
    told = set(re.findall(r"`(?:window\.__autodl\.)?(\w+)\(", clone.read_bytes().decode("utf-8")))
    assert [n for n in CLONE_NAMES if n not in told] == []


def test_function_text_rejects_anything_else():
    import pytest
    for bad in ["var x = 1;\n(function () {})\n", "(function () {}), sideEffect()\n", "(function () {})();\n"]:
        with pytest.raises(ValueError):
            fn_digest.function_text(bad)
    assert fn_digest.function_text("// c\n\n(function (a) { return a; })\n") == "function (a) { return a; }"


def test_the_checks_themselves_catch_forbidden_names():
    sample = ("var a = fetch; el.setAttribute('x', 1); localStorage.getItem('k'); x.value = 1; el.click(); new Image(); "
              "form.submit(); var types = ['submit'];")
    assert hits(sample, NEVER) == [r"\bfetch\b", r"\blocalStorage\b", r"\.submit\s*\(", r"\bImage\b"]
    assert hits(sample, WRITES) == [r"\bsetAttribute\b", r"\.value\b", r"\bclick\s*\("]
    assert code_of_text("  // fetch in a comment\nvar b = 1;") == "var b = 1;"
    sample2 = ("x.textContent = 'a'; y.value = 2; z.value === 3; w.id = 'q'; new KeyboardEvent('keydown'); new MouseEvent('x'); "
               "new FocusEvent('focus');")
    assert hits(sample2, ASSIGNMENTS) == [r"\.textContent\s*=(?!=)", r"\.value\s*=(?!=)",
                                          r"\.(checked|selected|disabled|hidden|id|className|title|name|type|tabIndex)\s*=(?!=)",
                                          r"\bnew\s+(?!MouseEvent\b|FocusEvent\b)\w*Event\b"]
    assert hits("a.value === b.value; c.textContent == d;", ASSIGNMENTS) == []


# ---- reference/clone-page.js, the script of the page that creates a cloned instance ----
PAGE_NAMES = ["page", "tickModel", "pickCount", "hosts", "loadMoreHosts", "pickHost", "expansion", "focusExpansion", "prepareCreate",
              "confirmCreate", "result", "leave"]


def test_clone_page_names_nothing_forbidden():
    code = code_of(PAGE_JS)
    assert hits(code, NEVER) == []
    assert hits(code, CONSOLE_WRITES) == []
    assert hits(code, ASSIGNMENTS) == []
    assert "new MouseEvent" not in code and not re.search(r"\bscroll(Left|To|By|IntoView)\b", code)


def test_clone_page_has_each_effect_once():
    """A click (behind the refusal list), a focus with its event, and one write: the scroll position of the host
    table's body. Each in one place."""
    code = code_of(PAGE_JS)
    assert len(re.findall(r"\.click\s*\(", code)) == 1 and "function press(el) { deny(textOf(el)); el.click(); }" in code
    assert len(re.findall(r"\.focus\s*\(", code)) == 1 and len(re.findall(r"\bdispatchEvent\b", code)) == 1
    assert len(re.findall(r"\bnew\s+FocusEvent\b", code)) == 1
    assert re.findall(r"\.scrollTop\s*=(?!=)", code) == [".scrollTop ="]
    assert "function scrollBody(bw, y) { bw.scrollTop = y; }" in code
    assert re.findall(r"\bscrollBody\(([^;]*)\);", code) == [      # the calls; the definition has no ");"
        "h.bw, h.bw.scrollHeight", "h.bw, h.bw.scrollTop + (r.top - b.top) - Math.max(0, (b.height - r.height) / 2)"]
    deny = re.search(r"var DENY = \[(.*?)\];", code, re.S).group(1)
    console_deny = re.search(r"var DENY = \[(.*?)\];", code_of(CONSOLE), re.S).group(1)
    words = re.findall(r"'([^']+)'", deny)
    assert words == re.findall(r"'([^']+)'", console_deny) + ["包日", "包周", "包月", "包年", "优惠券", "代金券"]


def test_clone_page_reads_only_what_the_page_shows():
    """No page data, nothing of the account: the script never reads the balance, and names no key of the page's own
    data, not even in a comment."""
    source = PAGE_JS.read_bytes().decode("utf-8")
    for name in ("_vnode", "__vue", "subTree", "props", "root_password", "jupyter_token", "phone", "token", "password", "账户余额"):
        assert name not in source, name


def test_clone_page_writes_one_global_only():
    code = code_of(PAGE_JS)
    assert re.findall(r"\bwindow\.(\w+)\s*=(?!=)", code) == ["__autodlClone"]
    assert re.findall(r"\b(document|location|navigator|history)\.\w+\s*=(?!=)", code) == []
    assert re.findall(r"\b(window|self|globalThis)\[", code) == []


def test_clone_page_is_one_function_expression_that_can_be_pasted():
    raw = PAGE_JS.read_bytes()
    assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    assert "`" not in raw.decode("utf-8") and "${" not in raw.decode("utf-8")
    assert fn_digest.function_text(raw.decode("utf-8")).startswith("function (mode) {") and raw.endswith(b"})\n")
    names = re.findall(r"'(\w+)'", re.search(r"var NAMES = \[(.*?)\];", raw.decode("utf-8"), re.S).group(1))
    assert names == PAGE_NAMES


def test_clone_page_copy_is_the_script_without_comments_and_indentation():
    source = PAGE_JS.read_bytes().decode("utf-8")
    copy = PAGE_MIN.read_bytes()
    assert copy.decode("utf-8") == paste_copy.strip(source)
    assert b"\r" not in copy and not copy.startswith(b"\xef\xbb\xbf") and copy.endswith(b"})\n")
    assert fn_digest.function_text(copy.decode("utf-8")).startswith("function (mode) {")
    code = [ln.strip() for ln in code_of_text(source).split("\n") if ln.strip()]
    assert [ln for ln in copy.decode("utf-8").split("\n") if ln] == code
    assert "clone-begin" not in source       # the whole script is for cloning; nothing in it is left out of its copy


CLONE_MD = ROOT / "reference" / "clone.md"


def test_the_clone_manual_calls_every_function_of_the_create_page_script():
    """reference/clone.md tells the create page's script: it names every function of it. The functions it names are
    those of the two scripts and no other."""
    text = CLONE_MD.read_bytes().decode("utf-8")
    told = set(re.findall(r"`(?:window\.__autodl(?:Clone)?\.)?(\w+)\(", text))
    assert [n for n in PAGE_NAMES if n not in told] == []
    assert told - set(PAGE_NAMES) - set(console_names()) == set()


def test_the_clone_manual_has_the_templates_of_the_two_copies_it_pastes():
    """Cloning pastes two scripts: the instance list's copy with the clone's functions, and the create page's. Each has
    a full paste and a short loader like the everyday copy's, with its own key in the tab's sessionStorage, and
    neither runs anything it has not checked."""
    text = CLONE_MD.read_bytes().decode("utf-8")
    blocks = re.findall(r"```js\n(.*?)```", text, re.S)
    assert len(blocks) == 4
    for (full, loader), path, key in ((blocks[0:2], PASTE_CLONE, "'__autodl_console_clone_text'"),
                                      (blocks[2:4], PAGE_MIN, "'__autodl_clone_page_text'")):
        n, h = fn_digest.digest(path)
        check = f"if (b.length !== {n} || h !== '{h}') return"
        assert full.count(check) == 1 and f"/* 这里放 reference/{path.name} 的全文 */" in full
        assert full.index(check) < full.index(f"sessionStorage.setItem({key}, t)") < full.index("var api = fn();")
        assert loader.count(check) == 1 and "这里放" not in loader
        assert loader.index(f"sessionStorage.getItem({key})") < loader.index(check) < loader.index("(0, eval)('(' + t + ')')")
        assert loader.index("(0, eval)") < loader.index("var api = fn();")
        for block in (full, loader):
            assert re.findall(r"(?:local|session)Storage\.\w+\(([^,)]*)", block) == [key]
            assert hits(block, [p for p in NEVER if p not in (r"\bsessionStorage\b", r"\beval\b")]) == []
            assert len(block.encode("utf-8")) < 1200
        assert f"`reference/{path.name}`（函数文本 {n} 字节，SHA-256 `{h}`）" in text
    # the everyday key is another one, so the two copies of the instance list's script never take each other's place
    assert "'__autodl_console_text'" not in "".join(blocks)
    assert "clone: api.clone" in blocks[0] and "clone: api.clone" in blocks[1]
    version = re.search(r"var VERSION = (\d+);", CONSOLE.read_bytes().decode("utf-8")).group(1)
    page_version = re.search(r"var VERSION = (\d+);", PAGE_JS.read_bytes().decode("utf-8")).group(1)
    assert f"`reference/console.js` 第 {version} 版" in text and f"`reference/clone-page.js` 第 {page_version} 版" in text


def test_the_clone_manual_quotes_the_fixed_texts_the_scripts_go_by():
    """The words the scripts hold the console to are written in the manual as well, so that a reader can tell a changed
    console from a mistake of their own."""
    text = CLONE_MD.read_bytes().decode("utf-8")
    code = CONSOLE.read_bytes().decode("utf-8")
    table = re.search(r"var CLONE = \{(.*?)\n  \};", code, re.S).group(1)
    fixed = [s for s in re.findall(r"'([^']+)'", table)]
    assert "克隆实例新" in fixed and "继续" in fixed and len(fixed) >= 12
    for s in fixed:
        assert s in text, s
    for s in ("今天剩余克隆次数：", "源实例有扩容数据盘：5GB 请扩容目标实例数据盘，以防拷贝失败", "创建并开机", "按量计费", "需要扩容", "请选择"):
        assert s in text, s
    head = re.search(r"var HEAD = \[(.*?)\];", PAGE_JS.read_bytes().decode("utf-8"), re.S).group(1)
    for s in re.findall(r"'([^']+)'", head):
        assert s in text, s
