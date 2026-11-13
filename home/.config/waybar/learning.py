#!/usr/bin/env python3
# Waybar module: Learning, a course for each scripting language (the cap in the gear drawer).
#   learning.py                      Prints the button (just the cap icon).
#   learning.py --hub                Opens the language picker, with your progress in each.
#   learning.py --popup <language>   Opens one language's course (luau, python, ...).
#   learning.py --check <test id> [--course <language>]   Marks a test from the terminal.
#
# Each course is lessons (with quick-check questions) and chapter tests you write in the panel.
# Tests are marked on this computer by running your code with that language's interpreter.
# Luau keeps its original home: luau-course/, luau-course-progress.json and luau-course-work/,
# and runs with stand-ins for Roblox (luau-course/mock.luau) so Studio isn't needed.
# Every other language lives in learning/<language>/ (content), learning-progress/<language>.json
# and learning-work/<language>/ (your code). Tests there use the "program" runner: your file is
# run as a program, with cases that check what it prints (see run_program_check).
import datetime
import json
import math
import os
import random
import re
import select
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LUAU_HOME = HERE / "luau-course"
HARNESS_PATH = LUAU_HOME / "harness.luau"
MOCK_PATH = LUAU_HOME / "mock.luau"
SOUNDS = LUAU_HOME / "sounds"
# Set by use_language() for the course that's open
CONTENT = LUAU_HOME
PROGRESS_PATH = HERE / "luau-course-progress.json"
WORK = HERE / "luau-course-work"
LANG = None
PROGRAM_SECONDS = 10  # A program check that runs longer than this is stuck
SCRIPT_CLASSES = ("Script", "LocalScript", "ModuleScript")
LUAU_SECONDS = 30  # A check that runs longer than this is stuck in a loop that never waits
REFRESH_SIGNAL = 9  # Waybar's "signal" for this module, so the button updates straight away
REVIEW_QUESTIONS = 5
LESSON_WEIGHT = 0.4  # How much lessons count toward the course percentage. Tests count for the rest
CAP_ICON = ""
FIRE_ICON = ""
ICON_FONT = "Font Awesome 7 Free Solid"

CHECK_TEXT = "Checked on this computer by running your code with Luau."


def token_pattern(keywords, comment, strings=r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''):
    # The same four groups as LUAU_TOKEN, for the editor's and examples' colouring
    return re.compile(
        rf"(?P<comment>{comment})|(?P<string>{strings})"
        r"|(?P<number>\b(?:0[xX][0-9a-fA-F_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)\b)"
        rf"|(?P<keyword>\b(?:{'|'.join(keywords.split())})\b)",
        re.S,
    )


C_COMMENT = r"//[^\n]*|/\*.*?\*/"
HASH_COMMENT = r"#[^\n]*"
TRIPLE = r'"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\''
TEMPLATE = r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`'

# Every language in the hub. "install" is how to get its interpreter when it's missing.
# A language shows as "Coming soon" until learning/<id>/course.json exists.
LANGUAGES = {
    "luau": {"name": "Luau", "badge": "Lu", "colour": "#00a2ff", "blurb": "Roblox scripting",
             "command": ["luau"], "ext": ".luau", "comment": "--", "runner": "roblox"},
    "python": {"name": "Python", "badge": "Py", "colour": "#3776ab", "blurb": "Automation, data, the all-rounder",
               "command": ["python3", "-u"], "ext": ".py", "comment": "#",
               "keywords": "False None True and as assert async await break class continue def del elif else "
                           "except finally for from global if import in is lambda nonlocal not or pass raise "
                           "return try while with yield match case",
               "comments": HASH_COMMENT, "strings": TRIPLE + "|" + r'[fFrRbB]?"(?:\\.|[^"\\\n])*"|[fFrRbB]?\'(?:\\.|[^\'\\\n])*\'',
               "explanations": [
                   (r"was never closed", "A bracket or quote was opened and never closed. Find the `(`, `[`, `{` or quote on that line (or the one above) and close it."),
                   (r"IndentationError|unexpected indent|expected an indented block", "The spaces at the start of a line are wrong. Lines inside an if, loop or function need four more spaces than the line with the colon, and lines at the same level need the same spaces."),
                   (r"expected ':'", "A line starting with if, elif, else, for, while, def or class must end with a colon."),
                   (r"NameError: name '(\w+)' is not defined", "That name doesn't exist yet. Check its spelling and capitals, that it's defined above this line, and that text has quotes around it."),
                   (r"can only concatenate str|unsupported operand type\(s\) for \+: 'int' and 'str'", "You joined text and a number with `+`. Use an f-string, or `str(...)` around the number."),
                   (r"invalid literal for int\(\) with base 10", "`int(...)` was given text that isn't a whole number. Check what's being converted, and `.strip()` it first."),
                   (r"could not convert string to float", "`float(...)` was given text that isn't a number."),
                   (r"IndexError", "You used an index past the end. Remember indexes start at 0, so the last one is `len(x) - 1`, or `-1`."),
                   (r"KeyError", "That key isn't in the dictionary. Check with `in` first, or use `.get(key, default)`."),
                   (r"'NoneType' object", "Something is `None` where you expected a value. Often a function that prints instead of returning, or a method like `.sort()` whose result was assigned."),
                   (r"ZeroDivisionError", "You divided by zero. Check the divisor first, for example whether a list is empty before averaging it."),
                   (r"takes \d+ positional arguments? but \d+ (were|was) given|missing \d+ required positional argument", "A function was called with the wrong number of arguments. Compare the call with the `def` line."),
               ]},
    "javascript": {"name": "JavaScript", "badge": "JS", "colour": "#d4b40a", "blurb": "Websites, Discord bots, Node",
                   "command": ["node"], "ext": ".js", "comment": "//", "install": "sudo pacman -S nodejs",
                   "keywords": "async await break case catch class const continue default delete do else export "
                               "extends false finally for function if import in instanceof let new null of return "
                               "static super switch this throw true try typeof undefined var void while yield",
                   "comments": C_COMMENT, "strings": TEMPLATE},
    "typescript": {"name": "TypeScript", "badge": "TS", "colour": "#3178c6", "blurb": "JavaScript with types",
                   "command": ["node"], "ext": ".ts", "comment": "//", "install": "sudo pacman -S nodejs",
                   "keywords": "abstract any as async await boolean break case catch class const continue default "
                               "do else enum export extends false finally for function if implements import in "
                               "interface instanceof let never new null number of private protected public readonly "
                               "return static string super switch this throw true try type typeof undefined "
                               "unknown var void while",
                   "comments": C_COMMENT, "strings": TEMPLATE},
    "lua": {"name": "Lua", "badge": "Lua", "colour": "#2c2d72", "blurb": "Plain Lua 5.4: games, mods, configs",
            "command": ["lua5.4"], "ext": ".lua", "comment": "--", "install": "sudo pacman -S lua",
            "keywords": "and break do else elseif end false for function goto if in local nil not or repeat "
                        "return then true until while",
            "comments": r"--\[(?P<ce>=*)\[.*?\](?P=ce)\]|--[^\n]*",
            "strings": r'\[(?P<se>=*)\[.*?\](?P=se)\]|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''},
    "bash": {"name": "Bash", "badge": "sh", "colour": "#2f9e44", "blurb": "Your terminal and Linux scripts",
             "command": ["bash"], "ext": ".sh", "comment": "#",
             "keywords": "if then else elif fi for while until do done case esac in function return local "
                         "export echo printf read declare set unset shift exit break continue true false",
             "comments": r"(?<![$\w])#[^\n]*"},
    "powershell": {"name": "PowerShell", "badge": "PS", "colour": "#2671be", "blurb": "Windows and cross-platform admin",
                   "command": ["pwsh", "-NoProfile", "-NonInteractive", "-File"], "ext": ".ps1", "comment": "#",
                   "install": "From the AUR: yay -S powershell-bin",
                   "keywords": "begin break catch class continue do else elseif end exit filter finally for "
                               "foreach function if in param process return switch throw trap try until while",
                   "comments": r"<#.*?#>|#[^\n]*"},
    "ruby": {"name": "Ruby", "badge": "rb", "colour": "#cc342d", "blurb": "Friendly scripting, Rails",
             "command": ["ruby"], "ext": ".rb", "comment": "#", "install": "sudo pacman -S ruby",
             "keywords": "BEGIN END alias and begin break case class def defined do else elsif end ensure false "
                         "for if in module next nil not or redo rescue retry return self super then true undef "
                         "unless until when while yield puts",
             "comments": HASH_COMMENT},
    "php": {"name": "PHP", "badge": "php", "colour": "#777bb4", "blurb": "Web servers and WordPress",
            "command": ["php"], "ext": ".php", "comment": "//", "install": "sudo pacman -S php",
            "keywords": "abstract and array as break case catch class const continue default do echo else "
                        "elseif empty extends false final finally fn for foreach function if implements isset "
                        "match namespace new null or print private protected public return static switch throw "
                        "true try use var while",
            "comments": C_COMMENT + "|" + HASH_COMMENT},
    "gdscript": {"name": "GDScript", "badge": "GD", "colour": "#478cbf", "blurb": "Godot game engine",
                 "command": ["godot", "--headless", "--quiet", "--script"], "ext": ".gd", "comment": "#",
                 "install": "sudo pacman -S godot",
                 "keywords": "and as break class class_name const continue elif else enum extends false for func "
                             "if in is match not null or pass return self signal static true var void while",
                 "comments": HASH_COMMENT},
    "csharp": {"name": "C#", "badge": "C#", "colour": "#9b4f96", "blurb": "Unity games and .NET apps",
               "command": ["dotnet", "run"], "ext": ".cs", "comment": "//", "install": "sudo pacman -S dotnet-sdk",
               "keywords": "abstract as base bool break case catch char class const continue decimal default do "
                           "double else enum false finally float for foreach if in int interface internal is "
                           "long namespace new null object out override private protected public readonly ref "
                           "return static string struct switch this throw true try using var virtual void while",
               "comments": C_COMMENT, "strings": r'\$?@?"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''},
}
LEARNING = HERE / "learning"
LEARNING_PROGRESS = HERE / "learning-progress"
LEARNING_WORK = HERE / "learning-work"
LAST_LANGUAGE = LEARNING_PROGRESS / "last.txt"


def language_paths(language):
    """(content folder, progress file, work folder) for a language."""
    if language == "luau":
        return LUAU_HOME, HERE / "luau-course-progress.json", HERE / "luau-course-work"
    return LEARNING / language, LEARNING_PROGRESS / f"{language}.json", LEARNING_WORK / language


def language_ready(language):
    return (language_paths(language)[0] / "course.json").exists()


def interpreter_missing(language):
    import shutil
    return shutil.which(LANGUAGES[language]["command"][0]) is None


def use_language(language):
    """Points the course code at one language: its content, progress, work folder and colours."""
    global CONTENT, PROGRESS_PATH, WORK, LANG, TOKENS, CHECK_TEXT
    CONTENT, PROGRESS_PATH, WORK = language_paths(language)
    LANG = dict(LANGUAGES[language], id=language)
    if language == "luau":
        TOKENS = LUAU_TOKEN
    else:
        TOKENS = token_pattern(LANG.get("keywords", ""), LANG.get("comments", HASH_COMMENT),
                               LANG.get("strings", r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''))
    CHECK_TEXT = f"Checked on this computer by running your code with {LANG['name']}."


# Content and progress -----------------------------------------------------------------------

def load_course():
    course = json.loads((CONTENT / "course.json").read_text())
    lessons, tests = {}, {}
    for path in sorted((CONTENT / "lessons").glob("*.json")):
        for lesson in json.loads(path.read_text())["lessons"]:
            lessons[lesson["id"]] = lesson
    for path in sorted((CONTENT / "tests").glob("*.json")):
        test = json.loads(path.read_text())
        tests[test["id"]] = test
    course["lesson_map"] = lessons
    course["test_map"] = tests
    course.setdefault("final", {"title": "Final project", "tests": []})
    return course


def all_lesson_ids(course):
    return [lesson for chapter in course["chapters"] for lesson in chapter["lessons"]]


def all_test_ids(course):
    return [chapter["test"] for chapter in course["chapters"]] + list(course["final"]["tests"])


def load_progress():
    try:
        progress = json.loads(PROGRESS_PATH.read_text())
    except (OSError, ValueError):
        progress = {}
    progress.setdefault("version", 1)
    for key in ("lessons", "quiz", "tests", "review"):
        progress.setdefault(key, {})
    progress.setdefault("days", [])
    return progress


def save_progress(progress):
    temporary = PROGRESS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(progress, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, PROGRESS_PATH)
    try:
        subprocess.run(["pkill", f"-RTMIN+{REFRESH_SIGNAL}", "waybar"], check=False)
    except OSError:
        pass


def today():
    return datetime.date.today().isoformat()


def mark_active(progress):
    if today() not in progress["days"]:
        progress["days"].append(today())


def streak(progress):
    # Days in a row with practice. Today only breaks it once today is over
    days = set(progress["days"])
    day = datetime.date.today()
    if day.isoformat() not in days:
        day -= datetime.timedelta(days=1)
    count = 0
    while day.isoformat() in days:
        count += 1
        day -= datetime.timedelta(days=1)
    return count


def test_passed(progress, test_id):
    return bool(progress["tests"].get(test_id, {}).get("passed"))


def lesson_done(progress, lesson_id):
    return bool(progress["lessons"].get(lesson_id, {}).get("done"))


def chapter_unlocked(course, progress, index):
    # The first chapter is always open. Each later one opens when the previous test passes
    if index == 0:
        return True
    return test_passed(progress, course["chapters"][index - 1]["test"])


def final_unlocked(course, progress):
    return test_passed(progress, course["chapters"][-1]["test"])


def course_fraction(course, progress):
    lessons = all_lesson_ids(course)
    tests = all_test_ids(course)
    lesson_part = sum(lesson_done(progress, l) for l in lessons) / max(len(lessons), 1)
    test_part = sum(test_passed(progress, t) for t in tests) / max(len(tests), 1)
    return LESSON_WEIGHT * lesson_part + (1 - LESSON_WEIGHT) * test_part


def print_button():
    # Just the cap icon on the bar; the percentage and streak are inside the course window
    text = f"<span font_family='{ICON_FONT}'>{CAP_ICON}</span>"
    print(json.dumps({"text": text, "class": "course"}))


# Static checks ------------------------------------------------------------------------------

LONG_BRACKET = re.compile(r"\[(=*)\[")


def strip_luau(source, keep_strings=False):
    # Blanks out comments, and string contents unless keep_strings, keeping every line in place.
    # Interpolated strings keep their {expressions}, since those are code
    out = list(source)
    length = len(source)

    def blank(start, end):
        for index in range(start, min(end, length)):
            if out[index] != "\n":
                out[index] = " "

    def long_end(start):
        match = LONG_BRACKET.match(source, start)
        close = "]" + match.group(1) + "]"
        end = source.find(close, match.end())
        return length if end == -1 else end + len(close)

    index = 0
    while index < length:
        if source.startswith("--", index):
            if LONG_BRACKET.match(source, index + 2):
                end = long_end(index + 2)
            else:
                end = source.find("\n", index)
                end = length if end == -1 else end
            blank(index, end)
            index = end
        elif LONG_BRACKET.match(source, index):
            end = long_end(index)
            if not keep_strings:
                blank(index + 1, end - 1)
            index = end
        elif source[index] in "\"'":
            quote = source[index]
            end = index + 1
            while end < length and source[end] not in (quote, "\n"):
                end += 2 if source[end] == "\\" else 1
            end = min(end + 1, length)
            if not keep_strings:
                blank(index + 1, end - 1)
            index = end
        elif source[index] == "`":
            end = index + 1
            while end < length and source[end] != "`":
                if source[end] == "\\":
                    if not keep_strings:
                        blank(end, end + 2)
                    end += 2
                elif source[end] == "{":
                    depth, end = 1, end + 1
                    while end < length and depth:
                        depth += {"{": 1, "}": -1}.get(source[end], 0)
                        end += 1
                else:
                    if not keep_strings:
                        blank(end, end + 1)
                    end += 1
            index = end + 1
        else:
            index += 1
    return "".join(out)


def run_static(test, items):
    # Returns a list of (requirement id, passed, detail) and whether anything is missing
    by_path = {item["path"]: item for item in items}
    outcomes = []
    missing = False
    for rule in test.get("static", []):
        kind, path, rule_id = rule["type"], rule["path"], rule["id"]
        item = by_path.get(path)
        if kind == "exists":
            classes = rule["class"].split("|") if rule.get("class") else None
            if item is None:
                missing = True
                outcomes.append((rule_id, False, rule.get("fail") or f"Couldn't find `{path}`."))
            elif classes and item["class"] not in classes:
                missing = True
                wanted = " or ".join(classes)
                outcomes.append((rule_id, False, rule.get("fail")
                                 or f"`{path}` is a {item['class']}, but it needs to be a {wanted}."))
            else:
                outcomes.append((rule_id, True, None))
        elif kind in ("uses", "avoids"):
            if item is None or "source" not in item:
                outcomes.append((rule_id, False, f"Couldn't find the script `{path}`."))
                continue
            code = strip_luau(item["source"], keep_strings=rule.get("keep_strings", False))
            count = len(re.findall(rule["pattern"], code, re.MULTILINE))
            ok = count >= rule.get("min", 1) if kind == "uses" else count == 0
            outcomes.append((rule_id, ok, None if ok else rule.get("fail") or "This requirement wasn't met."))
    return outcomes, missing


# Building the checker chunk -----------------------------------------------------------------

def lua_string(text):
    parts = ['"']
    for character in text:
        code = ord(character)
        if character == '"':
            parts.append('\\"')
        elif character == "\\":
            parts.append("\\\\")
        elif character == "\n":
            parts.append("\\n")
        elif code < 32 or code == 127:
            parts.append("\\%03d" % code)
        else:
            parts.append(character)
    parts.append('"')
    return "".join(parts)


def lua_long_string(text):
    level = 0
    while f"]{'=' * level}]" in text + "]":
        level += 1
    return f"[{'=' * level}[\n{text}]{'=' * level}]"


def build_chunk(test, platform="studio", mock=None, entries=None):
    limit = int(test.get("timeout", 20))
    parts = [
        f"local __RUNNER = {lua_string(test['runner'])}\n"
        f"local __TEST = {lua_string(test['id'])}\n"
        f"local __LIMIT = {limit}\n"
        f"local __PLATFORM = {lua_string(platform)}\n"
    ]
    if platform == "offline":
        parts.append(mock)
        rows = []
        for entry in entries:
            fields = [f"path = {lua_string(entry['path'])}", f"class = {lua_string(entry['class'])}"]
            if "source" in entry:
                fields.append(f"source = {lua_long_string(entry['source'])}")
            rows.append("\t{ " + ", ".join(fields) + " },")
        parts.append("__OFFLINE.build({\n" + "\n".join(rows) + "\n})\n")
    parts.append(HARNESS_PATH.read_text())
    parts.append("local function __check(Course)\n" + test["check"].rstrip() + "\nend\n")
    if platform == "offline":
        parts.append("__OFFLINE.run(function()\n\tprint(__finish(xpcall(__check, __traceback, Course)))\nend)\n")
    else:
        parts.append("return __finish(xpcall(__check, __traceback, Course))\n")
    return "\n".join(parts)


# Talking to Roblox Studio -------------------------------------------------------------------

def test_files(test):
    # The scripts a test needs, one editor tab each. Program tests list them under "files";
    # Luau tests take them from their static rules
    if test.get("runner") == "program":
        main = f"main{LANG['ext']}"
        return [{"path": f.get("name", main), "class": f.get("about", LANG["name"]), "name": f.get("name", main),
                 "starter": f.get("starter", "")} for f in test.get("files") or [{"name": main}]]
    files, seen = [], set()
    for rule in test.get("static", []):
        if rule["type"] == "exists" and rule.get("class") in SCRIPT_CLASSES and rule["path"] not in seen:
            seen.add(rule["path"])
            files.append({"path": rule["path"], "class": rule["class"], "name": rule["path"].split(".")[-1]})
    return files


def code_path(test_id, name):
    return WORK / test_id / (name if LANG and LANG["id"] != "luau" else f"{name}.luau")


def read_code(test_id, name):
    try:
        return code_path(test_id, name).read_text()
    except OSError:
        return ""


def write_code(test_id, name, text):
    path = code_path(test_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def place_items(test):
    return [{"path": f["path"], "class": f["class"], "source": read_code(test["id"], f["name"])}
            for f in test_files(test)]


def run_luau(chunk):
    # Returns (report, problem). The report is the JSON line the checker prints last
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".luau", delete=False) as handle:
        handle.write(chunk)
        name = handle.name
    try:
        result = subprocess.run(["luau", name], capture_output=True, text=True, timeout=LUAU_SECONDS)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as problem:
        return None, f"couldn't start luau ({problem})"
    finally:
        Path(name).unlink(missing_ok=True)
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line), None
            except ValueError:
                continue
    return None, (result.stderr or result.stdout)[-1500:] or f"luau exited with code {result.returncode}"


def failure(message, detail=None):
    return {"ran": False, "passed": False, "message": message, "rows": [], "notes": [], "detail": detail}


def merge(test, static_outcomes, report, skipped):
    outcomes = {}
    for rule_id, passed, detail in static_outcomes:
        entry = outcomes.setdefault(rule_id, {"passed": True, "details": []})
        entry["passed"] = entry["passed"] and passed
        if detail and not passed:
            entry["details"].append(detail)
    for result in (report or {}).get("results", []):
        entry = outcomes.setdefault(str(result.get("id")), {"passed": True, "details": []})
        entry["passed"] = entry["passed"] and bool(result.get("passed"))
        for detail in result.get("details") or []:
            if not detail.get("passed"):
                entry["details"].append(detail.get("text", ""))
    # A syntax error stops the whole script, so other failures are only knock-on effects of it
    syntax = next((d for entry in outcomes.values() for d in entry["details"] if "syntax error" in d), None)
    if syntax:
        for entry in outcomes.values():
            if not entry["passed"] and not any("syntax error" in d for d in entry["details"]):
                entry["details"] = ["Not checked: your code has a syntax error, so none of it ran. Fix that first "
                                    "(see the message on the requirement above), then check again."]
    rows = []
    for requirement in test["requirements"]:
        entry = outcomes.get(requirement["id"])
        if entry is None:
            status = "unchecked"
            details = [skipped or "The checker didn't report on this one. That's a problem with the course, not your code."]
        else:
            status = "pass" if entry["passed"] else "fail"
            details = entry["details"] if status == "fail" else []
        rows.append({"id": requirement["id"], "text": requirement["text"], "status": status, "details": details})
    passed = all(row["status"] == "pass" for row in rows)
    met = sum(row["status"] == "pass" for row in rows)
    notes = list((report or {}).get("notes") or [])
    problem = (report or {}).get("error")
    if problem:
        notes.append("The checker hit an error, so some results may be missing. That's a problem with the course, not your code.")
    if passed:
        message = "Passed. Every requirement is met."
    else:
        message = f"{met} of {len(rows)} requirements met. Fix the ones marked below, then check again."
    return {"ran": True, "passed": passed, "message": message, "rows": rows, "notes": notes, "detail": problem}


# Luau's error wording, and what it means in plain English
EXPLANATIONS = [
    (r"Malformed number", "Luau found something that starts like a number but isn't one, such as `6x8`. "
     "Numbers can't have letters stuck to them. To multiply, use `*`, so `6 * 8`."),
    (r"Malformed string", "A string was started with a quote mark but never closed. Every `\"` needs a matching `\"` "
     "on the same line."),
    (r"Expected 'end'", "A block (an `if`, a loop or a function) is missing its `end`. Each one needs exactly one."),
    (r"Expected '\)'", "Luau expected a closing bracket here. Check every `(` has a matching `)`, and that values are "
     "separated by commas or joined with an operator like `+`, `*` or `..`."),
    (r"Expected identifier", "Something is missing in the middle of an expression, for example a value after an "
     "operator or a name after `local`."),
    (r"Incomplete statement", "This line isn't something Luau can run. Often it's a typo, or a value on its own without "
     "`print`, an assignment or a function call."),
    (r"attempt to call a nil value", "You called a function that doesn't exist. Check the spelling and the capitals "
     "of its name, and that it's defined above this line."),
    (r"attempt to perform arithmetic.*nil", "You did maths with something that's `nil`, usually a misspelt variable "
     "name or one that was never given a value."),
    (r"attempt to perform arithmetic.*(string|table|boolean)", "You did maths with something that isn't a number."),
    (r"attempt to concatenate", "Joining with `..` only works on strings and numbers. Wrap other values in "
     "`tostring` first."),
    (r"attempt to index nil", "You used a dot or square brackets on something that's `nil`, so there's nothing to "
     "look inside. Check the name, and that it was set first."),
    (r"attempt to compare", "You compared two values of different types, like a number and a string."),
]


def explain(detail, sources):
    # Adds the line of the learner's code an error points at, and what the error means
    match = re.search(r"line (\d+) of (\w+)", detail) or re.search(r"Course\.\w+\.(\w+):(\d+):", detail)
    extra = []
    if match:
        number, name = (match.group(1), match.group(2)) if "line" in match.group(0) else (match.group(2), match.group(1))
        lines = sources.get(name, "").split("\n")
        index = int(number) - 1
        if 0 <= index < len(lines) and lines[index].strip():
            extra.append(f"Line {number} of {name}: `{lines[index].strip().replace('`', '')}`")
    for pattern, meaning in (EXPLANATIONS if not LANG or LANG["id"] == "luau" else LANG.get("explanations", [])):
        if re.search(pattern, detail):
            extra.append(meaning)
            break
    near = near_miss(detail)
    if near:
        extra.append(near)
    return detail if not extra else detail + "\n\n" + "\n\n".join(extra)


def near_miss(detail):
    # When a wanted line is almost there, say exactly what's different about it
    wanted = re.search(r"expected (?:a line )?`([^`]*)`", detail)
    printed = detail.split("printed", 1)
    if not wanted or len(printed) < 2 or ":" not in wanted.group(1):
        return None
    label, value = (part.strip() for part in wanted.group(1).split(":", 1))
    for line in re.findall(r'"((?:[^"\\]|\\.)*)"', printed[1]):
        if ":" not in line:
            continue
        got_label, got_value = (part.strip() for part in line.split(":", 1))
        if got_label.lower() != label.lower():
            continue
        problems = []
        if got_label != label:
            problems.append(f"the label should be `{label}`, not `{got_label}` (capital letters count)")
        if got_value != value:
            problem = f"the value is `{got_value}` but should be `{value}`"
            try:
                if float(got_value) != float(value) and int(float(got_value) // 1) == int(value):
                    problem += ". Round it down with `//` instead of `/`, or with `math.floor`"
            except ValueError:
                pass
            problems.append(problem)
        if problems:
            return f"Close! Your line `{line}` is nearly right: " + "; and ".join(problems) + "."
    return None


def explain_result(test, result):
    sources = {f["name"]: read_code(test["id"], f["name"]) for f in test_files(test)}
    for row in result.get("rows", []):
        details = row["details"]
        if len(details) > 1 and all(d.startswith("With ") for d in details):
            # Every set of test numbers usually fails the same way, so show one and count the rest
            others = len(details) - 1
            details = [details[0], f"The same problem shows up with {others} other set{'s' if others > 1 else ''} of numbers."]
        row["details"] = [explain(detail, sources) for detail in details]
    return result


def run_check(test, report=lambda text: None):
    if test.get("runner") == "program":
        return run_program_check(test, report)
    report("Running your code...")
    items = place_items(test)
    static_outcomes, _missing = run_static(test, items)
    result, problem = run_luau(build_chunk(test, "offline", MOCK_PATH.read_text(), items))
    if problem == "timeout":
        return failure("Your code was still running after 30 seconds. A loop that never waits, or never ends, "
                       "is the usual cause.")
    if result is None:
        return failure("The checker couldn't finish. That's a problem with the course, not your code.", problem)
    return explain_result(test, merge(test, static_outcomes, result, None))


def run_file(test, file):
    # Runs one tab's script like Studio would and returns its output as (kind, text) lines
    if test.get("runner") == "program":
        return run_program_file(test, file)
    path = lua_string(file["path"])
    if file["class"] == "ModuleScript":
        body = (f"local value, info = Course.load({path})\n"
                "for _, line in info.output do Course.note(line.kind .. \"\\t\" .. line.text) end\n"
                "if info.error then Course.note(\"error\\t\" .. info.error) "
                "else Course.note(\"info\\tThe module returned \" .. Course.show(value)) end\n")
    else:
        body = (f"local info = Course.run({path}, {{ timeout = 10 }})\n"
                "for _, line in info.output do Course.note(line.kind .. \"\\t\" .. line.text) end\n"
                "if info.error then Course.note(\"error\\t\" .. info.error) end\n")
    script = {"id": test["id"], "runner": "edit", "timeout": 20, "check": body}
    result, problem = run_luau(build_chunk(script, "offline", MOCK_PATH.read_text(), place_items(test)))
    if problem == "timeout":
        return [("error", "Still running after 30 seconds: a loop that never waits?")]
    if result is None:
        return [("error", f"Couldn't run it: {problem}")]
    sources = {f["name"]: read_code(test["id"], f["name"]) for f in test_files(test)}
    lines = []
    for note in result.get("notes", []):
        kind, text = note.split("\t", 1) if "\t" in note else ("info", note)
        if kind == "error":
            for part in explain(text, sources).split("\n\n"):
                lines.append((kind if part == text else "info", part.replace("`", "")))
        else:
            lines.append((kind, text))
    return lines or [("info", "It ran, and printed nothing.")]


# The program runner (every language but Luau) -------------------------------------------------

def run_program(test, append="", stdin="", args=(), main=None):
    """Runs the test's files as a program: the main file, with `append` added to its end
    (code a case uses to call your functions). Returns stdout, stderr, exit code, timed out."""
    import shutil
    import tempfile
    files = test_files(test)
    main = main or files[0]["name"]
    folder = Path(tempfile.mkdtemp(prefix="learning-"))
    try:
        for file in files:
            text = read_code(test["id"], file["name"])
            if file["name"] == main and append:
                text = text.rstrip("\n") + "\n\n" + append.rstrip("\n") + "\n"
            (folder / file["name"]).write_text(text)
        command = LANG["command"] + [str(folder / main)] + list(args)
        try:
            result = subprocess.run(command, input=stdin, capture_output=True, text=True, cwd=folder,
                                    timeout=test.get("timeout", PROGRAM_SECONDS))
        except subprocess.TimeoutExpired as stuck:
            return (stuck.stdout or b"").decode(errors="replace") if isinstance(stuck.stdout, bytes) else (stuck.stdout or ""), "", None, True
        return result.stdout.replace("\r\n", "\n"), result.stderr.replace(str(folder) + "/", ""), result.returncode, False
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def strip_comments(source):
    # Drops comments (keeping strings), so "uses" rules can't be met by a comment
    out, position = [], 0
    for match in TOKENS.finditer(source):
        if match.group("comment"):
            out.append(source[position:match.start()])
            position = match.end()
    out.append(source[position:])
    return "".join(out)


def show_output(text, limit=600):
    text = text.rstrip("\n")
    if not text:
        return "nothing"
    if len(text) > limit:
        text = text[:limit] + " …"
    return "`" + text.replace("`", "'").replace("\n", "` / `") + "`"


def last_error_line(stderr):
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return "\n".join(lines[-4:]) if lines else "(no message)"


def case_outcome(case, stdout, stderr, code, timed_out):
    """(passed, detail) for one case: what the program printed against what was expected."""
    if timed_out:
        return False, (f"Your program was still running after {PROGRAM_SECONDS} seconds. A loop that never ends, "
                       "or waiting for input that never comes, is the usual cause.")
    if case.get("error"):
        if code == 0:
            return False, case.get("fail", "Your program should have stopped with an error here, but it finished normally.")
        return True, None
    if code != 0:
        return False, f"Your program stopped with an error:\n{last_error_line(stderr)}"
    lines = [line.rstrip() for line in stdout.rstrip("\n").split("\n")] if stdout.strip() else []
    printed = f"It printed {show_output(stdout)}."
    fail = case.get("fail", "")
    if "output" in case and stdout.rstrip() != case["output"].rstrip():
        return False, f"{fail} The whole output should be {show_output(case['output'])}. {printed}".strip()
    if "lines" in case and lines != case["lines"]:
        return False, f"{fail} Expected exactly {show_output(chr(10).join(case['lines']))}. {printed}".strip()
    if "first" in case and (not lines or lines[0] != case["first"]):
        return False, f"{fail} The first line should be `{case['first']}`. {printed}".strip()
    if "last" in case and (not lines or lines[-1] != str(case["last"])):
        return False, f"{fail} The last line should be `{case['last']}`. {printed}".strip()
    for wanted in case.get("contains", []):
        if wanted not in stdout:
            return False, f"{fail} Expected `{wanted}` somewhere in the output. {printed}".strip()
    for unwanted in case.get("not_contains", []):
        if unwanted in stdout:
            return False, f"{fail} `{unwanted}` shouldn't be printed. {printed}".strip()
    if "regex" in case and not re.search(case["regex"], stdout, re.M):
        return False, f"{fail} {printed}".strip()
    if "min_lines" in case and len(lines) < case["min_lines"]:
        return False, f"{fail} {printed}".strip()
    return True, None


def run_program_check(test, report=lambda text: None):
    report("Running your code...")
    files = test_files(test)
    sources = {f["name"]: read_code(test["id"], f["name"]) for f in files}
    outcomes = []
    for rule in test.get("static", []):
        source = sources.get(rule.get("file", files[0]["name"]), "")
        text = source if rule.get("comments") else strip_comments(source)
        found = re.search(rule["pattern"], text, re.M)
        count = len(re.findall(rule["pattern"], text, re.M))
        if rule["type"] == "uses":
            ok = count >= rule.get("times", 1)
        else:
            ok = not found
        outcomes.append((rule["id"], ok, None if ok else rule["fail"]))
    for number, case in enumerate(test.get("cases", []), 1):
        report(f"Running case {number} of {len(test['cases'])}...")
        stdout, stderr, code, timed_out = run_program(test, case.get("append", ""), case.get("stdin", ""),
                                                      case.get("args", ()))
        ok, detail = case_outcome(case, stdout, stderr, code, timed_out)
        if detail and case.get("label"):
            detail = f"With {case['label']}: {detail}"
        outcomes.append((case["id"], ok, detail))
        if timed_out:
            break
    return explain_result(test, merge(test, outcomes, None, "Not checked, because an earlier case got stuck."))


def run_program_file(test, file):
    stdout, stderr, code, timed_out = run_program(test, stdin=test.get("run_stdin", ""), main=file["name"])
    lines = [("print", line) for line in stdout.rstrip("\n").split("\n")] if stdout.strip() else []
    if timed_out:
        lines.append(("error", f"Still running after {PROGRAM_SECONDS} seconds: a loop that never ends, "
                               "or waiting for input?"))
    elif code:
        lines += [("error", line) for line in stderr.rstrip().splitlines()[-12:]]
        meaning = next((m for pattern, m in LANG.get("explanations", []) if re.search(pattern, stderr)), None)
        if meaning:
            lines.append(("info", meaning.replace("`", "")))
    elif stderr.strip():
        lines += [("warn", line) for line in stderr.rstrip().splitlines()[-12:]]
    return lines or [("info", "It ran, and printed nothing.")]


def record_check(progress, test_id, result):
    entry = progress["tests"].setdefault(test_id, {})
    entry["attempts"] = entry.get("attempts", 0) + 1
    entry["last"] = today()
    if result.get("passed") and not entry.get("passed"):
        entry["passed"] = today()
    if result.get("ran"):
        mark_active(progress)


def compact_result(result):
    # What the panel remembers about the last check, so reopening it shows the same marks
    return {
        "ran": result.get("ran"),
        "passed": result.get("passed"),
        "message": result.get("message"),
        "rows": [{"id": r["id"], "status": r["status"], "details": r["details"]} for r in result.get("rows", [])],
        "notes": result.get("notes", []),
    }


def print_check(test_id):
    course = load_course()
    test = course["test_map"].get(test_id)
    if not test:
        sys.exit(f"No test called {test_id}")
    result = run_check(test, report=lambda text: print(text, file=sys.stderr))
    progress = load_progress()
    record_check(progress, test_id, result)
    progress["tests"][test_id]["result"] = compact_result(result)
    save_progress(progress)
    print(json.dumps(result, indent=2, ensure_ascii=False))


# Daily review -------------------------------------------------------------------------------

def review_questions(course, progress):
    # Five questions from finished lessons, the same five all day, with their choices shuffled
    pool = []
    for lesson_id in all_lesson_ids(course):
        lesson = course["lesson_map"].get(lesson_id)
        if lesson and lesson_done(progress, lesson_id):
            for index, question in enumerate(lesson.get("quiz", [])):
                pool.append((f"{lesson_id}#{index}", lesson, question))
    chooser = random.Random(today())
    chooser.shuffle(pool)
    chosen = []
    for key, lesson, question in pool[:REVIEW_QUESTIONS]:
        order = list(range(len(question["choices"])))
        chooser.shuffle(order)
        chosen.append({
            "key": key,
            "lesson": lesson["title"],
            "question": question["question"],
            "choices": [question["choices"][i] for i in order],
            "answer": order.index(question["answer"]),
            "explain": question.get("explain", ""),
        })
    return chosen


def review_state(progress):
    return progress["review"].setdefault(today(), {"done": [], "tried": [], "first_try": 0})


# The panel ----------------------------------------------------------------------------------

PANEL_WIDTH = 270
LIST_MAX_HEIGHT = 440
DETAILS_WIDTH = 610
DETAILS_MAX_HEIGHT = 760
LOCK_ICON = ""
GREEN = "#30d158"
ORANGE = "#ff9f0a"
GRAY = "#8e8e93"
RED = "#ff6961"
KEYWORD_COLOR = "#fc5fa3"
STRING_COLOR = "#fc8f7f"
COMMENT_COLOR = "#7f8c98"
NUMBER_COLOR = "#d0bf69"
LUAU_TOKEN = re.compile(
    r"(?P<comment>--\[(?P<ce>=*)\[.*?\](?P=ce)\]|--[^\n]*)"
    r"|(?P<string>\[(?P<se>=*)\[.*?\](?P=se)\]|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)"
    r"|(?P<number>\b(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)\b)"
    r"|(?P<keyword>\b(?:and|break|continue|do|else|elseif|end|false|for|function|if|in|local|nil|"
    r"not|or|repeat|return|then|true|until|while)\b)",
    re.S,
)
TOKENS = LUAU_TOKEN

COURSE_CSS = b"""
.chapter-row, .lesson-row, .test-row, .plain-row, .choice, .hint-button, .check-button, .run-button {
    background-image: none;
    background-color: transparent;
    border: none;
    border-radius: 6px;
    box-shadow: none;
    color: #ffffff;
    outline-width: 0;
    text-shadow: none;
}
.chapter-row, .plain-row { padding: 5px 6px; }
.lesson-row, .test-row { padding: 3px 5px; }
.chapter-row:hover, .lesson-row:hover, .test-row:hover, .plain-row:hover {
    background-color: rgba(255, 255, 255, 0.09);
}
.chapter-row.open { background-color: rgba(255, 255, 255, 0.06); }
.chapter-row.selected, .lesson-row.selected, .test-row.selected, .plain-row.selected,
.chapter-row.selected:hover, .lesson-row.selected:hover, .test-row.selected:hover, .plain-row.selected:hover {
    background-color: rgba(10, 132, 255, 0.55);
}
.chapter-number { color: rgba(255, 255, 255, 0.45); font-size: 10px; font-weight: 700; }
.chapter-name { font-size: 11px; font-weight: 700; }
.lesson-name { font-size: 10px; font-weight: 500; }
.test-name { font-size: 10px; font-weight: 700; color: #ff9f0a; }
.lessons { padding: 2px 2px 6px 18px; }
.locked { opacity: 0.5; }
.lesson-heading { font-size: 14px; font-weight: 700; }
.code-block {
    background-color: rgba(0, 0, 0, 0.3);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 8px;
    padding: 10px 12px;
}
.code { font-family: "Noto Sans Mono", monospace; font-size: 11px; }
.example-caption { color: rgba(255, 255, 255, 0.55); font-size: 11px; font-weight: 600; }
.try-box {
    background-color: rgba(48, 209, 88, 0.07);
    border: 1px solid rgba(48, 209, 88, 0.3);
    border-radius: 8px;
    padding: 10px 12px;
}
.try-heading { color: #30d158; font-size: 11px; font-weight: 700; }
.quiz-question { font-size: 12px; font-weight: 600; }
.choice {
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 7px;
    padding: 6px 9px;
}
.choice:hover { background-color: rgba(255, 255, 255, 0.12); }
.choice.correct, .choice.correct:hover, .choice.correct:disabled {
    background-color: rgba(48, 209, 88, 0.25);
    border-color: rgba(48, 209, 88, 0.65);
}
.choice.wrong, .choice.wrong:hover {
    background-color: rgba(255, 69, 58, 0.2);
    border-color: rgba(255, 69, 58, 0.55);
}
.choice:disabled { color: rgba(255, 255, 255, 0.6); }
.feedback-good { color: #30d158; font-size: 11px; }
.feedback-bad { color: #ff6961; font-size: 11px; }
.check-button {
    background-color: #0a84ff;
    font-size: 12px;
    font-weight: 700;
    border-radius: 7px;
    padding: 6px 16px;
}
.check-button:hover { background-color: #2b95ff; }
.check-button:disabled { background-color: rgba(10, 132, 255, 0.35); color: rgba(255, 255, 255, 0.7); }
.hint-button {
    background-color: rgba(255, 159, 10, 0.15);
    color: #ff9f0a;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 10px;
}
.hint-button:hover { background-color: rgba(255, 159, 10, 0.25); }
.banner-pass {
    background-color: rgba(48, 209, 88, 0.12);
    border: 1px solid rgba(48, 209, 88, 0.5);
    border-radius: 8px;
    padding: 10px 12px;
}
.banner-fail {
    background-color: rgba(255, 159, 10, 0.1);
    border: 1px solid rgba(255, 159, 10, 0.4);
    border-radius: 8px;
    padding: 10px 12px;
}
.detail-text { color: #ff8a80; font-size: 11px; }
.status-line { color: rgba(255, 255, 255, 0.65); font-size: 11px; }
@keyframes glow-correct {
    from { box-shadow: 0 0 0 0 rgba(48, 209, 88, 0.9); }
    to { box-shadow: 0 0 0 14px rgba(48, 209, 88, 0); }
}
@keyframes glow-wrong {
    from { box-shadow: 0 0 0 0 rgba(255, 69, 58, 0.9); }
    to { box-shadow: 0 0 0 10px rgba(255, 69, 58, 0); }
}
@keyframes pop-glow {
    from { text-shadow: 0 0 14px rgba(48, 209, 88, 1); }
    to { text-shadow: 0 0 0 rgba(48, 209, 88, 0); }
}
@keyframes pop-glow-fail {
    from { text-shadow: 0 0 14px rgba(255, 105, 97, 1); }
    to { text-shadow: 0 0 0 rgba(255, 105, 97, 0); }
}
@keyframes celebrate {
    from { box-shadow: 0 0 0 0 rgba(255, 214, 10, 0.95); }
    to { box-shadow: 0 0 0 24px rgba(255, 214, 10, 0); }
}
@keyframes unlocked {
    from { background-color: rgba(10, 132, 255, 0.8); box-shadow: 0 0 18px rgba(10, 132, 255, 0.9); }
    to { background-color: rgba(10, 132, 255, 0); box-shadow: 0 0 0 rgba(10, 132, 255, 0); }
}
@keyframes score-bump {
    from { color: #ffd60a; text-shadow: 0 0 16px rgba(255, 214, 10, 0.95); }
    to { color: #0a84ff; text-shadow: 0 0 0 rgba(255, 214, 10, 0); }
}
.choice.correct { animation: glow-correct 700ms ease-out; }
.choice.wrong { animation: glow-wrong 450ms ease-out; }
.pop { animation: pop-glow 900ms ease-out; }
.pop-fail { animation: pop-glow-fail 900ms ease-out; }
.banner-pass.celebrate { animation: celebrate 1000ms ease-out 2; }
.chapter-row.unlocked { animation: unlocked 2500ms ease-out; }
.score.bump { animation: score-bump 1100ms ease-out; }
.choice { transition: background-color 120ms ease-out, border-color 120ms ease-out; }
.chapter-row, .lesson-row, .test-row, .plain-row { transition: background-color 140ms ease-out; }
.check-button { transition: background-color 150ms ease-out, box-shadow 220ms ease-out; }
.check-button:hover { box-shadow: 0 0 16px rgba(10, 132, 255, 0.6); }
.hint-button { transition: background-color 150ms ease-out; }
.scanner trough {
    min-height: 3px;
    border: none;
    border-radius: 2px;
    background-color: rgba(255, 255, 255, 0.1);
}
.scanner progress {
    min-height: 3px;
    border: none;
    border-radius: 2px;
    background-image: none;
    background-color: #64d2ff;
    box-shadow: 0 0 8px rgba(100, 210, 255, 0.9);
}
.sound-button {
    background-image: none;
    background-color: transparent;
    border: none;
    box-shadow: none;
    padding: 0 6px;
    min-height: 0;
    color: rgba(255, 255, 255, 0.5);
    transition: color 150ms ease-out;
}
.sound-button:hover { color: #ffffff; }
.sound-button.locked { color: #ffd60a; }
.editor { font-family: "Noto Sans Mono", monospace; font-size: 11px; }
.editor text {
    background-color: rgba(0, 0, 0, 0.38);
    color: #e8e8e8;
    caret-color: #64d2ff;
}
.editor text selection { background-color: rgba(10, 132, 255, 0.45); }
.editor-tabs header { background-color: transparent; border: none; box-shadow: none; }
.editor-tabs header tab {
    padding: 3px 12px;
    color: rgba(255, 255, 255, 0.55);
    font-size: 11px;
    border: none;
    background: none;
}
.editor-tabs header tab:checked { color: #ffffff; box-shadow: inset 0 -2px #0a84ff; }
.editor-tabs stack { border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 8px; }
.run-button {
    background-color: rgba(255, 255, 255, 0.1);
    font-size: 12px;
    font-weight: 600;
    border-radius: 7px;
    padding: 6px 14px;
    transition: background-color 150ms ease-out;
}
.run-button:hover { background-color: rgba(255, 255, 255, 0.18); }
.run-button:disabled { color: rgba(255, 255, 255, 0.5); }
.run-output {
    background-color: rgba(0, 0, 0, 0.35);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 8px;
    padding: 8px 10px;
}
.output-line { font-family: "Noto Sans Mono", monospace; font-size: 11px; }
.task-box {
    background-color: rgba(10, 132, 255, 0.08);
    border: 1px solid rgba(10, 132, 255, 0.35);
    border-radius: 8px;
    padding: 12px 14px;
}
.task-heading { color: #64d2ff; font-size: 11px; font-weight: 700; }
.task-number {
    color: #0a1a2a;
    background-color: #64d2ff;
    border-radius: 9px;
    font-size: 11px;
    font-weight: 800;
    min-height: 18px;
}
"""


def load_bar():
    # Borrows the scripting progress panel's widgets and style, so both panels look the same
    import importlib.util
    spec = importlib.util.spec_from_file_location("scripting_progress", HERE / "scripting-progress.py")
    bar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bar)
    bar.PANEL_WIDTH = PANEL_WIDTH
    bar.DETAILS_WIDTH = DETAILS_WIDTH
    return bar


def highlight_luau(code):
    import html
    colors = {"comment": COMMENT_COLOR, "string": STRING_COLOR, "number": NUMBER_COLOR, "keyword": KEYWORD_COLOR}
    parts = []
    position = 0
    for match in TOKENS.finditer(code):
        kind = next(name for name in colors if match.group(name))
        parts.append(html.escape(code[position:match.start()], quote=False))
        parts.append(f"<span foreground='{colors[kind]}'>{html.escape(match.group(), quote=False)}</span>")
        position = match.end()
    parts.append(html.escape(code[position:], quote=False))
    return "".join(parts)


def icon_markup(status):
    icon, color = {
        "done": ("✔", GREEN),
        "tried": ("●", ORANGE),
        "todo": ("○", GRAY),
        "fail": ("✘", RED),
        "unchecked": ("○", GRAY),
    }.get(status, ("○", GRAY))
    if status == "locked":
        return f"<span font_family='{ICON_FONT}' foreground='{GRAY}' size='small'>{LOCK_ICON}</span>"
    return f"<span foreground='{color}'>{icon}</span>"


CONFETTI = [(1.0, 0.27, 0.23), (1.0, 0.62, 0.04), (1.0, 0.84, 0.04), (0.19, 0.82, 0.35),
            (0.04, 0.52, 1.0), (0.69, 0.32, 0.87), (1.0, 0.18, 0.57), (0.39, 0.82, 1.0)]
GREENS = [(0.19, 0.82, 0.35), (0.40, 0.95, 0.55), (0.65, 1.0, 0.75), (1.0, 1.0, 1.0)]
REDS = [(1.0, 0.27, 0.23), (1.0, 0.45, 0.40)]
GOLDS = [(1.0, 0.84, 0.04), (1.0, 0.62, 0.04), (1.0, 0.95, 0.6), (1.0, 1.0, 1.0)]
BLUES = [(0.04, 0.52, 1.0), (0.39, 0.82, 1.0), (1.0, 1.0, 1.0)]
SOUND_ON_ICON = ""
SOUND_OFF_ICON = ""
LOCKED_ICON = "\uf023"
UNLOCKED_ICON = "\uf3c1"

PARTICLES = False  # Confetti and sparks are switched off. Glows, shakes and sounds remain


class Effects:
    # Particles, rings, shakes, smooth numbers and sounds, drawn on a layer above the whole panel.
    # Every method is safe to call without a layer, so pages can still be built off-screen.
    def __init__(self, Gtk=None, root=None, canvas=None, progress=None):
        self.Gtk, self.root, self.canvas = Gtk, root, canvas
        self.progress = progress if progress is not None else {}
        self.particles, self.rings = [], []
        self.ticking = False
        self.last = None
        self.can_draw = False
        if canvas is not None:
            try:
                import gi
                gi.require_foreign("cairo")
                self.can_draw = True
                canvas.connect("draw", self.draw)
            except ImportError:
                pass  # Without python-cairo there are no particles, but everything else still animates

    def sound_on(self):
        return self.progress.get("settings", {}).get("sound", True)

    def sound(self, name):
        if self.canvas is None or not self.sound_on():
            return
        path = SOUNDS / f"{name}.wav"
        for player in ("pw-play", "paplay"):
            try:
                subprocess.Popen([player, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue

    def centre(self, widget):
        if self.root is None or widget is None or not widget.get_realized():
            return None
        allocation = widget.get_allocation()
        point = widget.translate_coordinates(self.root, allocation.width // 2, allocation.height // 2)
        return point if point else None

    def spray(self, x, y, count, colours, speed, spread, aim, life, size, confetti=False, gravity=900):
        for _ in range(count):
            angle = aim + random.uniform(-spread, spread)
            velocity = random.uniform(*speed)
            self.particles.append({
                "x": x, "y": y, "vx": math.cos(angle) * velocity, "vy": math.sin(angle) * velocity,
                "life": random.uniform(*life), "age": 0.0, "size": random.uniform(*size),
                "colour": random.choice(colours), "rotation": random.uniform(0, math.tau),
                "spin": random.uniform(-12, 12), "confetti": confetti, "gravity": gravity,
                "flutter": random.uniform(0, math.tau),
            })

    def ring(self, x, y, colour, radius, life=0.55, width=3.0):
        self.rings.append({"x": x, "y": y, "colour": colour, "radius": radius, "life": life, "age": 0.0, "width": width})

    def burst(self, widget, kind):
        if not PARTICLES:
            return
        point = self.centre(widget)
        if point is None or not self.can_draw:
            return
        x, y = point
        up = -math.pi / 2
        if kind == "correct":
            self.spray(x, y, 26, GREENS, (160, 430), math.pi, up, (0.45, 0.85), (2.5, 5))
            self.ring(x, y, GREENS[0], 70)
        elif kind == "wrong":
            self.spray(x, y, 10, REDS, (70, 190), math.pi, up, (0.3, 0.5), (2, 3.5), gravity=300)
        elif kind == "complete":
            self.spray(x, y, 110, CONFETTI, (380, 820), 1.15, up, (1.3, 2.2), (5, 9), confetti=True)
            self.ring(x, y, GOLDS[0], 120, 0.75, 4)
            self.ring(x, y, GREENS[0], 80, 0.6, 3)
        elif kind == "unlock":
            self.spray(x, y, 40, BLUES, (120, 380), math.pi, up, (0.6, 1.1), (2.5, 4.5), gravity=200)
            self.ring(x, y, BLUES[0], 90, 0.7, 3)
        elif kind == "pass":
            self.spray(x, y, 170, CONFETTI, (450, 1000), 1.25, up, (1.6, 2.6), (5, 10), confetti=True)
            self.spray(x, y, 40, GOLDS, (200, 600), math.pi, up, (0.8, 1.4), (2.5, 4.5))
            self.ring(x, y, GOLDS[0], 200, 0.9, 5)
            self.ring(x, y, CONFETTI[4], 140, 0.7, 3)
            for side in (-1, 1):
                self.spray(x + side * 220, y + 40, 70, CONFETTI, (500, 950), 0.5, up - side * 0.45,
                           (1.5, 2.4), (5, 9), confetti=True)
        self.start()

    def start(self):
        if self.canvas is not None and not self.ticking:
            self.ticking = True
            self.last = None
            self.canvas.add_tick_callback(self.step)

    def step(self, _widget, clock):
        now = clock.get_frame_time() / 1_000_000
        dt = min(now - (self.last or now), 0.05)
        self.last = now
        for p in self.particles:
            p["age"] += dt
            p["vy"] += p["gravity"] * dt
            drag = 0.985 if p["confetti"] else 0.97
            p["vx"] *= drag
            p["vy"] *= drag
            if p["confetti"]:
                p["vx"] += math.sin(p["age"] * 7 + p["flutter"]) * 40 * dt
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["rotation"] += p["spin"] * dt
        for r in self.rings:
            r["age"] += dt
        self.particles = [p for p in self.particles if p["age"] < p["life"]]
        self.rings = [r for r in self.rings if r["age"] < r["life"]]
        self.canvas.queue_draw()
        if not self.particles and not self.rings:
            self.ticking = False
            return False
        return True

    def draw(self, _widget, cr):
        for r in self.rings:
            t = r["age"] / r["life"]
            eased = 1 - (1 - t) ** 3
            red, green, blue = r["colour"]
            cr.set_source_rgba(red, green, blue, (1 - t) * 0.85)
            cr.set_line_width(r["width"] * (1 - t) + 0.5)
            cr.arc(r["x"], r["y"], 6 + r["radius"] * eased, 0, math.tau)
            cr.stroke()
        for p in self.particles:
            fade = min(1.0, (p["life"] - p["age"]) / 0.35)
            red, green, blue = p["colour"]
            cr.save()
            cr.translate(p["x"], p["y"])
            cr.set_source_rgba(red, green, blue, fade)
            if p["confetti"]:
                cr.rotate(p["rotation"])
                cr.scale(1, abs(math.cos(p["age"] * 9 + p["flutter"])) * 0.8 + 0.2)
                cr.rectangle(-p["size"] / 2, -p["size"] / 3.2, p["size"], p["size"] / 1.6)
            else:
                cr.arc(0, 0, p["size"] / 2, 0, math.tau)
            cr.fill()
            cr.restore()
        return False

    def animate(self, seconds, update, done=None):
        # Calls update(t) every frame, with t easing out from 0 to 1
        if self.canvas is None:
            update(1.0)
            if done:
                done()
            return
        state = {"start": None}

        def tick(_widget, clock):
            now = clock.get_frame_time() / 1_000_000
            state["start"] = state["start"] or now
            t = min((now - state["start"]) / seconds, 1.0)
            update(1 - (1 - t) ** 3)
            if t >= 1:
                if done:
                    done()
                return False
            return True

        self.canvas.add_tick_callback(tick)

    def shake(self, widget, base=6, size=6):
        if self.canvas is None:
            return

        def update(t):
            offset = round(math.sin(t * math.pi * 7) * size * (1 - t))
            widget.set_margin_start(base + offset)
            widget.set_margin_end(base - offset)

        def settle():
            widget.set_margin_start(base)
            widget.set_margin_end(base)

        self.animate(0.42, update, settle)

    def flash(self, widget, css_class, seconds=1.2):
        # Adds a class whose keyframes play once, then removes it so it can play again later
        if widget is None or self.canvas is None:
            return
        from gi.repository import GLib
        context = widget.get_style_context()
        context.remove_class(css_class)
        context.add_class(css_class)
        GLib.timeout_add(int(seconds * 1000), lambda: context.remove_class(css_class) or False)


class Panel:
    def __init__(self, Gtk, bar, course, progress, effects=None):
        self.Gtk = Gtk
        self.bar = bar
        self.course = course
        self.progress = progress
        self.lesson_icons = {}
        self.test_icons = {}
        self.chapters = []
        self.final = None
        self.review_icon = None
        self.header = {}
        self.checks_running = 0
        self.close_when_done = None
        self.on_lock = None  # Set by open_popup, so the lock button can pin the window
        self.effects = effects or Effects(progress=progress)
        self.shown = None  # The header percentage as last displayed, so changes can count up
        self.pane = bar.build_details_pane(Gtk)

    # Small widget helpers

    def text(self, markup, css="body-text", chars=72):
        return self.bar.build_text(self.Gtk, markup, css, chars)

    def rich(self, text, css="body-text", chars=72):
        return self.text(self.bar.rich_text(text), css, chars)

    def plain(self, text, css):
        import html
        return self.text(html.escape(text, quote=False), css)

    def icon(self, status):
        label = self.Gtk.Label()
        label.set_markup(icon_markup(status))
        label.set_valign(self.Gtk.Align.START)
        label.get_style_context().add_class("status-icon")
        return label

    def heading(self, text):
        label = self.Gtk.Label(label=text.upper(), xalign=0)
        label.get_style_context().add_class("section-heading")
        return label

    def box(self, spacing=6, css=None):
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=spacing)
        if css:
            box.get_style_context().add_class(css)
        return box

    def paragraphs(self, box, text):
        for paragraph in str(text).strip().split("\n\n"):
            if paragraph.strip():
                box.pack_start(self.rich(paragraph), False, False, 0)

    def bullets(self, box, items, chars=68):
        for item in items:
            row = self.Gtk.Box(spacing=7)
            dot = self.Gtk.Label(label="•")
            dot.set_valign(self.Gtk.Align.START)
            dot.get_style_context().add_class("bullet")
            row.pack_start(dot, False, False, 0)
            row.pack_start(self.rich(item, "body-text", chars), True, True, 0)
            box.pack_start(row, False, False, 0)

    def scroll_page(self, page):
        Gtk = self.Gtk
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_overlay_scrolling(False)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(DETAILS_MAX_HEIGHT)
        scroller.add(page)
        self.bar.add_smooth_scrolling(scroller)
        return scroller

    def page_top(self, title, kind, summary=None, status=None):
        page = self.box(15, "details-page")
        top = self.Gtk.Box(spacing=10)
        titles = self.box(2)
        name = self.Gtk.Label(label=title, xalign=0)
        name.set_line_wrap(True)
        name.set_max_width_chars(50)
        name.get_style_context().add_class("details-title")
        titles.pack_start(name, False, False, 0)
        titles.pack_start(self.plain(kind, "details-kind"), False, False, 0)
        if summary:
            titles.pack_start(self.rich(summary, "details-summary"), False, False, 3)
        top.pack_start(titles, True, True, 0)
        if status is not None:
            status.set_valign(self.Gtk.Align.START)
            top.pack_end(status, False, False, 0)
        page.pack_start(top, False, False, 0)
        return page

    # Saving and refreshing

    def save(self):
        save_progress(self.progress)
        self.refresh()

    def refresh(self):
        course, progress = self.course, self.progress
        percent = round(100 * course_fraction(course, progress))
        lessons = all_lesson_ids(course)
        tests = all_test_ids(course)
        done_lessons = sum(lesson_done(progress, l) for l in lessons)
        passed_tests = sum(test_passed(progress, t) for t in tests)
        days = streak(progress)
        self.show_percent(percent)
        day_text = f"{days}-day streak" if days else "No streak yet"
        self.header["counts"].set_text(
            f"{day_text}  ·  {done_lessons}/{len(lessons)} lessons  ·  {passed_tests}/{len(tests)} tests"
        )
        for index, entry in enumerate(self.chapters):
            chapter = entry["chapter"]
            unlocked = chapter_unlocked(course, progress, index)
            self.set_chapter_state(entry, unlocked, test_passed(progress, chapter["test"]),
                                   [lesson_done(progress, l) for l in chapter["lessons"]])
        if self.final:
            unlocked = final_unlocked(course, progress)
            finished = all(test_passed(progress, t) for t in course["final"]["tests"])
            passes = [test_passed(progress, t) for t in course["final"]["tests"]]
            self.set_chapter_state(self.final, unlocked, finished, passes)
        for lesson_id, label in self.lesson_icons.items():
            label.set_markup(icon_markup("done" if lesson_done(progress, lesson_id) else "todo"))
        for test_id, label in self.test_icons.items():
            entry = progress["tests"].get(test_id, {})
            label.set_markup(icon_markup("done" if entry.get("passed") else "tried" if entry.get("attempts") else "todo"))
        if self.review_icon:
            state = progress["review"].get(today(), {})
            questions = review_questions(course, progress)
            finished = questions and len(state.get("done", [])) >= len(questions)
            self.review_icon.set_markup(icon_markup("done" if finished else "todo"))

    def show_percent(self, percent):
        # The header counts up to a new value instead of jumping, and glows as it does
        before, self.shown = self.shown, percent
        if before is None or before == percent:
            self.header["score"].set_text(f"{percent}%")
            self.header["bar"].set_fraction(percent / 100)
            return

        def update(t):
            value = before + (percent - before) * t
            self.header["score"].set_text(f"{round(value)}%")
            self.header["bar"].set_fraction(value / 100)

        self.effects.animate(1.1, update)
        self.effects.flash(self.header["score"], "bump", 1.1)

    def set_chapter_state(self, entry, unlocked, finished, parts):
        entry["unlocked"] = unlocked
        context = entry["button"].get_style_context()
        context.remove_class("locked") if unlocked else context.add_class("locked")
        if not unlocked:
            entry["status"].set_markup(icon_markup("locked"))
            if entry["revealer"].get_reveal_child():
                self.toggle_chapter(entry)
        elif finished:
            entry["status"].set_markup(icon_markup("done"))
        else:
            share = round(100 * sum(parts) / max(len(parts), 1))
            entry["status"].set_markup(f"<span size='small' weight='600'>{share}%</span>")

    # The list on the left

    def build_header(self):
        Gtk = self.Gtk
        header = self.box(7, "header")
        top = Gtk.Box()
        titles = self.box(0)
        title = Gtk.Label(label=self.course.get("title", f"{LANG['name']} Course"), xalign=0)
        title.get_style_context().add_class("title")
        counts = Gtk.Label(xalign=0)
        counts.set_line_wrap(True)
        counts.set_max_width_chars(40)
        counts.get_style_context().add_class("muted")
        titles.pack_start(title, False, False, 0)
        titles.pack_start(counts, False, False, 0)
        score = Gtk.Label()
        score.get_style_context().add_class("score")
        top.pack_start(titles, True, True, 0)
        top.pack_end(score, False, False, 0)
        top.pack_end(self.build_sound_toggle(), False, False, 4)
        top.pack_end(self.build_lock_toggle(), False, False, 0)
        progress_bar = Gtk.ProgressBar()
        header.pack_start(top, False, False, 0)
        header.pack_start(progress_bar, False, False, 0)
        self.header = {"score": score, "counts": counts, "bar": progress_bar}
        return header

    def build_sound_toggle(self):
        label = self.Gtk.Label()
        button = self.row_button("sound-button", label)
        button.set_valign(self.Gtk.Align.CENTER)

        def paint():
            icon = SOUND_ON_ICON if self.effects.sound_on() else SOUND_OFF_ICON
            label.set_markup(f"<span font_family='{ICON_FONT}' size='small'>{icon}</span>")

        def toggle(_button):
            settings = self.progress.setdefault("settings", {})
            settings["sound"] = not settings.get("sound", True)
            save_progress(self.progress)
            paint()
            self.effects.sound("tick")

        button.connect("clicked", toggle)
        paint()
        return button

    def build_lock_toggle(self):
        # Locked keeps the panel open while you click and type in other windows
        label = self.Gtk.Label()
        button = self.row_button("sound-button", label)
        button.set_valign(self.Gtk.Align.CENTER)

        def paint():
            locked = self.progress.get("settings", {}).get("locked", False)
            icon = LOCKED_ICON if locked else UNLOCKED_ICON
            label.set_markup(f"<span font_family='{ICON_FONT}' size='small'>{icon}</span>")
            button.set_tooltip_text("Locked: stays open. Click to unlock" if locked
                                    else "Lock the panel open while you multitask")
            context = button.get_style_context()
            (context.add_class if locked else context.remove_class)("locked")

        def toggle(_button):
            settings = self.progress.setdefault("settings", {})
            settings["locked"] = not settings.get("locked", False)
            save_progress(self.progress)
            paint()
            self.effects.sound("tick")
            if self.on_lock:
                self.on_lock(settings["locked"])

        button.connect("clicked", toggle)
        paint()
        return button

    def row_button(self, css, content):
        button = self.Gtk.Button()
        button.set_relief(self.Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class(css)
        button.add(content)
        return button

    def plain_row(self, title, subtitle, key, build_page, status=None):
        Gtk = self.Gtk
        content = Gtk.Box(spacing=6)
        if status is not None:
            content.pack_start(status, False, False, 0)
        text = self.box(0)
        name = Gtk.Label(label=title, xalign=0)
        name.get_style_context().add_class("chapter-name")
        sub = Gtk.Label(label=subtitle, xalign=0)
        sub.get_style_context().add_class("muted")
        text.pack_start(name, False, False, 0)
        text.pack_start(sub, False, False, 0)
        content.pack_start(text, True, True, 0)
        button = self.row_button("plain-row", content)
        button.connect("clicked", lambda b: self.pane["toggle"](key, build_page, b))
        return button

    def lesson_row(self, chapter_number, index, lesson):
        Gtk = self.Gtk
        content = Gtk.Box(spacing=6)
        icon = self.icon("todo")
        self.lesson_icons[lesson["id"]] = icon
        content.pack_start(icon, False, False, 0)
        name = Gtk.Label(label=f"{chapter_number}.{index}  {lesson['title']}", xalign=0)
        name.set_line_wrap(True)
        name.set_max_width_chars(30)
        name.get_style_context().add_class("lesson-name")
        content.pack_start(name, True, True, 0)
        minutes = Gtk.Label(label=f"{lesson.get('minutes', 8)} min")
        minutes.set_valign(Gtk.Align.START)
        minutes.get_style_context().add_class("muted")
        content.pack_end(minutes, False, False, 0)
        button = self.row_button("lesson-row", content)
        key = f"lesson/{lesson['id']}"
        button.connect("clicked", lambda b: self.pane["toggle"](
            key, lambda: self.build_lesson_page(chapter_number, index, lesson), b))
        return button

    def test_row(self, label, test):
        Gtk = self.Gtk
        content = Gtk.Box(spacing=6)
        icon = self.icon("todo")
        self.test_icons[test["id"]] = icon
        content.pack_start(icon, False, False, 0)
        name = Gtk.Label(label=f"{label}: {test['title']}", xalign=0)
        name.set_line_wrap(True)
        name.set_max_width_chars(30)
        name.get_style_context().add_class("test-name")
        content.pack_start(name, True, True, 0)
        button = self.row_button("test-row", content)
        key = f"test/{test['id']}"
        button.connect("clicked", lambda b: self.pane["toggle"](
            key, lambda: self.build_test_page(label, test), b))
        return button

    def toggle_chapter(self, entry):
        is_open = not entry["revealer"].get_reveal_child()
        entry["revealer"].set_reveal_child(is_open)
        entry["chevron"].set_text("⌄" if is_open else "›")
        context = entry["button"].get_style_context()
        context.add_class("open") if is_open else context.remove_class("open")
        if not is_open:
            for key in entry["keys"]:
                self.pane["hide_within"](key)

    def chapter_block(self, number_text, title, rows, keys, locked_page):
        Gtk = self.Gtk
        content = Gtk.Box(spacing=5)
        chevron = Gtk.Label(label="›")
        chevron.set_size_request(9, -1)
        chevron.get_style_context().add_class("chevron")
        number = Gtk.Label(label=number_text)
        number.set_size_request(14, -1)
        number.get_style_context().add_class("chapter-number")
        name = Gtk.Label(label=title, xalign=0)
        name.set_line_wrap(True)
        name.set_max_width_chars(26)
        name.get_style_context().add_class("chapter-name")
        status = Gtk.Label()
        content.pack_start(chevron, False, False, 0)
        content.pack_start(number, False, False, 0)
        content.pack_start(name, True, True, 0)
        content.pack_end(status, False, False, 0)
        button = self.row_button("chapter-row", content)

        body = self.box(1, "lessons")
        for row in rows:
            body.pack_start(row, False, False, 0)
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        revealer.set_transition_duration(self.bar.ROW_DURATION)
        revealer.add(body)

        entry = {"button": button, "revealer": revealer, "chevron": chevron, "status": status,
                 "unlocked": True, "keys": keys}

        def on_click(widget):
            if entry["unlocked"]:
                self.toggle_chapter(entry)
            else:
                self.pane["toggle"](f"locked/{number_text}", locked_page, widget)

        button.connect("clicked", on_click)
        wrapper = self.box(0)
        wrapper.pack_start(button, False, False, 0)
        wrapper.pack_start(revealer, False, False, 0)
        return wrapper, entry

    def build_list(self):
        course = self.course
        rows = self.box(1, "list")
        rows.pack_start(self.plain_row(
            "How the course works", "Start here", "about", self.build_about_page,
            self.icon("todo") if not self.progress["days"] else None), False, False, 0)
        self.review_icon = self.icon("todo")
        rows.pack_start(self.plain_row(
            "Daily review", f"{REVIEW_QUESTIONS} questions from lessons you've finished", "review",
            self.build_review_page, self.review_icon), False, False, 0)

        for index, chapter in enumerate(course["chapters"]):
            number = chapter.get("number", index + 1)
            lesson_rows = [
                self.lesson_row(number, position, course["lesson_map"][lesson_id])
                for position, lesson_id in enumerate(chapter["lessons"], 1)
                if lesson_id in course["lesson_map"]
            ]
            test = course["test_map"].get(chapter["test"])
            if test:
                lesson_rows.append(self.test_row(f"Test {number}", test))
            keys = [f"lesson/{l}" for l in chapter["lessons"]] + [f"test/{chapter['test']}"]
            previous = course["chapters"][index - 1] if index else None
            locked_page = (lambda p=previous: self.build_locked_page(p))
            block, entry = self.chapter_block(str(number), chapter["title"], lesson_rows, keys, locked_page)
            entry["chapter"] = chapter
            self.chapters.append(entry)
            rows.pack_start(block, False, False, 0)

        final_tests = [course["test_map"][t] for t in course["final"]["tests"] if t in course["test_map"]]
        if final_tests:
            final_rows = [self.test_row(f"Project {n}", t) for n, t in enumerate(final_tests, 1)]
            keys = [f"test/{t['id']}" for t in final_tests]
            last = course["chapters"][-1]
            block, entry = self.chapter_block(
                "★", course["final"].get("title", "Final exam"), final_rows, keys,
                lambda: self.build_locked_page(last))
            self.final = entry
            rows.pack_start(block, False, False, 0)
        return rows

    def build_panel(self):
        Gtk = self.Gtk
        panel = self.box(0, "panel")
        panel.set_size_request(PANEL_WIDTH, -1)
        panel.pack_start(self.build_header(), False, False, 0)
        panel.pack_start(self.bar.separator(Gtk), False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_overlay_scrolling(False)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(LIST_MAX_HEIGHT)
        scroller.add(self.build_list())
        self.bar.add_smooth_scrolling(scroller)
        panel.pack_start(scroller, True, True, 0)
        panel.pack_start(self.bar.separator(Gtk), False, False, 0)
        legend = Gtk.Label(xalign=0)
        legend.set_markup(
            f"{icon_markup('done')} Done   {icon_markup('tried')} Tried   {icon_markup('todo')} To do   "
            f"{icon_markup('locked')} Locked"
        )
        legend.get_style_context().add_class("muted")
        legend.get_style_context().add_class("legend")
        panel.pack_start(legend, False, False, 0)
        self.refresh()
        return panel

    # Pages

    def build_about_page(self):
        if self.course.get("about"):
            page = self.page_top("How the course works", "Start here", self.course.get("about_summary"))
            for section in self.course["about"]:
                block = self.box(6)
                title_label = self.Gtk.Label(label=section["heading"], xalign=0)
                title_label.get_style_context().add_class("lesson-heading")
                block.pack_start(title_label, False, False, 0)
                self.paragraphs(block, section["body"])
                page.pack_start(block, False, False, 0)
            return self.scroll_page(page)
        page = self.page_top("How the course works", "Start here",
                             "Lessons teach the ideas. Tests on your baseplate prove you can use them.")
        sections = [
            ("Code right here",
             "Every test has a small editor with one tab per script. Type your code there: it saves as you "
             "type, so you can close the panel and come back later. **Run** runs the current tab and shows its "
             "output, like Studio's Output window, and **Check my work** marks the whole test.\n\n"
             "Everything runs on this computer with Luau, using stand-ins for the parts of Roblox the tests "
             "need, so you don't need Studio open. Your code is also saved as files in "
             "`~/.config/waybar/luau-course-work`, if you ever want to open it in another editor."),
            ("Lessons",
             "Each lesson explains one idea properly: what it is, why it works the way it does, and the mistakes "
             "people make with it. Examples show the syntax on a different problem from the test, so read them, "
             "then type your own version on the baseplate. Text in this panel can't be copied, on purpose.\n\n"
             "Every lesson ends with three quick check questions. Get all three right to finish the lesson."),
            ("Tests",
             "Each chapter ends with a test you build on your baseplate. The test page says exactly what to "
             "create and what gets checked, but never how to write it. Hints get stronger one at a time.\n\n"
             "When you're ready, press **Check my work**. Check as often as you like: each requirement gets a "
             "tick or a reason. Passing a chapter's test unlocks the next chapter."),
            ("Daily review and your streak",
             "Once you've finished a lesson, Daily review gives you five questions from lessons you've done, "
             "different every day. Finishing a lesson or a review, or checking a test, counts as practice for "
             "the day. The flame on your bar counts the days in a row."),
            ("Tips",
             "The lessons' Try it tasks are still best done in Studio on a spare Baseplate place, where you can "
             "see parts move and press Play.\n\n"
             "If a check says your code ran for too long, there's a loop that never waits or never ends."),
        ]
        for title, body in sections:
            section = self.box(6)
            title_label = self.Gtk.Label(label=title, xalign=0)
            title_label.get_style_context().add_class("lesson-heading")
            section.pack_start(title_label, False, False, 0)
            self.paragraphs(section, body)
            page.pack_start(section, False, False, 0)
        return self.scroll_page(page)

    def build_locked_page(self, previous):
        test = self.course["test_map"].get(previous["test"]) if previous else None
        name = f"Chapter {previous.get('number', '')}: {previous['title']}" if previous else "the previous chapter"
        page = self.page_top("Locked", "Pass the previous test to open this")
        message = f"Pass the test at the end of {name} to unlock this."
        if test:
            message += f" That's **{test['title']}**."
        self.paragraphs(page, message)
        return self.scroll_page(page)

    def build_editor(self, test):
        # One tab per script, with Luau colouring, saving to luau-course-work/ as you type
        Gtk = self.Gtk
        from gi.repository import GLib, Pango
        files = test_files(test)
        notebook = Gtk.Notebook()
        notebook.set_scrollable(True)
        notebook.get_style_context().add_class("editor-tabs")
        state = {"buffers": [], "timers": {}}

        def highlight(buffer):
            start, end = buffer.get_bounds()
            buffer.remove_all_tags(start, end)
            text = buffer.get_text(start, end, True)
            for match in TOKENS.finditer(text):
                kind = next(name for name in ("comment", "string", "number", "keyword") if match.group(name))
                buffer.apply_tag_by_name(kind, buffer.get_iter_at_offset(match.start()),
                                         buffer.get_iter_at_offset(match.end()))

        def save(file, buffer):
            start, end = buffer.get_bounds()
            write_code(test["id"], file["name"], buffer.get_text(start, end, True))

        for file in files:
            buffer = Gtk.TextBuffer()
            for name, colour in (("comment", COMMENT_COLOR), ("string", STRING_COLOR),
                                 ("number", NUMBER_COLOR), ("keyword", KEYWORD_COLOR)):
                buffer.create_tag(name, foreground=colour)
            if LANG["id"] == "luau":
                starter = f"-- {file['name']} ({file['class']})\n"
            else:
                starter = file.get("starter") or f"{LANG['comment']} {file['name']}\n"
            buffer.set_text(read_code(test["id"], file["name"]) or starter)
            highlight(buffer)
            view = Gtk.TextView(buffer=buffer)
            view.set_monospace(True)
            view.set_wrap_mode(Gtk.WrapMode.NONE)
            view.set_left_margin(10)
            view.set_right_margin(10)
            view.set_top_margin(8)
            view.set_bottom_margin(8)
            tabs = Pango.TabArray.new(1, True)
            tabs.set_tab(0, Pango.TabAlign.LEFT, 28)
            view.set_tabs(tabs)
            view.get_style_context().add_class("editor")
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroller.set_size_request(-1, 300)
            scroller.add(view)
            tab = Gtk.Label(label=file["name"])
            tab.set_tooltip_text(file["class"])
            notebook.append_page(scroller, tab)

            def changed(changed_buffer, f=file):
                key = f["name"]
                if key in state["timers"]:
                    GLib.source_remove(state["timers"].pop(key))

                def later():
                    state["timers"].pop(key, None)
                    highlight(changed_buffer)
                    save(f, changed_buffer)
                    return False

                state["timers"][key] = GLib.timeout_add(350, later)

            buffer.connect("changed", changed)
            state["buffers"].append((file, buffer))

        def save_all():
            for source in state["timers"].values():
                GLib.source_remove(source)
            state["timers"].clear()
            for file, buffer in state["buffers"]:
                save(file, buffer)
                highlight(buffer)

        def current_file():
            index = notebook.get_current_page()
            return files[index] if 0 <= index < len(files) else None

        return notebook, save_all, current_file

    def build_example(self, example):
        section = self.box(6)
        if example.get("caption"):
            section.pack_start(self.rich(example["caption"], "example-caption"), False, False, 0)
        block = self.Gtk.Box()
        block.get_style_context().add_class("code-block")
        code = self.text(highlight_luau(example["code"].expandtabs(4).rstrip()), "code", 80)
        block.pack_start(code, True, True, 0)
        section.pack_start(block, False, False, 0)
        if example.get("explain"):
            section.pack_start(self.rich(example["explain"], "body-text"), False, False, 0)
        return section

    def build_lesson_page(self, chapter_number, index, lesson):
        status = self.Gtk.Label()
        status.get_style_context().add_class("details-status")

        def show_status():
            if lesson_done(self.progress, lesson["id"]):
                status.set_markup(f"<span foreground='{GREEN}'>✔ Finished</span>")
            else:
                status.set_markup(f"<span foreground='{GRAY}'>Not finished</span>")

        show_status()
        kind = f"Chapter {chapter_number}, lesson {index}  ·  about {lesson.get('minutes', 8)} minutes"
        page = self.page_top(lesson["title"], kind, lesson.get("summary"), status)

        for section in lesson.get("sections", []):
            block = self.box(7)
            if section.get("heading"):
                title = self.Gtk.Label(label=section["heading"], xalign=0)
                title.set_line_wrap(True)
                title.get_style_context().add_class("lesson-heading")
                block.pack_start(title, False, False, 0)
            self.paragraphs(block, section.get("body", ""))
            if section.get("bullets"):
                self.bullets(block, section["bullets"])
            if section.get("example"):
                block.pack_start(self.build_example(section["example"]), False, False, 2)
            page.pack_start(block, False, False, 0)

        if lesson.get("key_points"):
            points = self.box(6, "technique")
            name = self.Gtk.Label(label="Key points", xalign=0)
            name.get_style_context().add_class("technique-name")
            points.pack_start(name, False, False, 0)
            self.bullets(points, lesson["key_points"], 64)
            page.pack_start(points, False, False, 0)

        if lesson.get("mistakes"):
            mistakes = self.box(6)
            mistakes.pack_start(self.heading("Common mistakes"), False, False, 0)
            self.bullets(mistakes, lesson["mistakes"])
            page.pack_start(mistakes, False, False, 0)

        if lesson.get("try_it"):
            practice = self.box(6, "try-box")
            name = self.Gtk.Label(label=self.course.get("try_heading", "TRY IT YOURSELF"), xalign=0)
            name.get_style_context().add_class("try-heading")
            practice.pack_start(name, False, False, 0)
            self.paragraphs(practice, lesson["try_it"])
            page.pack_start(practice, False, False, 0)

        page.pack_start(self.build_quiz(lesson, show_status), False, False, 0)
        return self.scroll_page(page)

    def build_question(self, question, number, already_right, on_right, on_wrong=None):
        # One multiple-choice question. Right answers show the explanation, wrong ones invite a retry
        Gtk = self.Gtk
        block = self.box(6)
        block.pack_start(self.rich(f"{number}. {question['question']}", "quiz-question"), False, False, 0)
        feedback = self.text("", "feedback-good")
        feedback.set_no_show_all(True)
        buttons = []

        def reveal_right(choice_button):
            for other in buttons:
                other.set_sensitive(False)
            choice_button.get_style_context().add_class("correct")
            feedback.get_style_context().remove_class("feedback-bad")
            feedback.get_style_context().add_class("feedback-good")
            feedback.set_markup(self.bar.rich_text("Correct. " + question.get("explain", "")))
            feedback.show()

        for choice_index, choice in enumerate(question["choices"]):
            content = self.rich(choice, "body-text", 66)
            choice_button = self.row_button("choice", content)
            choice_button.set_margin_start(6)
            choice_button.set_margin_end(6)

            def on_click(widget, picked=choice_index):
                if picked == question["answer"]:
                    reveal_right(widget)
                    big = on_right()
                    self.effects.burst(widget, "complete" if big else "correct")
                    self.effects.sound("complete" if big else "correct")
                    self.effects.flash(feedback, "pop", 0.9)
                else:
                    self.effects.shake(widget)
                    self.effects.burst(widget, "wrong")
                    self.effects.sound("wrong")
                    widget.get_style_context().add_class("wrong")
                    feedback.get_style_context().remove_class("feedback-good")
                    feedback.get_style_context().add_class("feedback-bad")
                    feedback.set_markup(self.bar.rich_text("Not quite. Have another look and try again."))
                    feedback.show()
                    if on_wrong:
                        on_wrong()

            choice_button.connect("clicked", on_click)
            buttons.append(choice_button)
            block.pack_start(choice_button, False, False, 0)
        block.pack_start(feedback, False, False, 2)
        if already_right:
            reveal_right(buttons[question["answer"]])
        return block

    def build_quiz(self, lesson, show_status):
        quiz = lesson.get("quiz", [])
        box = self.box(12)
        box.pack_start(self.heading("Quick check"), False, False, 0)
        box.pack_start(self.rich("Answer all three correctly to finish this lesson.", "muted"), False, False, 0)
        answered = set(self.progress["quiz"].get(lesson["id"], []))

        def right(question_index):
            done = set(self.progress["quiz"].get(lesson["id"], []))
            done.add(question_index)
            self.progress["quiz"][lesson["id"]] = sorted(done)
            completed = len(done) >= len(quiz) and not lesson_done(self.progress, lesson["id"])
            if completed:
                self.progress["lessons"][lesson["id"]] = {"done": today()}
                mark_active(self.progress)
            self.save()
            show_status()
            return completed

        for number, question in enumerate(quiz, 1):
            box.pack_start(self.build_question(
                question, number, (number - 1) in answered, lambda i=number - 1: right(i)), False, False, 0)
        return box

    def build_review_page(self):
        questions = review_questions(self.course, self.progress)
        page = self.page_top("Daily review", datetime.date.today().strftime("%A %-d %B"),
                             "Five questions from lessons you've finished. They change every day.")
        if not questions:
            self.paragraphs(page, "Finish a lesson's quick check first, and its questions will start "
                                  "turning up here.")
            return self.scroll_page(page)
        state = review_state(self.progress)
        score = self.rich("", "muted")

        def show_score():
            state_now = review_state(self.progress)
            if len(state_now["done"]) >= len(questions):
                score.set_markup(self.bar.rich_text(
                    f"**Done for today.** You got {state_now['first_try']} of {len(questions)} right first time."))
            else:
                score.set_markup(self.bar.rich_text(
                    f"{len(state_now['done'])} of {len(questions)} answered."))

        def first_click(key, correct):
            state_now = review_state(self.progress)
            if key not in state_now["tried"]:
                state_now["tried"].append(key)
                if correct:
                    state_now["first_try"] += 1

        def right(key):
            first_click(key, True)
            state_now = review_state(self.progress)
            if key not in state_now["done"]:
                state_now["done"].append(key)
            finished = key not in state_now["done"][:-1] and len(state_now["done"]) >= len(questions)
            if finished:
                mark_active(self.progress)
            self.save()
            show_score()
            return finished

        def wrong(key):
            first_click(key, False)
            self.save()

        for number, question in enumerate(questions, 1):
            block = self.build_question(question, number, question["key"] in state["done"],
                                        lambda k=question["key"]: right(k), lambda k=question["key"]: wrong(k))
            block.pack_start(self.plain(f"From: {question['lesson']}", "muted"), False, False, 0)
            page.pack_start(block, False, False, 0)
        show_score()
        page.pack_start(score, False, False, 0)
        return self.scroll_page(page)

    def build_test_page(self, label, test):
        Gtk = self.Gtk
        from gi.repository import GLib

        status = Gtk.Label()
        status.get_style_context().add_class("details-status")
        page = self.page_top(test["title"], f"{label}  ·  {CHECK_TEXT}", None, status)

        story = self.box(6)
        self.paragraphs(story, test.get("story", ""))
        page.pack_start(story, False, False, 0)

        if test.get("steps"):
            task = self.box(8, "task-box")
            title = Gtk.Label(label="YOUR TASK", xalign=0)
            title.get_style_context().add_class("task-heading")
            task.pack_start(title, False, False, 0)
            for number, step in enumerate(test["steps"], 1):
                row = Gtk.Box(spacing=8)
                mark = Gtk.Label(label=f"{number}", xalign=0.5)
                mark.set_size_request(18, -1)
                mark.set_valign(Gtk.Align.START)
                mark.get_style_context().add_class("task-number")
                row.pack_start(mark, False, False, 0)
                row.pack_start(self.rich(step, "body-text", 64), True, True, 0)
                task.pack_start(row, False, False, 0)
            if test.get("example"):
                example_title = Gtk.Label(label="EXAMPLE", xalign=0)
                example_title.get_style_context().add_class("task-heading")
                task.pack_start(example_title, False, False, 4)
                block = Gtk.Box()
                block.get_style_context().add_class("code-block")
                import html
                block.pack_start(self.text(html.escape(test["example"], quote=False), "code", 80), True, True, 0)
                task.pack_start(block, False, False, 0)
            page.pack_start(task, False, False, 0)

        code = self.box(8)
        code.pack_start(self.heading("Your code"), False, False, 0)
        names = ", ".join(f"`{f['name']}` ({f['class']})" for f in test_files(test))
        if test.get("runner") == "program":
            missing = (f" **{LANG['name']} isn't installed yet**, so Run and Check won't work until you "
                       f"install it: `{LANG.get('install', '')}`." if interpreter_missing(LANG["id"]) else "")
            code.pack_start(self.rich(f"Write your program in {names}. It saves as you type. **Run** runs it and "
                                      f"shows what it prints.{missing}", "muted", 80), False, False, 0)
        else:
            code.pack_start(self.rich(f"Each tab is one script: {names}. They all sit in the same folder, so "
                                      "`script.Parent` followed by a tab's name finds that script. Your code saves "
                                      "as you type.", "muted", 80), False, False, 0)
        skip = ("A Folder", "A Script", "A ModuleScript", "Three ModuleScripts", "A LocalScript")
        extra = [line for line in test.get("create", []) if not line.startswith(skip)]
        if extra:
            self.bullets(code, extra)
        editor, save_all, current_file = self.build_editor(test)
        code.pack_start(editor, False, False, 0)

        checked = self.box(7)
        checked.pack_start(self.heading("Checklist"), False, False, 0)
        requirement_rows = {}
        for requirement in test["requirements"]:
            row = Gtk.Box(spacing=7)
            icon = self.icon("unchecked")
            row.pack_start(icon, False, False, 0)
            texts = self.box(2)
            texts.pack_start(self.rich(requirement["text"], "body-text", 66), False, False, 0)
            details = self.box(2)
            texts.pack_start(details, False, False, 0)
            row.pack_start(texts, True, True, 0)
            checked.pack_start(row, False, False, 0)
            requirement_rows[requirement["id"]] = (icon, details)
        page.pack_start(checked, False, False, 0)

        hints = test.get("hints", [])
        if hints:
            hint_box = self.box(7)
            hint_box.pack_start(self.heading("Hints"), False, False, 0)
            shown = self.box(6)
            hint_box.pack_start(shown, False, False, 0)
            hint_button = self.row_button("hint-button", Gtk.Label(label="Show hint 1"))
            hint_button.set_halign(Gtk.Align.START)
            count = {"shown": 0}

            def show_hint(button):
                index = count["shown"]
                if index >= len(hints):
                    return
                reveal = Gtk.Revealer()
                reveal.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
                reveal.set_transition_duration(320)
                reveal.add(self.rich(f"**Hint {index + 1}.** {hints[index]}"))
                shown.pack_start(reveal, False, False, 0)
                shown.show_all()
                GLib.idle_add(lambda: reveal.set_reveal_child(True) or False)
                self.effects.sound("tick")
                count["shown"] += 1
                if count["shown"] >= len(hints):
                    button.hide()
                else:
                    button.get_child().set_text(f"Show hint {count['shown'] + 1}")

            hint_button.connect("clicked", show_hint)
            hint_box.pack_start(hint_button, False, False, 0)
            page.pack_start(hint_box, False, False, 0)

        actions = self.box(8)
        check_button = self.row_button("check-button", Gtk.Label(label="Check my work"))
        run_button = self.row_button("run-button", Gtk.Label(label="▶  Run"))
        buttons = Gtk.Box(spacing=8)
        buttons.pack_start(check_button, False, False, 0)
        buttons.pack_start(run_button, False, False, 0)
        line = self.plain("Write your code above, then press Check my work.", "status-line")
        actions.pack_start(buttons, False, False, 0)
        output = self.box(2, "run-output")
        output.set_no_show_all(True)
        actions.pack_start(output, False, False, 0)
        actions.pack_start(line, False, False, 0)
        scanner = Gtk.ProgressBar()
        scanner.get_style_context().add_class("scanner")
        scanner.set_pulse_step(0.06)
        scanner.set_no_show_all(True)
        actions.pack_start(scanner, False, False, 0)
        scanning = {"source": None}
        banner = self.box(6)
        banner.set_no_show_all(True)
        banner_text = self.rich("", "body-text")
        banner.pack_start(banner_text, False, False, 0)
        notes = self.box(4)
        banner.pack_start(notes, False, False, 0)
        actions.pack_start(banner, False, False, 0)
        page.pack_start(code, False, False, 0)
        page.pack_start(actions, False, False, 0)

        def show_status():
            entry = self.progress["tests"].get(test["id"], {})
            if entry.get("passed"):
                status.set_markup(f"<span foreground='{GREEN}'>✔ Passed</span>")
            elif entry.get("attempts"):
                status.set_markup(f"<span foreground='{ORANGE}'>Tried {entry['attempts']}×</span>")
            else:
                status.set_markup(f"<span foreground='{GRAY}'>Not tried</span>")

        def fill_row(icon, details, row, state):
            for child in details.get_children():
                details.remove(child)
            icon.set_markup(icon_markup(state))
            for detail in (row or {}).get("details", [])[:4]:
                details.pack_start(self.rich(detail, "detail-text", 64), False, False, 0)
            details.show_all()

        def fill_banner(result, newly_passed):
            context = banner.get_style_context()
            context.remove_class("banner-pass")
            context.remove_class("banner-fail")
            context.add_class("banner-pass" if result.get("passed") else "banner-fail")
            message = result.get("message") or ""
            if newly_passed:
                message = "**Passed.** " + self.unlock_message(test)
            banner_text.set_markup(self.bar.rich_text(message))
            for child in notes.get_children():
                notes.remove(child)
            for note in result.get("notes", []):
                notes.pack_start(self.rich(note, "muted", 66), False, False, 0)
            if result.get("detail") and not result.get("ran"):
                notes.pack_start(self.plain(f"Details: {str(result['detail'])[:400]}", "muted"), False, False, 0)
            banner.set_no_show_all(False)
            banner.show_all()

        def celebrate_unlock():
            chapters = self.course["chapters"]
            for index, chapter in enumerate(chapters):
                if chapter["test"] == test["id"]:
                    entry = self.chapters[index + 1] if index + 1 < len(self.chapters) else self.final
                    if entry:
                        self.effects.flash(entry["button"], "unlocked", 2.6)
                        self.effects.burst(entry["button"], "unlock")
                        self.effects.sound("unlock")
            return False

        def show_result(result, newly_passed, animate=False):
            by_id = {row["id"]: row for row in result.get("rows", [])}
            rows = []
            for requirement_id, (icon, details) in requirement_rows.items():
                row = by_id.get(requirement_id)
                state = {"pass": "done", "fail": "fail"}.get(row["status"], "unchecked") if row else "unchecked"
                rows.append((icon, details, row, state))
            if not animate:
                for icon, details, row, state in rows:
                    fill_row(icon, details, row, state)
                fill_banner(result, newly_passed)
                return
            banner.hide()
            for icon, details, _row, _state in rows:
                fill_row(icon, details, None, "unchecked")
            for index, (icon, details, row, state) in enumerate(rows):
                def land(icon=icon, details=details, row=row, state=state):
                    fill_row(icon, details, row, state)
                    self.effects.flash(icon, "pop" if state == "done" else "pop-fail", 0.9)
                    if state == "done":
                        self.effects.sound("tick")
                    return False
                GLib.timeout_add(150 + index * 170, land)

            def reveal():
                fill_banner(result, newly_passed)
                if newly_passed:
                    self.effects.flash(banner, "celebrate", 2.1)
                    self.effects.burst(banner, "pass")
                    self.effects.sound("pass")
                    GLib.timeout_add(1200, celebrate_unlock)
                elif result.get("passed"):
                    self.effects.burst(banner, "correct")
                    self.effects.sound("correct")
                return False

            GLib.timeout_add(260 + len(rows) * 170, reveal)

        def finish(result):
            self.checks_running -= 1
            was_passed = test_passed(self.progress, test["id"])
            record_check(self.progress, test["id"], result)
            self.progress["tests"][test["id"]]["result"] = compact_result(result)
            self.save()
            if scanning["source"]:
                GLib.source_remove(scanning["source"])
                scanning["source"] = None
            scanner.hide()
            show_result(result, result.get("passed") and not was_passed, animate=True)
            show_status()
            line.set_text("")
            check_button.set_sensitive(True)
            if self.close_when_done and self.checks_running == 0:
                self.close_when_done()
            return False

        def show_output(lines):
            import html
            colours = {"print": "#e8e8e8", "warn": ORANGE, "error": RED, "info": GRAY}
            for child in output.get_children():
                output.remove(child)
            for kind, text in lines[:200]:
                markup = f"<span foreground='{colours.get(kind, GRAY)}'>{html.escape(text, quote=False)}</span>"
                output.pack_start(self.text(markup, "output-line", 84), False, False, 0)
            output.set_no_show_all(False)
            output.show_all()
            run_button.set_sensitive(True)
            return False

        def on_run(button):
            import threading
            save_all()
            file = current_file()
            if not file:
                return
            button.set_sensitive(False)
            self.effects.sound("tick")
            threading.Thread(target=lambda: GLib.idle_add(show_output, run_file(test, file)), daemon=True).start()

        run_button.connect("clicked", on_run)

        def on_check(button):
            import threading
            save_all()
            button.set_sensitive(False)
            self.checks_running += 1
            line.set_text("Starting...")
            self.effects.sound("tick")
            scanner.set_no_show_all(False)
            scanner.show()
            scanning["source"] = GLib.timeout_add(45, lambda: scanner.pulse() or True)

            def work():
                try:
                    result = run_check(test, report=lambda text: GLib.idle_add(line.set_text, text))
                except Exception as problem:  # Keep the panel alive whatever happens
                    result = failure("The checker crashed. That's a problem with the course, not your code.",
                                     repr(problem))
                GLib.idle_add(finish, result)

            threading.Thread(target=work, daemon=True).start()

        check_button.connect("clicked", on_check)
        show_status()
        last = self.progress["tests"].get(test["id"], {}).get("result")
        if last:
            show_result(last, False)
            line.set_text("Showing your last check.")
        return self.scroll_page(page)

    def unlock_message(self, test):
        chapters = self.course["chapters"]
        for index, chapter in enumerate(chapters):
            if chapter["test"] == test["id"]:
                if index + 1 < len(chapters):
                    following = chapters[index + 1]
                    return f"Chapter {following.get('number', index + 2)}, {following['title']}, is now unlocked."
                return f"The {self.course['final'].get('title', 'final exam')} is now unlocked."
        return "Great work."


def open_popup(app, Gtk, bar):
    import gi
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import Gdk, GLib, GtkLayerShell

    bar.apply_css(Gtk)
    provider = Gtk.CssProvider()
    provider.load_from_data(COURSE_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
    )

    window = Gtk.ApplicationWindow(application=app)
    window.set_visual(window.get_screen().get_rgba_visual())
    window.set_app_paintable(True)
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_exclusive_zone(window, -1)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.EXCLUSIVE)
    for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                 GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
        GtkLayerShell.set_anchor(window, edge, True)

    progress = load_progress()
    overlay = Gtk.Overlay()
    # A windowless widget, so the particle layer can draw over the panel but never catches clicks
    canvas = Gtk.Box()
    effects = Effects(Gtk, overlay, canvas, progress)
    panel = Panel(Gtk, bar, load_course(), progress, effects)
    menu = Gtk.EventBox()
    menu.set_visible_window(False)
    menu.set_valign(Gtk.Align.START)
    menu.add(panel.build_panel())
    menu.connect("button-press-event", lambda *_: True)

    left = bar.menu_left_edge()
    row = Gtk.Box()
    row.set_margin_start(min(left, bar.SHADOW_ROOM))
    row.set_margin_end(bar.SHADOW_ROOM)
    row.set_margin_bottom(44)
    row.pack_start(menu, False, False, 0)
    row.pack_start(panel.pane["area"], False, False, 0)

    drop = Gtk.Revealer()
    drop.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
    drop.set_transition_duration(bar.OPEN_DURATION)
    drop.set_margin_start(max(left - bar.SHADOW_ROOM, 0))
    drop.set_margin_top(bar.BAR_HEIGHT)
    drop.set_halign(Gtk.Align.START)
    drop.set_valign(Gtk.Align.START)
    drop.add(row)

    import cairo
    state = {"closing": False, "locked": progress.get("settings", {}).get("locked", False)}

    def shape(*_):
        # When locked, only the panel itself takes clicks; everywhere else goes to the windows below
        if not state["locked"]:
            window.input_shape_combine_region(None)
            return
        region = cairo.Region()
        for widget in (menu, panel.pane["area"]):
            if not widget.get_mapped():
                continue
            spot = widget.translate_coordinates(window, 0, 0)
            size = widget.get_allocation()
            if spot and size.width > 1 and size.height > 1:
                region.union(cairo.RectangleInt(spot[0], spot[1], size.width, size.height))
        window.input_shape_combine_region(region)

    def set_locked(locked):
        state["locked"] = locked
        GtkLayerShell.set_keyboard_mode(
            window, GtkLayerShell.KeyboardMode.ON_DEMAND if locked else GtkLayerShell.KeyboardMode.EXCLUSIVE
        )
        shape()

    panel.on_lock = set_locked

    def close_panel(*_):
        if state["closing"]:
            return
        state["closing"] = True
        drop.set_transition_duration(bar.CLOSE_DURATION)
        drop.set_reveal_child(False)
        if panel.checks_running:
            # Let a running check finish, so Studio isn't left in a playtest
            GLib.timeout_add(bar.CLOSE_DURATION + 30, window.hide)
            panel.close_when_done = window.destroy
        else:
            GLib.timeout_add(bar.CLOSE_DURATION + 30, window.destroy)

    def on_key(_window, event):
        if event.keyval == Gdk.KEY_Escape and not state["locked"]:
            close_panel()

    def on_click(*_):
        if not state["locked"]:
            close_panel()

    overlay.add(drop)
    overlay.add_overlay(canvas)
    overlay.set_overlay_pass_through(canvas, True)
    window.add(overlay)
    window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
    window.connect("button-press-event", on_click)
    window.connect("key-press-event", on_key)
    window.connect("map-event", lambda *_: drop.set_reveal_child(True))
    def queue_shape(*_):
        # Slides move the panel without a new allocation, so look again once they settle
        shape()
        GLib.timeout_add(bar.OPEN_DURATION + 60, lambda: shape() and False)

    menu.connect("size-allocate", queue_shape)
    drop.connect("notify::child-revealed", queue_shape)
    for signal in ("size-allocate", "map", "unmap"):
        panel.pane["area"].connect(signal, queue_shape)
    if state["locked"]:
        GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.ON_DEMAND)
    window.show_all()
    window.connect("realize", shape)
    shape()
    return close_panel


def run_popup(language):
    use_language(language)
    try:
        LEARNING_PROGRESS.mkdir(parents=True, exist_ok=True)
        LAST_LANGUAGE.write_text(language)
    except OSError:
        pass
    bar = load_bar()
    Gtk = bar.load_gtk()
    from gi.repository import Gio

    app_id = "local.waybar.LuauCourse" if language == "luau" else f"local.waybar.Learning.{language}"
    app = Gtk.Application(application_id=app_id, flags=Gio.ApplicationFlags.FLAGS_NONE)
    state = {"close": None}

    def activate(application):
        if state["close"]:
            state["close"]()
        else:
            state["close"] = open_popup(application, Gtk, bar)

    app.connect("activate", activate)
    app.run([sys.argv[0]])


# The hub: every language, with your progress in each ------------------------------------------

HUB_WIDTH = 360
HUB_CSS = b"""
.hub-icon { background-color: #5e5ce6; border-radius: 11px; min-width: 40px; min-height: 40px; font-size: 17px; }
.hub-title { font-size: 15px; font-weight: 700; }
.language { padding: 8px 10px; transition: background-color 140ms ease-out; }
.language.hover { background-color: rgba(255, 255, 255, 0.12); }
.language.soon { opacity: 0.45; }
.language.last { border-color: rgba(10, 132, 255, 0.55); }
.badge { border-radius: 9px; min-width: 34px; min-height: 34px; font-size: 12px; font-weight: 800; }
.language-name { font-size: 12px; font-weight: 600; }
.language-sub { font-size: 10px; color: rgba(255, 255, 255, 0.55); }
.language-warn { font-size: 10px; color: #ff9f0a; }
progressbar.thin trough { min-height: 3px; border-radius: 2px; background-color: rgba(255, 255, 255, 0.1); border: none; }
progressbar.thin progress { min-height: 3px; border-radius: 2px; background-color: #30d158; border: none; }
"""


def language_summary(language):
    """(fraction done, text) for the hub, or (None, text) for a course that isn't written yet."""
    if not language_ready(language):
        return None, "Coming soon"
    use_language(language)
    try:
        course, progress = load_course(), load_progress()
    except (OSError, ValueError, KeyError):
        return None, "Couldn't read this course"
    lessons = all_lesson_ids(course)
    tests = all_test_ids(course)
    done = sum(lesson_done(progress, l) for l in lessons)
    passed = sum(test_passed(progress, t) for t in tests)
    if not done and not passed:
        return 0.0, f"{len(course['chapters'])} chapters  ·  {len(lessons)} lessons  ·  {len(tests)} tests"
    return course_fraction(course, progress), f"{done}/{len(lessons)} lessons  ·  {passed}/{len(tests)} tests"


def build_hub(cc, Gtk, GLib, close):
    cc.ICONS.update({"cap": CAP_ICON})
    panel = cc.css_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "panel")
    panel.set_size_request(HUB_WIDTH, -1)
    try:
        last = LAST_LANGUAGE.read_text().strip()
    except OSError:
        last = "luau"

    header = cc.css_classes(Gtk.Box(spacing=11), "tile")
    icon = cc.icon_label("cap", "hub-icon")
    icon.set_valign(Gtk.Align.CENTER)
    header.pack_start(icon, False, False, 0)
    titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    titles.set_valign(Gtk.Align.CENTER)
    titles.pack_start(cc.css_classes(Gtk.Label(label="Learning", xalign=0), "hub-title"), False, False, 0)
    ready = [language for language in LANGUAGES if language_ready(language)]
    titles.pack_start(cc.css_classes(Gtk.Label(
        label=f"{len(ready)} of {len(LANGUAGES)} courses ready  ·  pick a language", xalign=0), "tile-sub"),
        False, False, 0)
    header.pack_start(titles, True, True, 0)
    panel.pack_start(header, False, False, 0)

    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    order = sorted(LANGUAGES, key=lambda l: (not language_ready(l), l != last))
    for language in order:
        info = LANGUAGES[language]
        fraction, text = language_summary(language)
        row = cc.css_classes(Gtk.Box(spacing=10), "tile", "language")
        if fraction is None:
            cc.css_classes(row, "soon")
        if language == last and fraction is not None:
            cc.css_classes(row, "last")
        badge = cc.css_classes(Gtk.Label(label=info["badge"]), "badge")
        badge.set_valign(Gtk.Align.CENTER)
        provider = Gtk.CssProvider()
        provider.load_from_data(f"label {{ background-color: {info['colour']}; }}".encode())
        badge.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        row.pack_start(badge, False, False, 0)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        words.set_valign(Gtk.Align.CENTER)
        words.pack_start(cc.css_classes(Gtk.Label(label=info["name"], xalign=0), "language-name"), False, False, 0)
        sub_text = info["blurb"] if fraction is None else text
        words.pack_start(cc.css_classes(Gtk.Label(label=sub_text, xalign=0), "language-sub"), False, False, 0)
        if fraction is not None and interpreter_missing(language):
            warn = cc.css_classes(Gtk.Label(label=f"Install to run code: {info.get('install', '')}", xalign=0),
                                  "language-warn")
            warn.set_selectable(False)
            words.pack_start(warn, False, False, 0)
        if fraction:
            bar = cc.css_classes(Gtk.ProgressBar(), "thin")
            bar.set_fraction(fraction)
            words.pack_start(bar, False, False, 1)
        row.pack_start(words, True, True, 0)
        if fraction is None:
            row.pack_start(cc.css_classes(Gtk.Label(label="Soon"), "language-sub"), False, False, 0)
            rows.pack_start(row, False, False, 0)
            continue
        area = Gtk.EventBox()
        area.set_visible_window(False)
        area.add(row)

        def on_hover(_area, event, inside, row=row):
            if event.detail != cc.Gdk.NotifyType.INFERIOR:
                cc.set_class(row, "hover", inside)

        def on_click(_area, event, language=language):
            if event.button == 1:
                cc.spawn(sys.executable, str(Path(__file__).resolve()), "--popup", language)
                close()

        area.connect("enter-notify-event", on_hover, True)
        area.connect("leave-notify-event", on_hover, False)
        area.connect("button-release-event", on_click)
        rows.pack_start(area, False, False, 0)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_propagate_natural_height(True)
    scroller.set_max_content_height(520)
    scroller.add(rows)
    panel.pack_start(scroller, False, False, 0)
    rows.show_all()
    scroller.set_min_content_height(min(rows.get_preferred_height()[1], 520))
    return panel, [], lambda: None


def run_hub():
    import importlib.util
    spec = importlib.util.spec_from_file_location("clips", HERE / "clips.py")
    clips = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(clips)
    clips.run_panel("local.waybar.Learning", build_hub, HUB_WIDTH, HUB_CSS)


def argument(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv and sys.argv.index(flag) + 1 < len(sys.argv) else default


if __name__ == "__main__":
    if "--hub" in sys.argv:
        run_hub()
    elif "--popup" in sys.argv:
        language = argument("--popup", "luau")
        run_popup(language if language in LANGUAGES else "luau")
    elif "--check" in sys.argv:
        use_language(argument("--course", "luau"))
        print_check(argument("--check"))
    else:
        print_button()
