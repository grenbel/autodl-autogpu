"""The copy of reference/console.js that goes into the page: the same code without its whole-line comments, blank
lines and indentation. The AI has to write the script out once for every new browser tab, so every byte that is not
code costs time there; the source keeps its comments for the people who read it.

  python tests/console/paste_copy.py            write reference/console.min.js from reference/console.js
  python tests/console/paste_copy.py --check    exit 1 when reference/console.min.js is not what the source gives

Nothing else is changed: no renaming, no joining of lines, comments at the end of a code line stay. The stripping
works line by line, so it refuses a source in which a line break could be part of a value (a template literal, a
block comment, a line continued by a backslash). tests/test_console_static.py holds the two files against each other,
and the offline tests in the browser run both.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SOURCE = ROOT / "reference" / "console.js"
COPY = ROOT / "reference" / "console.min.js"


def strip(text: str) -> str:
    lines = text.split("\n")
    if "`" in text or "/*" in text or any(line.rstrip().endswith("\\") for line in lines):
        raise ValueError("a template literal, a block comment or a continued line: stripping line by line is not safe")
    kept = [line.lstrip(" ") for line in lines if line.strip() and not line.strip().startswith("//")]
    return "\n".join(kept) + "\n"


def main(argv) -> int:
    want = strip(SOURCE.read_bytes().decode("utf-8")).encode("utf-8")
    if argv == ["--check"]:
        same = COPY.is_file() and COPY.read_bytes() == want
        print("reference/console.min.js is", "the stripped source" if same else "NOT the stripped source: run this without --check")
        return 0 if same else 1
    if argv:
        print(__doc__)
        return 2
    COPY.write_bytes(want)
    print(f"reference/console.min.js: {len(want)} bytes (the source has {SOURCE.stat().st_size})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
