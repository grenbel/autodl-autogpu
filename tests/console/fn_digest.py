"""Byte count and SHA-256 of a page script's function text, as Function.prototype.toString returns it in the page.

A page script file is full-line // comments followed by one parenthesised function expression, "(function ... })".
After pasting the file as code, the page computes the same two values from fn.toString(); equal values show that the
function in the page is exactly the one in the file.

Usage: python tests/console/fn_digest.py <file.js>
"""
import hashlib
import pathlib
import sys


def function_text(source):
    lines = source.split("\n")
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("//")):
        lines.pop(0)
    code = "\n".join(lines).rstrip("\n")
    if not (code.startswith("(function") and code.endswith("})")):
        raise ValueError("not one parenthesised function expression")
    return code[1:-1]


def digest(path):
    text = function_text(pathlib.Path(path).read_bytes().decode("utf-8"))
    data = text.encode("utf-8")
    return len(data), hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python tests/console/fn_digest.py <file.js>")
    n, h = digest(sys.argv[1])
    print(f"{n} {h}")
