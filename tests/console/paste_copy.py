"""The copies of the page scripts that go into the page: the same code without its whole-line comments, blank lines and
indentation. The AI has to write a script out once for every new browser tab, so every byte that is not needed costs
time there; the sources keep their comments for the people who read them. reference/console.js gives two copies, and
reference/clone-page.js one:

  reference/console.min.js         the everyday copy. It also leaves out what only cloning an instance needs: the
                                   lines from a comment line "clone-begin" through the next comment line "clone-end"
  reference/console-clone.min.js   the whole code, loaded in place of the everyday copy when an instance is cloned
  reference/clone-page.min.js      the script of the page that creates a cloned instance

  python tests/console/paste_copy.py            write the three copies from the two sources
  python tests/console/paste_copy.py --check    exit 1 when a copy is not what its source gives

Nothing else is changed: no renaming, no joining of lines, comments at the end of a code line stay. The stripping
works line by line, so it refuses a source in which a line break could be part of a value (a template literal, a
block comment, a line continued by a backslash). tests/test_console_static.py holds the files against each other,
and the offline tests in the browser run the source and both copies.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SOURCE = ROOT / "reference" / "console.js"
COPY = ROOT / "reference" / "console.min.js"
CLONE_COPY = ROOT / "reference" / "console-clone.min.js"
PAGE_SOURCE = ROOT / "reference" / "clone-page.js"
PAGE_COPY = ROOT / "reference" / "clone-page.min.js"
BEGIN, END = "// clone-begin", "// clone-end"


def strip(text: str) -> str:
    lines = text.split("\n")
    if "`" in text or "/*" in text or any(line.rstrip().endswith("\\") for line in lines):
        raise ValueError("a template literal, a block comment or a continued line: stripping line by line is not safe")
    kept = [line.lstrip(" ") for line in lines if line.strip() and not line.strip().startswith("//")]
    return "\n".join(kept) + "\n"


def everyday(text: str) -> str:
    """The source without its clone blocks. A block is the lines from a line that is the comment clone-begin through
    the next line that is the comment clone-end; blocks do not nest, and every one is closed."""
    kept, inside = [], False
    for line in text.split("\n"):
        mark = line.strip()
        if mark == BEGIN:
            if inside:
                raise ValueError("clone-begin inside a clone block")
            inside = True
        elif mark == END:
            if not inside:
                raise ValueError("clone-end without a clone-begin")
            inside = False
        elif not inside:
            kept.append(line)
    if inside:
        raise ValueError("a clone block that is not closed")
    return "\n".join(kept)


def copies(text: str) -> dict:
    """What each copy has to hold, as bytes, by its path."""
    return {COPY: strip(everyday(text)).encode("utf-8"), CLONE_COPY: strip(text).encode("utf-8")}


def main(argv) -> int:
    want = copies(SOURCE.read_bytes().decode("utf-8"))
    want[PAGE_COPY] = strip(PAGE_SOURCE.read_bytes().decode("utf-8")).encode("utf-8")
    if argv == ["--check"]:
        worst = 0
        for path, data in want.items():
            same = path.is_file() and path.read_bytes() == data
            print(f"reference/{path.name} is", "the stripped source" if same else "NOT the stripped source: run this without --check")
            worst = worst if same else 1
        return worst
    if argv:
        print(__doc__)
        return 2
    for path, data in want.items():
        path.write_bytes(data)
        source = PAGE_SOURCE if path == PAGE_COPY else SOURCE
        print(f"reference/{path.name}: {len(data)} bytes (the source has {source.stat().st_size})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
