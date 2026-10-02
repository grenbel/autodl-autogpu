"""Run the page script's offline test without pasting anything into a browser.

  python tests/console/run_headless.py [--browser PATH] [--console PATH]

Headless Edge or Chrome loads copies of fixtures.js and run.js and the text of reference/console.js from a temporary
directory, calls __con.runSource(text) and prints the page; the result is read from it. The test runs twice: with the
page focused, and with document.hasFocus() answering false, as in a browser pane that is hidden (the script then sends
the focus event itself). The browser gets an empty profile in the temporary directory, and the page allows no network
(its CSP is default-src 'none').

Exit 0 when both runs pass, 1 when a check fails, 2 when no browser was found or the page gave no result.
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
<title>console.js offline test</title>
</head>
<body>
<script src="fixtures.js"></script>
<script src="run.js"></script>
<script src="src.js"></script>
<script>
if (window.__noFocus) Object.defineProperty(Document.prototype, 'hasFocus', { configurable: true, value: function () { return false; } });
function show(text) {
  var pre = document.createElement('pre');
  pre.id = 'result';
  pre.textContent = text;
  document.body.replaceChildren(pre);
}
window.__con.runSource(window.__src).then(show, function (e) { show(JSON.stringify({ pass: false, error: String(e && e.stack || e) })); });
</script>
</body>
</html>
"""
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


def run_once(browser, console, no_focus):
    work = pathlib.Path(tempfile.mkdtemp(prefix="console-offline-"))
    try:
        for name in ("fixtures.js", "run.js"):
            (work / name).write_bytes((HERE / name).read_bytes())
        text = console.read_bytes().decode("utf-8")
        (work / "src.js").write_bytes(("window.__noFocus = " + ("true" if no_focus else "false") + ";\nwindow.__src = "
                                       + json.dumps(text, ensure_ascii=False) + ";\n").encode("utf-8"))
        (work / "auto.html").write_bytes(PAGE.encode("utf-8"))
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
    for flag in ("--browser", "--console"):
        if flag in args:
            i = args.index(flag)
            given[flag] = args[i + 1]
            del args[i:i + 2]
    if args:
        print(__doc__)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    browser = find_browser(given.get("--browser"))
    if browser is None or not browser.is_file():
        print("no Edge or Chrome found; name one with --browser PATH or AUTODL_TEST_BROWSER")
        return 2
    console = pathlib.Path(given.get("--console") or ROOT / "reference" / "console.js")
    print("browser:", browser)
    worst = 0
    for no_focus in (False, True):
        out, why = run_once(browser, console, no_focus)
        mode = "no focus, as in a hidden pane" if no_focus else "the page has focus"
        if out is None:
            print("[%s] %s" % (mode, why))
            worst = max(worst, 2)
            continue
        failed = out.get("failed") or []
        print("[%s] pass: %s, checks: %s, failed: %d, digest: %s" % (mode, out.get("pass"), out.get("total"), len(failed),
                                                                  json.dumps(out.get("digest"))))
        if out.get("error"):
            print("  error:", out["error"])
        for f in failed:
            print("  - %s :: %s" % (f.get("name"), str(f.get("detail"))[:400]))
        if out.get("pass") is not True:
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
