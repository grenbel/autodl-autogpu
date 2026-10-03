"""Run the page scripts' offline tests without pasting anything into a browser.

  python tests/console/run_headless.py [--browser PATH] [--console PATH [--suite console|clone]]

Headless Edge or Chrome loads copies of a fake page (fixtures), of its tests and of the text of a page script from a
temporary directory, calls the tests' runSource(text) and prints the page; the result is read from it. There are two
suites. "console" (fixtures.js, run.js) is for the script of the instance list: reference/console.js, and the two
copies without comments that are pasted into the real page, reference/console.min.js (the everyday copy, which must not
have the functions for cloning an instance) and reference/console-clone.min.js (which must have them, as the source
does). "clone" (clone_fixtures.js, clone_run.js) is for the script of the page that creates a cloned instance:
reference/clone-page.js and its copy without comments reference/clone-page.min.js. All five files are tested unless
--console names one, with --suite saying which suite it belongs to (console when not given).

Each file runs twice: with the page focused, and with document.hasFocus() answering false, as in a browser pane that
is hidden (the script then sends the focus event itself). The browser gets an empty profile in the temporary
directory, and the page allows no network (its CSP is default-src 'none').

Exit 0 when every run passes, 1 when a check fails, 2 when no browser was found or the page gave no result.
The browser is --browser, or the environment variable AUTODL_TEST_BROWSER, or the first one found in the usual places."""
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self' file: 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'">
<title>page script offline test</title>
</head>
<body>
<script src="FIXTURES"></script>
<script src="TESTS"></script>
<script src="src.js"></script>
<script>
if (window.__noFocus) Object.defineProperty(Document.prototype, 'hasFocus', { configurable: true, value: function () { return false; } });
function show(text) {
  var pre = document.createElement('pre');
  pre.id = 'result';
  pre.textContent = text;
  document.body.replaceChildren(pre);
}
window.RUNNER.runSource(window.__src).then(show, function (e) { show(JSON.stringify({ pass: false, error: String(e && e.stack || e) })); });
</script>
</body>
</html>
"""
# suite: the fake page, its tests, and the object the tests register themselves as
SUITES = {"console": ("fixtures.js", "run.js", "__con"), "clone": ("clone_fixtures.js", "clone_run.js", "__ccon")}
NAMES = ["msedge", "microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable", "chromium",
         "chromium-browser", "chrome"]


def candidates():
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        base = os.environ.get(var)
        if base:
            yield pathlib.Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            yield pathlib.Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
    for app in ("Microsoft Edge", "Google Chrome", "Chromium"):
        yield pathlib.Path("/Applications") / (app + ".app") / "Contents" / "MacOS" / app
    for name in NAMES:
        found = shutil.which(name)
        if found:
            yield pathlib.Path(found)


def find_browser(given):
    if given:
        return pathlib.Path(given)
    if os.environ.get("AUTODL_TEST_BROWSER"):
        return pathlib.Path(os.environ["AUTODL_TEST_BROWSER"])
    for p in candidates():
        if p.is_file():
            return p
    return None


def run_once(browser, console, no_focus, clone=None, suite="console"):
    """clone: whether this file has to have the functions for cloning (None: whichever it has; the console suite only)."""
    fixtures, tests, runner = SUITES[suite]
    work = pathlib.Path(tempfile.mkdtemp(prefix="console-offline-"))
    try:
        for name in (fixtures, tests):
            (work / name).write_bytes((HERE / name).read_bytes())
        text = console.read_bytes().decode("utf-8")
        (work / "src.js").write_bytes(("window.__noFocus = " + ("true" if no_focus else "false") + ";\nwindow.__expectClone = "
                                       + json.dumps(clone) + ";\nwindow.__src = "
                                       + json.dumps(text, ensure_ascii=False) + ";\n").encode("utf-8"))
        page = PAGE.replace("FIXTURES", fixtures).replace("TESTS", tests).replace("RUNNER", runner)
        (work / "auto.html").write_bytes(page.encode("utf-8"))
        cmd = [str(browser), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--user-data-dir=" + str(work / "profile"), "--window-size=1600,900",
               "--virtual-time-budget=120000", "--dump-dom", (work / "auto.html").as_uri()]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        m = re.search(r'<pre id="result">(.*?)</pre>', r.stdout.decode("utf-8", "replace"), re.S)
        if not m:
            return None, "the page gave no result (browser exit %d): %s" % (r.returncode, r.stderr.decode("utf-8", "replace")[-600:])
        return json.loads(html.unescape(m.group(1))), ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv):
    args = list(argv)
    given = {}
    for flag in ("--browser", "--console", "--suite"):
        if flag in args:
            i = args.index(flag)
            given[flag] = args[i + 1]
            del args[i:i + 2]
    if args or given.get("--suite", "console") not in SUITES or ("--suite" in given and "--console" not in given):
        print(__doc__)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    browser = find_browser(given.get("--browser"))
    if browser is None or not browser.is_file():
        print("no Edge or Chrome found; name one with --browser PATH or AUTODL_TEST_BROWSER")
        return 2
    if "--console" in given:
        consoles = [(pathlib.Path(given["--console"]), None, given.get("--suite", "console"))]
    else:
        ref = ROOT / "reference"
        consoles = [(ref / "console.js", True, "console"), (ref / "console.min.js", False, "console"),
                    (ref / "console-clone.min.js", True, "console"), (ref / "clone-page.js", None, "clone"),
                    (ref / "clone-page.min.js", None, "clone")]
    print("browser:", browser)
    worst = 0
    for console, clone, suite in consoles:
        for no_focus in (False, True):
            out, why = run_once(browser, console, no_focus, clone, suite)
            mode = "%s; %s" % (console.name, "no focus, as in a hidden pane" if no_focus else "the page has focus")
            if out is None:
                print("[%s] %s" % (mode, why))
                worst = max(worst, 2)
                continue
            failed = out.get("failed") or []
            what = "" if suite == "clone" else ", clone: %s" % out.get("clone")
            print("[%s] pass: %s%s, checks: %s, failed: %d, digest: %s" % (
                mode, out.get("pass"), what, out.get("total"), len(failed), json.dumps(out.get("digest"))))
            if out.get("error"):
                print("  error:", out["error"])
            for f in failed:
                print("  - %s :: %s" % (f.get("name"), str(f.get("detail"))[:400]))
            if out.get("pass") is not True:
                worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
