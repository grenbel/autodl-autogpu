"""Check a supervisord program file before it is put in place: it must parse, hold exactly one
section named SECTION, and that section must have exactly the keys the calibration probe uses.

Usage: python check_ini.py FILE SECTION   (prints "ok" and exits 0, or says what is wrong)
"""
import configparser
import sys

path, section = sys.argv[1], sys.argv[2]
c = configparser.RawConfigParser()
try:
    with open(path, encoding="utf-8") as f:
        c.read_file(f)
except (OSError, configparser.Error) as e:
    sys.exit(f"bad: {e}")
if c.sections() != [section]:
    sys.exit(f"bad: sections {c.sections()}")
keys = sorted(c[section])
if keys != ["autorestart", "autostart", "command", "startretries", "startsecs"]:
    sys.exit(f"bad: keys {keys}")
print("ok")
