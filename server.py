#!/usr/bin/env python3
"""Task Board - local server.

    python3 server.py            then open http://127.0.0.1:8765

Everything you do on the page (add, tick, star, edit, delete, drag, new project...)
is written straight back to the markdown files in the data folder, so the files
stay the single source of truth and you can also edit them by hand.

Data folder: ./data next to this file, or set TASKBOARD_DATA=/path/to/folder.
On first run an empty board is created there from ./starter.
Binds to localhost only - nothing is exposed to the network.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("TASKBOARD_DATA") or os.path.join(HERE, "data"))
PORT = int(os.environ.get("TASKBOARD_PORT", "8765"))
sys.dont_write_bytecode = True
# Requests run in parallel (a browser's idle spare connection would otherwise stall
# every click); writes still go one at a time through this lock.
WRITE_LOCK = threading.Lock()


def first_run():
    """Create the data folder from ./starter if it doesn't exist yet."""
    if not os.path.exists(os.path.join(ROOT, "tasks.md")):
        os.makedirs(ROOT, exist_ok=True)
        for name in ("tasks.md",):
            shutil.copy(os.path.join(HERE, "starter", name), os.path.join(ROOT, name))
        os.makedirs(os.path.join(ROOT, "projects"), exist_ok=True)
        print("Created a new board in %s" % ROOT)


first_run()
os.environ["TASKBOARD_DATA"] = ROOT
_spec = importlib.util.spec_from_file_location("board", os.path.join(HERE, "board.py"))
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)


def page():
    # Re-read the board module on every view so area changes show immediately.
    _spec.loader.exec_module(bt)
    return bt.render(bt.read_tasks(), bt.read_projects(), True, server=True)


def add_area(name):
    name = _clean_line(name)
    if len(name) > 40 or "#" in name:
        raise ValueError("keep area names short and plain")
    if name in bt.AREA_ORDER:
        raise ValueError("that area already exists")
    path = os.path.join(ROOT, "tasks.md")
    text = io.open(path, encoding="utf-8").read().rstrip("\n")
    io.open(path, "w", encoding="utf-8").write(text + "\n\n## %s\n" % name)
    return name


def toggle(rel_file, raw, done):
    """Flip one checkbox in the markdown, matching the exact source line."""
    # never let a posted path escape the data folder
    path = os.path.realpath(os.path.join(ROOT, rel_file))
    if not path.startswith(os.path.realpath(ROOT) + os.sep):
        raise ValueError("path outside the data folder")
    if not os.path.exists(path):
        raise ValueError(f"no such file: {rel_file}")

    text = io.open(path, encoding="utf-8").read()
    want = "x" if done else " "
    have = " " if done else "x"
    pat = re.compile(r"^(\s*)- \[%s\] %s\s*$" % (re.escape(have), re.escape(raw)),
                     re.M)
    new, n = pat.subn(lambda m: "%s- [%s] %s" % (m.group(1), want, raw), text, count=1)
    if not n:
        # already in the requested state? then there is nothing to do
        if re.search(r"^\s*- \[%s\] %s\s*$" % (re.escape(want), re.escape(raw)),
                     text, re.M):
            return False
        raise ValueError("could not find that line — regenerate and retry")
    io.open(path, "w", encoding="utf-8").write(new)
    return True


def star(rel_file, raw):
    """Toggle the star on one open task line: adds {star YYYY-MM-DD}, or removes it.
    A star stays until clicked off."""
    import datetime
    path = os.path.realpath(os.path.join(ROOT, rel_file))
    if not path.startswith(os.path.realpath(ROOT) + os.sep):
        raise ValueError("path outside the data folder")
    if not os.path.exists(path):
        raise ValueError(f"no such file: {rel_file}")
    today = datetime.date.today().isoformat()
    text = io.open(path, encoding="utf-8").read()
    pat = re.compile(r"^(\s*)- \[ \] %s\s*$" % re.escape(raw), re.M)
    m = pat.search(text)
    if not m:
        raise ValueError("could not find that line — regenerate and retry")
    was_on = re.search(r"\{star[^}]*\}", raw) is not None
    base = re.sub(r"\s*\{star[^}]*\}", "", raw).rstrip()
    new_raw = base if was_on else base + " {star %s}" % today
    text = text[:m.start()] + "%s- [ ] %s" % (m.group(1), new_raw) + text[m.end():]
    io.open(path, "w", encoding="utf-8").write(text)
    return not was_on


def _task_path(rel_file):
    path = os.path.realpath(os.path.join(ROOT, rel_file or ""))
    root = os.path.realpath(ROOT)
    ok = (path == os.path.join(root, "tasks.md") or
          (path.startswith(os.path.join(root, "projects") + os.sep) and path.endswith(".md")))
    if not ok:
        raise ValueError("only tasks.md and project files can be changed here")
    if not os.path.exists(path):
        raise ValueError(f"no such file: {rel_file}")
    return path


def _find_line(lines, raw):
    pat = re.compile(r"^\s*- \[( |x)\] %s\s*$" % re.escape(raw))
    for i, l in enumerate(lines):
        if pat.match(l):
            return i
    raise ValueError("could not find that line — reload and retry")


def _clean_line(text):
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("empty text")
    if len(text) > 300:
        raise ValueError("too long")
    if text.lstrip().startswith(("- ", "#", "* ")):
        raise ValueError("must be a single plain line")
    return text


def delete_line(rel_file, raw):
    import datetime
    path = _task_path(rel_file)
    lines = io.open(path, encoding="utf-8").read().split("\n")
    i = _find_line(lines, raw)
    gone = lines.pop(i)
    arch = os.path.join(ROOT, "archive", "deleted-tasks.md")
    os.makedirs(os.path.dirname(arch), exist_ok=True)
    new = not os.path.exists(arch)
    with io.open(arch, "a", encoding="utf-8") as fh:
        if new:
            fh.write("# Deleted tasks\n\nEvery task or step deleted from the dashboard lands here "
                     "first, with the date and the file it came from. Copy a line back to restore it.\n\n")
        fh.write("%s · %s · %s\n" % (datetime.date.today().isoformat(), rel_file, gone.strip()))
    io.open(path, "w", encoding="utf-8").write("\n".join(lines))
    return gone.strip()


def edit_line(rel_file, raw, new_text):
    path = _task_path(rel_file)
    new_text = _clean_line(new_text)
    lines = io.open(path, encoding="utf-8").read().split("\n")
    i = _find_line(lines, raw)
    m = re.match(r"^(\s*- \[( |x)\] )", lines[i])
    lines[i] = m.group(1) + new_text
    io.open(path, "w", encoding="utf-8").write("\n".join(lines))
    return lines[i].strip()


def move_step(rel_file, raw, direction):
    """Swap a project step with its neighbor under ## Steps (direction -1 up, +1 down)."""
    path = _task_path(rel_file)
    if not os.path.basename(os.path.dirname(path)) == "projects":
        raise ValueError("only project steps can be moved this way")
    lines = io.open(path, encoding="utf-8").read().split("\n")
    i = _find_line(lines, raw)
    box = re.compile(r"^\s*- \[( |x)\] ")
    j = i + (1 if direction > 0 else -1)
    if j < 0 or j >= len(lines) or not box.match(lines[j]):
        return False  # already first/last
    lines[i], lines[j] = lines[j], lines[i]
    io.open(path, "w", encoding="utf-8").write("\n".join(lines))
    return True


def new_project(name, done_when, area, kind="project"):
    """New project or goal file. Goals get kind: goal and the next order number."""
    name = _clean_line(name)
    done_when = " ".join((done_when or "").split()) or "[?] say what finished looks like"
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    if not slug:
        raise ValueError("name needs letters or numbers")
    if kind == "goal":
        slug = "goal-" + slug
    path = os.path.join(ROOT, "projects", slug + ".md")
    if os.path.exists(path):
        raise ValueError("a project file with that name already exists")
    area = area if area in bt.AREA_ORDER and area not in ("Goals", "Projects") else bt.AREA_ORDER[0]
    extra = ""
    if kind == "goal":
        orders = [p.get("order", 99) for p in bt.read_projects() if p.get("kind") == "goal"]
        extra = "kind: goal\norder: %d\n" % (max([o for o in orders if o < 99] or [0]) + 1)
    io.open(path, "w", encoding="utf-8").write(
        "---\nproject: %s\narea: %s\nstatus: active\n%sdeadline: none\n---\n\n# %s\n\n"
        "**Done when:** %s\n\n## Steps\n\n## Notes\n\nCreated from the dashboard.\n"
        % (name, area, extra, name, done_when))
    return "projects/" + slug + ".md"


def rename_project(rel_file, name):
    """Change the display name (frontmatter `project:` and the # heading). The filename
    stays put so [[wikilinks]] to it keep working."""
    path = _task_path(rel_file)
    if "/projects/" not in path.replace(os.sep, "/"):
        raise ValueError("not a project file")
    name = _clean_line(name)
    text = io.open(path, encoding="utf-8").read()
    text, n = re.subn(r"^project: .*$", "project: " + name.replace("\\", "\\\\"), text, count=1, flags=re.M)
    if not n:
        raise ValueError("project file has no 'project:' line")
    text = re.sub(r"^# .*$", "# " + name.replace("\\", "\\\\"), text, count=1, flags=re.M)
    io.open(path, "w", encoding="utf-8").write(text)
    return name


def archive_project(rel_file):
    """Move a project file to archive/projects/ - nothing is deleted."""
    path = _task_path(rel_file)
    if "/projects/" not in path.replace(os.sep, "/"):
        raise ValueError("not a project file")
    dest_dir = os.path.join(ROOT, "archive", "projects")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    if os.path.exists(dest):
        raise ValueError("archive/projects/ already has a file with that name")
    os.rename(path, dest)
    return "archive/projects/" + os.path.basename(path)


def add_step(rel_file, text, est):
    """Append a step under `## Steps` in a project file ."""
    text = " ".join((text or "").split())
    est = (est or "30m").strip()
    path = os.path.realpath(os.path.join(ROOT, rel_file or ""))
    proj_dir = os.path.realpath(os.path.join(ROOT, "projects")) + os.sep
    if not path.startswith(proj_dir) or not path.endswith(".md"):
        raise ValueError("not a project file")
    if not os.path.exists(path):
        raise ValueError(f"no such file: {rel_file}")
    if not text:
        raise ValueError("empty step")
    if len(text) > 300:
        raise ValueError("step too long")
    if not re.fullmatch(r"\d+(h\d*|m|h\d*m)?", est):
        raise ValueError("estimate should look like 30m, 1h or 1h30")
    if "\n" in text or text.lstrip().startswith(("- ", "#", "* ")):
        raise ValueError("step must be a single plain line")
    line = "- [ ] %s {%s}" % (text, est)
    lines = io.open(path, encoding="utf-8").read().split("\n")
    try:
        h = next(i for i, l in enumerate(lines) if re.match(r"^## Steps\s*$", l))
    except StopIteration:
        raise ValueError("this project has no ## Steps section")
    end = next((i for i in range(h + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    last = max((i for i in range(h + 1, end) if re.match(r"^- \[( |x)\] ", lines[i])), default=h)
    lines.insert(last + 1, line)
    io.open(path, "w", encoding="utf-8").write("\n".join(lines))
    return line


def add_task(area, text, est, due=None, repeat=None):
    """Append a task under its `## Area` heading in tasks.md.

    Estimates are not optional here. Every task needs one or the board totals
    and sorting break, and people rarely add one - so the form defaults to
    30m rather than letting a task through without it.
    """
    area = (area or "").strip()
    text = " ".join((text or "").split())
    est = (est or "30m").strip()
    if area not in bt.AREA_ORDER:
        raise ValueError("unknown area: %s" % area)
    if not text:
        raise ValueError("empty task")
    if len(text) > 300:
        raise ValueError("task too long")
    if not re.fullmatch(r"\d+(h\d*|m|h\d*m)?", est):
        raise ValueError("estimate should look like 30m, 1h or 1h30")
    # Don't let a posted string inject its own checkbox or heading.
    if "\n" in text or text.lstrip().startswith(("- ", "#", "* ")):
        raise ValueError("task must be a single plain line")

    path = os.path.join(ROOT, "tasks.md")
    lines = io.open(path, encoding="utf-8").read().split("\n")
    try:
        start = next(i for i, l in enumerate(lines)
                     if l.strip().lower() == ("## %s" % area).lower())
    except StopIteration:
        raise ValueError("no '## %s' heading in tasks.md" % area)
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    last = max([i for i in range(start + 1, end)
                if lines[i].strip().startswith("- [")] or [start])
    line = "- [ ] %s {%s}" % (text, est)
    if due:
        import datetime
        datetime.date.fromisoformat(due)  # rejects anything that isn't YYYY-MM-DD
        line += " {due %s}" % due
    if repeat:
        if repeat not in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
            raise ValueError("repeat should be a weekday, e.g. Fri")
        line += " {every %s}" % repeat
    lines.insert(last + 1, line)
    io.open(path, "w", encoding="utf-8").write("\n".join(lines))
    return line


def move_task(raw, area, before=None):
    """Move one open task line in tasks.md - into another `## Area`, and/or to just
    above the task `before` (None = end of the area). Drag to
    re-categorize and to set your working order. File order IS that order."""
    if area not in bt.AREA_ORDER or area in ("Goals", "Projects"):
        raise ValueError("can't move a task into %s" % area)
    path = os.path.join(ROOT, "tasks.md")
    lines = io.open(path, encoding="utf-8").read().split("\n")
    want = "- [ ] %s" % raw.rstrip()
    try:
        i = next(n for n, l in enumerate(lines) if l.rstrip() == want)
    except StopIteration:
        raise ValueError("could not find that task — reload and retry")
    line = lines.pop(i)
    try:
        start = next(n for n, l in enumerate(lines)
                     if l.strip().lower() == ("## %s" % area).lower())
    except StopIteration:
        raise ValueError("no '## %s' heading in tasks.md" % area)
    end = next((n for n in range(start + 1, len(lines))
                if lines[n].startswith("## ")), len(lines))
    at = None
    if before:
        b = "- [ ] %s" % before.rstrip()
        at = next((n for n in range(start + 1, end) if lines[n].rstrip() == b), None)
    if at is None:
        boxes = [n for n in range(start + 1, end) if lines[n].strip().startswith("- [")]
        if boxes:
            at = boxes[-1] + 1
        else:
            at = start + 2 if start + 1 < len(lines) and not lines[start + 1].strip() else start + 1
    lines.insert(at, line)
    io.open(path, "w", encoding="utf-8").write("\n".join(lines))
    return line


ROUTES = {
    "/toggle":          lambda d: toggle(d["file"], d["raw"], bool(d["done"])),
    "/star":            lambda d: star(d["file"], d["raw"]),
    "/add":             lambda d: add_task(d.get("area"), d.get("text"), d.get("est"), d.get("due"), d.get("repeat")),
    "/move":            lambda d: move_task(d.get("raw") or "", d.get("area"), d.get("before")),
    "/add-step":        lambda d: add_step(d.get("file"), d.get("text"), d.get("est")),
    "/delete":          lambda d: delete_line(d.get("file"), d.get("raw")),
    "/edit":            lambda d: edit_line(d.get("file"), d.get("raw"), d.get("text")),
    "/move-step":       lambda d: move_step(d.get("file"), d.get("raw"), int(d.get("dir") or 0)),
    "/new-project":     lambda d: new_project(d.get("name"), d.get("done_when"), d.get("area"),
                                              "goal" if d.get("kind") == "goal" else "project"),
    "/rename-project":  lambda d: rename_project(d.get("file"), d.get("name")),
    "/archive-project": lambda d: archive_project(d.get("file")),
    "/add-area":        lambda d: add_area(d.get("name")),
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/index.html"):
            return self._send(404, "not found")
        try:
            self._send(200, page(), "text/html; charset=utf-8")
        except Exception as e:
            self._send(500, "render failed: %s" % e)

    def do_POST(self):
        fn = ROUTES.get(self.path)
        if not fn:
            return self._send(404, "not found")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100000:
                raise ValueError("request too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            with WRITE_LOCK:
                result = fn(body)
        except Exception as e:
            return self._send(400, str(e))
        print("  %s %s" % (self.path, str(result)[:70]))
        return self._send(200, "ok")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print("Task Board on http://127.0.0.1:%d  (data: %s)" % (PORT, ROOT))
    print("Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
