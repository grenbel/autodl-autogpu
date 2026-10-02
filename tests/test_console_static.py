"""Static checks for the page scripts that run inside the user's logged-in AutoDL console.

tests/console/skeleton.js is a development tool that reads the page's structure; reference/console.js is the page
script of the skill. These checks are the first line of defence; the offline runs in the browser
(tests/console/skeleton_test.js and tests/console/run.js) trap the same entries at run time.
"""
import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKELETON = ROOT / "tests" / "console" / "skeleton.js"
CONSOLE = ROOT / "reference" / "console.js"

_spec = importlib.util.spec_from_file_location("fn_digest", ROOT / "tests" / "console" / "fn_digest.py")
fn_digest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fn_digest)

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


# console.js reads text and input values. Its effects are a click, a focus, and a hover or focus event sent through one
# helper, each in one place.
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
    assert len(re.findall(r"\.click\s*\(", code)) == 1
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
    code = CONSOLE.read_bytes().decode("utf-8")
    return re.findall(r"'(\w+)'", re.search(r"var NAMES = \[(.*?)\];", code, re.S).group(1))


def test_manual_names_the_published_script():
    text = MANUAL.read_bytes().decode("utf-8")
    n, h = fn_digest.digest(CONSOLE)
    version = re.search(r"var VERSION = (\d+);", CONSOLE.read_bytes().decode("utf-8")).group(1)
    assert f"`reference/console.js` 第 {version} 版（函数文本 {n} 字节，SHA-256 `{h}`）" in text
    assert f"if (b.length !== {n} || h !== '{h}') return" in text
    assert f"`version: {version}`" in text


def js_blocks():
    return re.findall(r"```js\n(.*?)```", MANUAL.read_bytes().decode("utf-8"), re.S)


def test_manual_has_a_full_paste_and_a_short_loader_that_both_check_before_running():
    """Pasting 44 KB after every reload is slow, so the full paste leaves the checked text in the tab's
    sessionStorage and a short loader takes it from there. Neither runs anything it has not checked."""
    n, h = fn_digest.digest(CONSOLE)
    check = f"if (b.length !== {n} || h !== '{h}') return"
    key = "'__autodl_console_text'"
    full, loader = js_blocks()[:2]
    # the full paste: the text is kept only once it has passed the check, and before the script runs
    assert full.count(check) == 1 and "/* 这里放 reference/console.js 的全文 */" in full
    assert full.index(check) < full.index(f"sessionStorage.setItem({key}, t)") < full.index("var api = fn();")
    # the loader: what was kept is checked again, byte for byte, before it is turned back into the function
    assert loader.count(check) == 1 and "这里放" not in loader
    assert loader.index(f"sessionStorage.getItem({key})") < loader.index(check) < loader.index("(0, eval)('(' + t + ')')")
    assert loader.index("(0, eval)") < loader.index("var api = fn();")
    for block in (full, loader):   # that one key is all the storage either touches, and neither sends anything
        assert re.findall(r"(?:local|session)Storage\.\w+\(([^,)]*)", block) == [key]
        assert hits(block, [p for p in NEVER if p not in (r"\bsessionStorage\b", r"\beval\b")]) == []
        assert len(block.encode("utf-8")) < 1200


def test_manual_calls_every_function_of_the_script_and_no_other():
    text = MANUAL.read_bytes().decode("utf-8")
    names = console_names()
    called = set(re.findall(r"`(?:window\.__autodl\.)?(\w+)\(", text))
    assert len(names) == 23
    assert called - set(names) == set()
    assert [n for n in names if n not in called] == []


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
