#!/usr/bin/env python3
"""Task Board - renders tasks.md + projects/*.md as a dashboard.

The markdown files are the only source of truth; this module just reads them and
builds the page. server.py serves it live and writes your clicks back to the files.
Nothing here needs editing for normal use.
"""
import datetime as dt
import glob
import html
import io
import os
import re

ROOT = os.path.abspath(os.environ.get("TASKBOARD_DATA") or
                       os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
TODAY = dt.date.today()
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# ---------------------------------------------------------------- parsing

def parse_est(s):
    """'1h30' -> 90 minutes. '45m' -> 45. '2h' -> 120."""
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m?)?", s.strip())
    if not m or not (m.group(1) or m.group(2)):
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def parse_tags(text):
    """Pull {..} tags off a task line. Returns (clean_title, meta)."""
    meta = {"est": None, "due": None, "rrule_day": None, "rrule_time": None,
            "ongoing": False, "prog": None, "last": None, "snooze": None, "star": None}
    for tag in re.findall(r"\{([^}]*)\}", text):
        t = tag.strip()
        if t.lower().startswith("star"):
            # {star YYYY-MM-DD}: starred for that day only
            try:
                meta["star"] = dt.date.fromisoformat(t.split()[1])
            except (IndexError, ValueError):
                pass
        elif t.lower().startswith("snooze"):
            # hide until an explicit date, regardless of the recurrence
            try:
                meta["snooze"] = dt.date.fromisoformat(t.split()[1])
            except (IndexError, ValueError):
                pass
        elif t.lower().startswith("done"):
            # a recurring task is never "finished" - it's done *for this period*,
            # so record the date and leave it open rather than deleting it
            try:
                meta["last"] = dt.date.fromisoformat(t.split()[1])
            except (IndexError, ValueError):
                pass
        elif t.lower().startswith("due"):
            try:
                meta["due"] = dt.date.fromisoformat(t.split()[1])
            except (IndexError, ValueError):
                pass
        elif t.lower().startswith("ongoing"):
            meta["ongoing"] = True
            frac = re.search(r"(\d+)\s*/\s*(\d+)", t)
            if frac:
                meta["prog"] = (int(frac.group(1)), int(frac.group(2)))
        elif t.lower().startswith("every"):
            parts = t.split()
            if len(parts) > 1 and parts[1][:3].lower() in DAYS:
                meta["rrule_day"] = DAYS[parts[1][:3].lower()]
            if len(parts) > 2 and ":" in parts[2]:
                meta["rrule_time"] = parts[2]
        else:
            e = parse_est(t)
            if e:
                meta["est"] = e
    title = re.sub(r"\s*\{[^}]*\}", "", text).strip()
    return title, meta


def read_tasks():
    path = os.path.join(ROOT, "tasks.md")
    if not os.path.exists(path):
        return []
    out, area = [], "Other"
    for pos, line in enumerate(io.open(path, encoding="utf-8")):
        if line.startswith("## "):
            area = line[3:].strip()
        m = re.match(r"- \[( |x)\] (.+)", line.strip())
        if m:
            title, meta = parse_tags(m.group(2))
            # pos = line number: the order you drag tasks into IS the file order
            out.append(dict(title=title, done=m.group(1) == "x", area=area,
                            project=None, raw=m.group(2).rstrip(),
                            src="tasks.md", pos=pos, **meta))
    return out




def read_projects():
    projs = []
    for path in sorted(glob.glob(os.path.join(ROOT, "projects", "*.md"))):
        if os.path.basename(path).startswith("_"):
            continue
        raw = io.open(path, encoding="utf-8").read()
        fm = re.match(r"---\n(.*?)\n---\n", raw, re.S)
        meta = {}
        if fm:
            for ln in fm.group(1).splitlines():
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    meta[k.strip()] = v.strip()
        steps = []
        # only checkboxes under "## Steps" are project steps - a Follow up:
        # checkbox is not a step and must not count toward progress
        sec = re.search(r"^## Steps\s*$(.*?)(?=^## |\Z)", raw, re.M | re.S)
        step_src = sec.group(1) if sec else raw
        for m in re.finditer(r"^- \[( |x)\] (.+)$", step_src, re.M):
            title, tags = parse_tags(m.group(2))
            if title.lower().startswith("nothing outstanding"):
                continue
            steps.append(dict(title=re.sub(r"^\d+\.\s*", "", title),
                              done=m.group(1) == "x", raw=m.group(2).rstrip(),
                              src=os.path.relpath(path, ROOT), **tags))
        body = raw[fm.end():] if fm else raw
        dw = re.search(r"\*\*Done when:\*\*\s*(.+?)(?=\n\s*\n|\n\*\*)", body, re.S)
        projs.append(dict(
            name=meta.get("project", os.path.basename(path)[:-3]),
            area=meta.get("area", "Other"),
            status=meta.get("status", "active"),
            kind=meta.get("kind", "project"),
            order=int(meta["order"]) if meta.get("order", "").isdigit() else 99,
            file=os.path.relpath(path, ROOT),
            done_when=" ".join(dw.group(1).split()) if dw else "",
            touched=dt.date.fromtimestamp(os.path.getmtime(path)),
            steps=steps,
        ))
    return projs

# ---------------------------------------------------------------- derive

def is_complete(p):
    """Every step ticked. A finished project sinks to the bottom of its board and
    drops out of the stalled ranking - it isn't stalled, it's done."""
    return bool(p["steps"]) and all(st["done"] for st in p["steps"])








def surfaced_steps(projects):
    """Steps that carry a recurrence surface in the task board as well as their
    own lane. One source - the goal file - shown in two places, so they cannot
    drift. Recurrence is the trigger because repeating work is what gets
    forgotten; a one-off step stays in its lane."""
    out = []
    for p in projects:
        if p["status"] != "active":
            continue
        for st in p["steps"]:
            if st["done"] or st.get("rrule_day") is None:
                continue
            out.append(dict(st, area="Goals", goal=p["name"]))
    return out


def project_next_steps(projects):
    """A Projects lane on the main board, each item titled
    "Project name: step", so project work shows up without opening the Projects tab.
    One item per active project: its next open step (the START/STUCK HERE one).
    Recurring steps are skipped here because surfaced_steps already shows them."""
    out = []
    for p in projects:
        if p["status"] != "active" or p.get("kind") == "goal":
            continue
        st = next((s for s in p["steps"] if not s["done"] and not s.get("ongoing")
                   and s.get("rrule_day") is None), None)
        if st:
            out.append(dict(st, area="Projects", project=p["name"],
                            title=f"{p['name']}: {st['title']}"))
    return out


def urgency(t):
    if t["done"]:
        return ("done", "Done")
    if t["due"]:
        d = (t["due"] - TODAY).days
        if d < 0:
            return ("critical", f"{-d}d overdue")
        if d == 0:
            return ("critical", "Today")
        if d <= 2:
            return ("warning", f"in {d}d")
        if d <= 7:
            return ("serious", f"in {d}d")
        return ("none", t["due"].strftime("%-d %b"))
    if t["rrule_day"] is not None:
        return ("none", "weekly")
    return ("none", "")


def due_this_cycle(t):
    """False when a task shouldn't be on the board today.

    Two ways that happens: an explicit `{snooze YYYY-MM-DD}`, or a recurring task
    already done since its last due day.

    A weekly task done this cycle isn't outstanding, so it drops off the board and
    comes back when it's next due - rather than sitting there looking undone.
    """
    if t.get("snooze") and TODAY < t["snooze"]:
        return False
    if t.get("rrule_day") is None or not t.get("last"):
        return True
    occ = TODAY - dt.timedelta(days=(TODAY.weekday() - t["rrule_day"]) % 7)
    return t["last"] < occ


def fmt_est(mins):
    if not mins:
        return ""
    h, m = divmod(mins, 60)
    return (f"{h}h{m:02d}" if h and m else f"{h}h" if h else f"{m}m")

# ---------------------------------------------------------------- calendar

def next_weekday(wd, frm=None, include_today=True):
    frm = frm or TODAY
    d = (wd - frm.weekday()) % 7
    if d == 0 and not include_today:
        d = 7
    return frm + dt.timedelta(days=d)







# ---------------------------------------------------------------- html

CSS = """
*{box-sizing:border-box}
/* an author display rule beats the hidden attribute's UA display:none,
   so .board[hidden] stayed on screen - this makes hidden actually hide */
[hidden]{display:none!important}
html,body{max-width:100%;overflow-x:clip}
.viz-root{
  --surface-1:#fcfcfb; --plane:#f9f9f7; --ink:#0b0b0b; --ink-2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --rule:#c3c2b7; --border:rgba(11,11,11,.10);
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --b250:#86b6ef; --b350:#5598e7; --b450:#2a78d6; --b550:#1c5cab; --b650:#104281;
  --shadow:0 1px 2px rgba(11,11,11,.06),0 4px 10px rgba(11,11,11,.07);
}
body{margin:0;color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-text-size-adjust:100%;background:var(--plane)}
.viz-root{max-width:1000px;margin:0 auto;padding:22px 16px 64px}
h1{font-size:22px;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px;margin:0 0 18px;line-height:1.5}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
  font-weight:600;margin:26px 0 10px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;
  padding:14px 16px;box-shadow:var(--shadow)}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.kpi{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;
  padding:12px 14px;box-shadow:var(--shadow)}
.kpi .v{font-size:30px;font-weight:600;line-height:1.05;letter-spacing:-.02em}
.kpi .l{font-size:11px;color:var(--muted);margin-top:3px;text-transform:uppercase;
  letter-spacing:.05em;line-height:1.35}
.note{font-size:12px;color:var(--muted);margin-top:8px;line-height:1.5}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;flex:none}
.pill{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:600;
  padding:2px 7px;border-radius:99px;white-space:nowrap;border:1px solid transparent}
.pill.critical{color:var(--critical);border-color:var(--critical)}
.pill.warning{color:#8a5d00;border-color:var(--warning)}
.pill.serious{color:#8a4520;border-color:var(--serious)}
.pill.done{color:var(--good);border-color:var(--good)}

/* ---- sticky notes ---- */
.board{display:block}
.swim{margin-bottom:20px}
.swim:last-child{margin-bottom:0}
.swim-head{display:flex;align-items:center;gap:7px;font-size:11px;font-weight:700;
  text-transform:uppercase;letter-spacing:.07em;color:var(--ink-2);margin:0 0 10px;
  padding-bottom:6px;border-bottom:2px solid var(--hue)}
.swim-head .cnt{margin-left:auto;font-weight:400;color:var(--muted);letter-spacing:0;
  font-variant-numeric:tabular-nums;text-transform:none;font-size:12px}
.swim-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}
.sticky{position:relative;background:var(--tint);border-radius:4px;box-shadow:var(--shadow);
  padding:11px 12px 9px;min-height:118px;display:flex;flex-direction:column;
  border-top:3px solid var(--hue);overflow-wrap:anywhere}
.sticky .st-title{font-size:13.5px;line-height:1.35;font-weight:600;flex:1;padding-right:20px}
.chk{position:absolute;top:8px;right:9px;line-height:0}
.chk input{width:15px;height:15px;cursor:pointer;accent-color:var(--b450);margin:0}
.step .chk{top:6px;right:6px}
.step .t{padding-right:16px}
.sticky .st-foot{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:9px}
.sticky .st-est{font-size:12px;color:var(--ink-2);font-variant-numeric:tabular-nums}
.sticky .st-area{display:flex;align-items:center;font-size:11px;color:var(--ink-2);
  margin-top:7px;text-transform:uppercase;letter-spacing:.04em}
/* the blank box at the end of each lane - server mode only */
.sticky.addnew{background:transparent;box-shadow:none;border:1.5px dashed var(--rule);
  border-top:3px dashed var(--hue);cursor:text}
.sticky.addnew:focus-within{background:var(--tint);border-style:solid;box-shadow:var(--shadow)}
.sticky.addnew textarea{flex:1;border:0;background:transparent;resize:none;outline:none;
  font:inherit;font-size:13.5px;line-height:1.35;font-weight:600;color:var(--ink);padding:0}
.sticky.addnew textarea::placeholder{color:var(--muted);font-weight:400}
.sticky.addnew .st-est{color:var(--muted)}
.sticky[draggable=true]{cursor:grab}
.sticky.dragging{opacity:.35}
.swim.dropok .swim-grid{outline:2px dashed var(--hue);outline-offset:4px;border-radius:4px}

/* ---- project lanes ---- */
.lane{margin-bottom:14px}
.lane:last-child{margin-bottom:0}
.lane-head{display:flex;align-items:center;justify-content:space-between;gap:10px;
  margin:0 0 9px;padding-bottom:7px;border-bottom:2px solid var(--rule)}
.lane-head .nm{font-weight:700;font-size:12px;text-transform:uppercase;
  letter-spacing:.07em;color:var(--ink-2);display:flex;align-items:center;gap:7px}
.lane-head .ct{font-size:12px;color:var(--ink-2);font-variant-numeric:tabular-nums;
  white-space:nowrap}
.lane .dw{font-size:12px;color:var(--muted);margin:0 0 11px;line-height:1.45}
.lane .meter{margin:0 0 10px}
.flow{display:flex;flex-wrap:wrap;align-items:stretch;gap:0}
.step{position:relative;background:var(--surface-1);border:1px solid var(--border);border-radius:4px;
  box-shadow:var(--shadow);padding:8px 9px;width:128px;min-height:82px;
  display:flex;flex-direction:column;gap:4px;margin-bottom:10px;overflow-wrap:anywhere}
.step .n{font-size:10px;color:var(--muted);font-weight:700;letter-spacing:.06em}
.step .t{font-size:11.5px;line-height:1.3}
.step.done{background:#f2f7f2;border-color:#cfe4cf}
.step.done .t{color:var(--muted);text-decoration:line-through}
.step.done .n{color:var(--good)}
.step.now{border-color:var(--warning);box-shadow:0 0 0 2px #fdf0cf,var(--shadow);
  background:#fffdf6}
.step .flag{font-size:10px;font-weight:700;color:#8a5d00;letter-spacing:.03em;
  margin-top:auto}
.conn{width:14px;align-self:flex-start;margin-top:41px;height:2px;background:var(--rule);
  flex:none}
.lane .more{font-size:12px;color:var(--muted);align-self:center;margin:0 0 10px 10px}
.step.ong{border-style:dashed}
.step .tag{display:inline-block;font-size:9px;font-weight:700;letter-spacing:.07em;
  text-transform:uppercase;color:var(--ink-2);border:1px solid var(--rule);
  border-radius:99px;padding:1px 6px;margin-top:auto;align-self:flex-start}
.step .prog{font-size:10px;color:var(--ink-2);font-variant-numeric:tabular-nums;
  margin-top:auto}
.step .pbar{height:4px;background:var(--grid);border-radius:2px;overflow:hidden;margin-top:3px}
.step .pbar span{display:block;height:100%;background:var(--b450);border-radius:2px}

/* ---- held up ---- */
.bars{display:flex;flex-direction:column;gap:7px}
.bar{display:grid;grid-template-columns:150px 1fr auto;align-items:center;gap:10px;
  font-size:13px}
.bar .t{background:var(--grid);border-radius:4px;height:14px;overflow:hidden}
.bar .t span{display:block;height:100%;border-radius:4px}
.bar .n{font-variant-numeric:tabular-nums;color:var(--ink-2);font-size:12px;white-space:nowrap}
.meter{height:7px;background:var(--grid);border-radius:4px;margin:2px 0 6px;overflow:hidden}
.meter span{display:block;height:100%;background:var(--b450);border-radius:4px}

/* ---- table (alternate view) ---- */
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
  color:var(--muted);font-weight:600;padding:6px 8px;border-bottom:1px solid var(--rule)}
td{padding:7px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
tr:last-child td{border-bottom:none}
.est{font-variant-numeric:tabular-nums;color:var(--ink-2);white-space:nowrap;font-size:13px}
.areacell{white-space:nowrap}
.areacell span.dot{vertical-align:baseline}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em;
  background:rgba(11,11,11,.05);padding:0 3px;border-radius:3px}

@media(max-width:620px){
  .kpis{grid-template-columns:repeat(2,1fr)}
  .kpi .v{font-size:26px}
  .swim-grid{grid-template-columns:repeat(auto-fill,minmax(142px,1fr));gap:10px}
  .bar{grid-template-columns:96px 1fr auto;font-size:12px;gap:8px}
  .viz-root{padding:16px 12px 48px}
  .step{width:112px}
}
/* Today stars: star what you want done today. A star is
   {star YYYY-MM-DD} on the line and only counts on that date, so yesterday's stars
   clear themselves - nothing to go back and un-star. */
.sticky{position:relative}
.sticky .st-title{padding-right:48px}
.star{position:absolute;top:3px;right:30px;border:none;background:none;cursor:pointer;
  font-size:20px;line-height:1;padding:2px;color:#8a847a}
.star:hover{color:#E0A100}
.star.on{color:#E0A100}
span.star{cursor:default}
.sticky.starred{box-shadow:0 0 0 2px #E0A100,var(--shadow)}
.today{margin:0 0 18px;padding:12px 14px;border:2px solid #E0A100;border-radius:12px;
  background:#FFF8E6}
.today-head{font-weight:700;font-size:14px;margin:0 0 10px;color:#7a5600}
.today-head .cnt{font-weight:400;margin-left:6px;opacity:.75}
.today-empty{margin:0;font-size:13px;color:#7a5600}
.sticky.leaving{opacity:0;transform:scale(.96);transition:opacity .25s,transform .25s}
/* Floating overlay at the top of the screen, always visible whatever the scroll position. */
#donetoast{position:fixed;left:50%;top:18px;transform:translate(-50%,-20px);opacity:0;
  pointer-events:none;transition:opacity .2s,transform .2s;z-index:2147483000;display:flex;gap:6px;
  align-items:center;background:#22201d;color:#fff;border-radius:10px;padding:10px 12px 10px 16px;
  box-shadow:0 6px 24px rgba(0,0,0,.18);font-size:14px;max-width:min(92vw,640px);
  /* it lives outside <body>, so it must be given the page font itself */
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.4;-webkit-font-smoothing:antialiased}
#donetoast.show{opacity:1;transform:translate(-50%,0);pointer-events:auto}
#donetoast .tm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-right:6px}
#donetoast button{font:inherit;font-size:13px;font-weight:600;border:0;border-radius:6px;padding:5px 10px;
  background:rgba(255,255,255,.14);color:#fff;cursor:pointer}
#donetoast button:hover{background:rgba(255,255,255,.26)}
/* edit / delete / reorder controls */
.acts{margin-left:auto;display:inline-flex;gap:2px;opacity:.35;transition:opacity .15s}
.sticky:hover .acts,.step:hover .acts,.doneitem:hover .acts{opacity:1}
.act{border:none;background:none;cursor:pointer;font-size:13px;padding:2px 4px;color:var(--ink-2);
  border-radius:4px;line-height:1}
.act:hover{background:rgba(0,0,0,.07);color:var(--ink)}
.act[data-act=del]:hover{color:#b3261e}
.step .acts{display:flex;margin-top:6px}
.lane-head .pacts{margin-left:8px;vertical-align:middle;text-transform:none;letter-spacing:0}
.lane:hover .pacts{opacity:1}
.newproj{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px}
.newproj input,.newproj select{font:inherit;font-size:13px;padding:7px 10px;border:1px solid var(--border);
  border-radius:8px;background:var(--surface-1);color:var(--ink)}
.newproj input[name=name]{flex:1;min-width:180px}
.newproj input[name=done_when]{flex:2;min-width:220px}
.newproj button{font:inherit;font-size:13px;font-weight:600;padding:7px 14px;border:0;border-radius:8px;
  background:#22201d;color:#fff;cursor:pointer}
.donebox{margin:18px 0 8px;border:1px solid var(--border);border-radius:10px;padding:8px 12px;
  background:var(--surface-1)}
.donebox summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--ink-2)}
.donelist{max-height:340px;overflow:auto;margin-top:8px}
.doneitem{position:relative;display:flex;align-items:center;gap:10px;padding:6px 0;
  border-top:1px solid var(--rule);font-size:13px}
.doneitem .chk{position:static;line-height:0}
.doneitem .dt{color:var(--muted);text-decoration:line-through;flex:1}
.doneitem .da{font-size:11px;color:var(--muted)}
.addstep{margin:10px 0 0;flex-basis:100%;width:100%}
.addstep input{width:100%;box-sizing:border-box;font:inherit;font-size:13px;padding:8px 10px;
  border:1px dashed var(--border);border-radius:8px;background:transparent;color:var(--ink)}
.addstep input:focus{outline:none;border-style:solid;border-color:var(--b450)}
"""

CONTROLS_CSS = """
.tabs{display:flex;gap:2px;border-bottom:1px solid var(--rule);margin:22px 0 14px}
.tabs button{font:inherit;font-size:13px;font-weight:600;padding:8px 14px;cursor:pointer;
  background:none;border:none;border-bottom:2px solid transparent;color:var(--muted);
  margin-bottom:-1px}
.tabs button:hover{color:var(--ink-2)}
.tabs button[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--b450)}
.tabs .cnt{font-weight:400;opacity:.6;margin-left:5px;font-variant-numeric:tabular-nums}
.panel[hidden]{display:none}
.kpi[data-tile]{cursor:pointer;text-align:left;font:inherit;color:inherit;width:100%}
.kpi[data-tile]:hover{border-color:var(--rule)}
.kpi[aria-pressed="true"]{border-color:var(--b450);box-shadow:inset 0 0 0 1px var(--b450),var(--shadow)}
.ctl{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 12px}
.ctl button{font:inherit;font-size:12px;padding:4px 10px;border-radius:99px;cursor:pointer;
  border:1px solid var(--border);background:var(--surface-1);color:var(--ink-2)}
.ctl button[aria-pressed="true"]{background:var(--b450);color:#fff;border-color:var(--b450)}
.ctl .sep{width:1px;height:18px;background:var(--rule);margin:0 4px}
.ctl .lab{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
"""

JS = """
const $=s=>document.querySelectorAll(s);
let state={tile:'open',area:'all',sort:'mine',view:'grid'};
function tileOK(r){
  const d=r.dataset.due===''?null:+r.dataset.due, k=r.dataset.kind;
  if(k!=='task') return false;
  if(state.tile==='overdue') return d!==null&&d<0;
  if(state.tile==='week') return d!==null&&d>=0&&d<=7;
  return true;
}
const cmp=(a,b)=>state.sort==='mine' ? (+a.dataset.pos)-(+b.dataset.pos)
  : state.sort==='urgency'
  ? (+a.dataset.rank)-(+b.dataset.rank)||(+b.dataset.est)-(+a.dataset.est)
  : state.sort==='est' ? (+b.dataset.est)-(+a.dataset.est)
  : a.dataset.title.localeCompare(b.dataset.title);
function apply(){
  let shown=0;
  const grid=document.getElementById('taskgrid');
  if(grid) grid.querySelectorAll('.swim').forEach(lane=>{
    const laneOK=(state.area==='all'||lane.dataset.area===state.area);
    const rows=[...lane.querySelectorAll('[data-row]')];
    rows.forEach(r=>{r.style.display=(laneOK&&tileOK(r))?'':'none';});
    const vis=rows.filter(r=>r.style.display!=='none');
    shown+=vis.length;
    const g=lane.querySelector('.swim-grid');
    vis.sort(cmp); vis.forEach(r=>g.appendChild(r));
    const add=lane.querySelector('.addnew');
    if(add){ g.appendChild(add); add.style.display=laneOK?'':'none'; }
    lane.hidden=(vis.length===0)&&!(add&&laneOK);
    const c=lane.querySelector('.cnt'); if(c) c.textContent=vis.length;
  });
  const list=document.getElementById('tasklist');
  if(list){
    const rows=[...list.querySelectorAll('[data-row]')];
    rows.forEach(r=>{
      r.style.display=(tileOK(r)&&(state.area==='all'||r.dataset.area===state.area))
        ?'':'none';});
    const vis=rows.filter(r=>r.style.display!=='none');
    const tb=list.querySelector('tbody');
    vis.sort(cmp); vis.forEach(r=>tb.appendChild(r));
  }
  document.getElementById('taskgrid').hidden=(state.view!=='grid');
  document.getElementById('tasklist').hidden=(state.view!=='list');
  const names={open:'open tasks',overdue:'overdue',week:'due in 7 days'};
  document.getElementById('shown').textContent=shown+' shown — '+names[state.tile];
}
function showTab(k){
  ['tasks','goals','projects','held'].forEach(t=>{
    document.getElementById('panel-'+t).hidden=(t!==k);});
  $('[data-tab]').forEach(b=>b.setAttribute('aria-selected',b.dataset.tab===k));
  try{sessionStorage.setItem('tab',k);}catch(e){}
}
// Ticking a box reloads the page; that must not jump back to the Tasks tab. Remember the tab and scroll position across reloads.
window.addEventListener('beforeunload',()=>{
  try{sessionStorage.setItem('scrollY',String(window.scrollY));}catch(e){}});
document.addEventListener('DOMContentLoaded',()=>{
  $('[data-tab]').forEach(b=>b.onclick=()=>showTab(b.dataset.tab));
  try{
    const k=sessionStorage.getItem('tab');
    if(k&&document.getElementById('panel-'+k)) showTab(k);
    const y=sessionStorage.getItem('scrollY');
    if(y) requestAnimationFrame(()=>window.scrollTo(0,+y));
  }catch(e){}
  $('[data-tile]').forEach(b=>b.onclick=()=>{
    state.tile=b.dataset.tile; state.area='all'; showTab('tasks');
    $('[data-tile]').forEach(x=>x.setAttribute('aria-pressed',x===b));
    $('[data-filter]').forEach(x=>x.setAttribute('aria-pressed',x.dataset.filter==='all'));
    apply();});
  $('[data-filter]').forEach(b=>b.onclick=()=>{
    state.area=b.dataset.filter;
    $('[data-filter]').forEach(x=>x.setAttribute('aria-pressed',x===b));
    apply();});
  $('[data-sort]').forEach(b=>b.onclick=()=>{
    state.sort=b.dataset.sort;
    $('[data-sort]').forEach(x=>x.setAttribute('aria-pressed',x===b));
    apply();});
  $('[data-view]').forEach(b=>b.onclick=()=>{
    state.view=b.dataset.view;
    $('[data-view]').forEach(x=>x.setAttribute('aria-pressed',x===b));
    apply();});
  apply();
});
"""

RANK = {"critical": 0, "warning": 1, "serious": 2, "none": 3, "done": 4}

# Categorical slots 1, 2, 3 and 7 of the validated palette. Slot 4 (yellow) was
# rejected: against slot 2 (orange) it measures normal-vision dE 13.7, under the
# hard floor of 15. This set clears all pairs - worst normal-vision 16.3, worst
# CVD 9.2. The sticky fill is a pale tint of the same hue and carries no meaning
# on its own: the saturated top edge plus the written area name do that, so the
# encoding is never color-alone and dark ink always sits on a near-white fill.
PALETTE = ["#4a3aa7", "#2a78d6", "#eb6834", "#1baf7a", "#c2453e", "#8a5d00"]
AREA_NEUTRAL = "#898781"


def load_areas():
    """Areas are the `## ` headings in tasks.md, in the order they appear there.
    Projects and Goals always come last; they are fed from projects/*.md."""
    path = os.path.join(ROOT, "tasks.md")
    heads = []
    if os.path.exists(path):
        for line in io.open(path, encoding="utf-8"):
            if line.startswith("## ") and line[3:].strip() not in heads:
                heads.append(line[3:].strip())
    return heads + ["Projects", "Goals"]


AREA_ORDER = load_areas()
AREA_COLOR = {a: PALETTE[i % len(PALETTE)] for i, a in enumerate(AREA_ORDER[:-2])}






def area_sort(areas):
    known = [a for a in AREA_ORDER if a in areas]
    return known + sorted(a for a in areas if a not in AREA_ORDER)


def esc(s):
    return html.escape(str(s if s is not None else ""))


def md(s):
    """Escape, then honour inline `code` spans from the markdown source."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(s))


def tint(hex_color, amount=0.90):
    """Mix a hue toward the chart surface to get a sticky-note fill."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    sr, sg, sb = 0xFC, 0xFC, 0xFB
    mix = lambda c, s: round(c + (s - c) * amount)
    return "#%02x%02x%02x" % (mix(r, sr), mix(g, sg), mix(b, sb))


def checkbox(item, server):
    """A real checkbox, only in server mode - a file:// page cannot write to disk."""
    if not server:
        return ""
    ck = " checked" if item.get("done") else ""
    return (f"<label class='chk'><input type='checkbox'{ck} "
            f"data-file='{esc(item.get('src'))}' data-raw='{esc(item.get('raw'))}'>"
            "</label>")




def render(tasks, projects, interactive, server=False):
    open_tasks = ([t for t in tasks if not t["done"]] + surfaced_steps(projects)
                  + project_next_steps(projects))
    rested = [t for t in open_tasks if not due_this_cycle(t)]
    open_tasks = [t for t in open_tasks if due_this_cycle(t)]
    all_steps = [(p, s) for p in projects for s in p["steps"]]
    open_steps = [(p, s) for p, s in all_steps if not s["done"]]
    overdue = [t for t in open_tasks if t["due"] and t["due"] < TODAY]
    week = [t for t in open_tasks if t["due"] and 0 <= (t["due"] - TODAY).days <= 7]
    active = [p for p in projects if p["status"] == "active"]
    stalled = sorted((p for p in active if not is_complete(p)),
                     key=lambda p: p["touched"])

    A = []
    w = A.append
    w("<!doctype html><html lang='en' data-theme='light'><head><meta charset='utf-8'>")
    w("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    w(f"<title>Tasks — {TODAY:%-d %b %Y}</title>")
    w("<style>" + CSS + (CONTROLS_CSS if interactive else "") + "</style></head><body>")
    w("<div class='viz-root'>")
    w("<h1>Tasks</h1>")
    kind = ("laptop · live — tick a box and it writes to the markdown" if server
            else "laptop" if interactive else "phone · read-only")
    w(f"<p class='sub'>{TODAY:%A %-d %B %Y} · {kind} · generated, do not edit by hand</p>")

    # ---- KPI tiles
    total_min = sum(t["est"] or 30 for t in open_tasks)
    w("<div class='kpis'>")
    for key, v, l in [("open", len(open_tasks), "Open tasks"),
                      ("overdue", len(overdue), "Overdue"),
                      ("week", len(week), "Due in 7 days")]:
        if interactive:
            press = "true" if key == "open" else "false"
            w(f"<button class='kpi' data-tile='{key}' aria-pressed='{press}'>"
              f"<div class='v'>{v}</div><div class='l'>{l}</div></button>")
        else:
            w(f"<div class='kpi'><div class='v'>{v}</div><div class='l'>{l}</div></div>")
    w("</div>")
    w(f"<p class='note'>Roughly <b>{total_min//60}h{total_min%60:02d}</b> of estimated work "
      f"in open tasks, plus {len(open_steps)} project steps across {len(active)} "
      "active projects."
      + (f" <b>{len(rested)}</b> recurring "
         + ("task is" if len(rested) == 1 else "tasks are")
         + " already done this cycle and hidden until next due." if rested else "")
      + "</p>")

    if interactive:
        w("<div class='tabs' role='tablist'>")
        n_goals = len([p for p in projects
                       if p.get("kind") == "goal" and p["status"] == "active"])
        n_work = len([p for p in projects
                      if p.get("kind") != "goal" and p["status"] == "active"])
        for key, lab, n in [("tasks", "Tasks", len(open_tasks)),
                            ("goals", "Goals", n_goals),
                            ("projects", "Projects", n_work),
                            ("held", "Held up",
                             len([p for p in stalled
                                  if (TODAY - p["touched"]).days > 0]))]:
            sel = "true" if key == "tasks" else "false"
            w(f"<button role='tab' data-tab='{key}' aria-selected='{sel}'>{lab}"
              f"<span class='cnt'>{n}</span></button>")
        w("</div>")

    # ================= TASKS =================
    if interactive:
        w("<div class='panel' id='panel-tasks'>")
        w("<div class='ctl'><span class='lab'>Area</span>")
        w("<button data-filter='all' aria-pressed='true'>All</button>")
        for a in area_sort({t["area"] for t in open_tasks}):
            w(f"<button data-filter='{esc(a)}' aria-pressed='false'>{esc(a)}</button>")
        if server:
            w("<button type='button' class='addarea' title='Add an area (a new lane)'>+ Area</button>")
        w("<span class='sep'></span><span class='lab'>Sort</span>")
        w("<button data-sort='mine' aria-pressed='true'>My order</button>")
        w("<button data-sort='urgency' aria-pressed='false'>Urgency</button>")
        w("<button data-sort='est' aria-pressed='false'>Longest</button>")
        w("<button data-sort='title' aria-pressed='false'>A–Z</button>")
        w("<span class='sep'></span><span class='lab'>View</span>")
        w("<button data-view='grid' aria-pressed='true'>Board</button>")
        w("<button data-view='list' aria-pressed='false'>List</button>")
        w("</div><p class='note' id='shown'></p>")
    else:
        w("<h2>Tasks</h2>")

    def attrs(title, area, t, kind_):
        st = urgency(t)[0]
        days = (t["due"] - TODAY).days if t.get("due") else ""
        # items surfaced from project files have no tasks.md line; they sort last
        pos = t.get("pos", 10 ** 6)
        return (f"data-row data-kind='{kind_}' data-area='{esc(area)}' "
                f"data-rank='{RANK[st]}' data-due='{days}' data-pos='{pos}' "
                f"data-est='{t.get('est') or 0}' data-title='{esc(title)}'")

    def acts(t, edit=True, move=False):
        """Edit / delete (and up / down for project steps) - server mode only, and only
        for lines that live in tasks.md or a project file."""
        src = t.get("src") or ""
        if not server or not (src == "tasks.md" or src.startswith("projects/")):
            return ""
        d = f"data-file='{esc(src)}' data-raw='{esc(t.get('raw'))}'"
        b = []
        if move:
            b += [f"<button class='act' data-act='up' {d} title='Move up'>↑</button>",
                  f"<button class='act' data-act='down' {d} title='Move down'>↓</button>"]
        if edit:
            b.append(f"<button class='act' data-act='edit' {d} title='Edit text, time or due date'>✎</button>")
        b.append(f"<button class='act' data-act='del' {d} title='Delete'>✕</button>")
        return "<span class='acts'>" + "".join(b) + "</span>"

    def sticky(title, area, t, kind_):
        st, lab = urgency(t)
        icon = {"critical": "▲", "warning": "●", "serious": "●", "done": "✓"}.get(st, "")
        hue = AREA_COLOR.get(area, AREA_NEUTRAL)
        pill = (f"<span class='pill {st}'>{icon} {esc(lab)}</span>" if st != "none"
                else (f"<span class='st-est'>{esc(lab)}</span>" if lab else ""))
        est = f"<span class='st-est'>{fmt_est(t.get('est'))}</span>" if t.get("est") else ""
        if t.get("last"):
            d = (TODAY - t["last"]).days
            when = "today" if d == 0 else "yesterday" if d == 1 else f"{d}d ago"
            est += f"<span class='st-est'>· last done {when}</span>"
        # Only tasks.md lines can be dragged - a project step lives in its
        # own file and its place is set there.
        drag = (f" draggable='true' data-raw='{esc(t.get('raw'))}'"
                if server and t.get("src") == "tasks.md" else "")
        on = t.get("star") == TODAY
        if server and t.get("src"):
            star = (f"<button class='star{' on' if on else ''}' aria-pressed='{str(on).lower()}' "
                    f"title='{'Unstar' if on else 'Star for today'}' "
                    f"data-file='{esc(t.get('src'))}' data-raw='{esc(t.get('raw'))}'>"
                    f"{'★' if on else '☆'}</button>")
        else:
            star = "<span class='star on'>★</span>" if on else ""
        return (f"<div class='sticky{' starred' if on else ''}' {attrs(title, area, t, kind_)}{drag} "
                f"style='--tint:{tint(hue)};--hue:{hue}'>"
                + star + checkbox(t, server) +
                f"<div class='st-title'>{md(title)}</div>"
                f"<div class='st-foot'>{pill}{est}{acts(t)}</div>"
                f"<div class='st-area'><span class='dot' style='background:{hue}'></span>"
                f"{esc(area)}</div></div>")

    rows = [(t["title"], t["area"], t, "task") for t in open_tasks]
    # The board follows tasks.md line order, which you set by
    # dragging. Urgency is still a chip on the laptop board.
    rows.sort(key=lambda r: r[2].get("pos", 10 ** 6))

    # Areas a task can be typed into. Projects and Goals are fed from project files.
    addable = [a for a in AREA_ORDER if a not in ("Goals", "Projects")]
    lanes = {r[1] for r in rows} | (set(addable) if server else set())

    # Today strip: copies of the starred stickies, without data-row so the filters
    # and counts below still see each task once.
    # The starred row is always there, even before anything is starred.
    starred = [r for r in rows if r[2].get("star") == TODAY]
    mins = sum(r[2].get("est") or 30 for r in starred)
    cnt = f"{len(starred)} · {fmt_est(mins)}" if starred else "0"
    w("<div class='today'><div class='today-head'>★ Starred for today"
      f"<span class='cnt'>{cnt}</span></div>")
    if starred:
        w("<div class='swim-grid'>")
        for r in starred:
            w(sticky(*r).replace("data-row ", "", 1))
        w("</div>")
    else:
        w("<p class='today-empty'>Nothing starred yet. Click the ☆ on any task to pin it here "
          "for today. Stars clear themselves tomorrow.</p>")
    w("</div>")

    w("<div class='board' id='taskgrid'>")
    for area in area_sort(lanes):
        lane = [r for r in rows if r[1] == area]
        hue = AREA_COLOR.get(area, AREA_NEUTRAL)
        w(f"<div class='swim' data-area='{esc(area)}' style='--hue:{hue}'>")
        w(f"<div class='swim-head'><span class='dot' style='background:{hue}'></span>"
          f"{esc(area)}<span class='cnt'>{len(lane)}</span></div>")
        w("<div class='swim-grid'>")
        for r in lane:
            w(sticky(*r))
        if server and area in addable:
            # An empty box in each section to click and type
            # into; Enter saves it and a fresh blank box takes its place.
            w(f"<div class='sticky addnew' style='--tint:{tint(hue)};--hue:{hue}'>"
              f"<textarea rows='3' data-addarea='{esc(area)}' "
              f"placeholder='+ Add a task…'></textarea>"
              f"<div class='st-foot'><span class='st-est'>Enter to save · "
              f"end with 1h to set time (default 30m)</span></div></div>")
        w("</div></div>")
    w("</div>")

    if interactive:
        w("<div id='tasklist' hidden><div class='card'><table><thead><tr>"
          "<th>Task</th><th>Area</th><th>When</th><th>Est</th></tr></thead><tbody>")
        for title, area, t, kind_ in rows:
            st, lab = urgency(t)
            icon = {"critical": "▲", "warning": "●", "serious": "●",
                    "done": "✓"}.get(st, "")
            pill = (f"<span class='pill {st}'>{icon} {esc(lab)}</span>" if st != "none"
                    else f"<span class='est'>{esc(lab)}</span>")
            hue = AREA_COLOR.get(area, AREA_NEUTRAL)
            w(f"<tr {attrs(title, area, t, kind_)}><td>{md(title)}</td>"
              f"<td class='est areacell'><span class='dot' style='background:{hue}'></span>"
              f"{esc(area)}</td><td>{pill}</td>"
              f"<td class='est'>{fmt_est(t.get('est'))}</td></tr>")
        w("</tbody></table></div></div>")

    # Done tasks, so a mis-tick can be undone from the board .
    # Newest work first: tasks.md order is your working order, so show it reversed.
    if server:
        done_tasks = [t for t in tasks if t["done"]][::-1]
        w(f"<details class='donebox'><summary>Done ({len(done_tasks)}) — untick to reopen, "
          "✕ to delete</summary><div class='donelist'>")
        for t in done_tasks:
            w("<div class='doneitem'>" + checkbox(t, server) +
              f"<span class='dt'>{md(t['title'])}</span><span class='da'>{esc(t['area'])}</span>"
              + acts(t, edit=False) + "</div>")
        w("</div></details>")

    w("<p class='note'>Color marks the area and is always written out beside it — "
      "status uses an icon and a word too, never color alone. ▲ overdue or today · "
      "● due soon.</p>")
    if interactive:
        w("</div>")

    # ================= LANE BOARDS =================
    def lane_board(items, panel, heading, wrap=True):
        if wrap and interactive:
            w(f"<div class='panel' id='panel-{panel}' hidden>")
        elif not interactive or not wrap:
            w(f"<h2>{heading}</h2>")
        if server and panel in ("projects", "goals"):
            area_opts = "".join(f"<option>{a}</option>" for a in AREA_ORDER
                                if a not in ("Goals", "Projects"))
            noun = "goal" if panel == "goals" else "project"
            w(f"<form class='newproj' autocomplete='off' data-kind='{noun}'>"
              f"<input name='name' placeholder='New {noun} name' required>"
              "<input name='done_when' placeholder='Done when… (optional)'>"
              f"<select name='area'>{area_opts}</select>"
              f"<button type='submit'>+ New {noun}</button></form>")
        if not items:
            w("<div class='card'><p class='dw' style='margin:0'>Nothing here yet.</p></div>")
        for p in items:
            done_n = sum(1 for s in p["steps"] if s["done"])
            tot = len(p["steps"]) or 1
            pct = round(100 * done_n / tot)
            tag = ("" if p["status"] == "active"
                   else f" <span class='pill warning'>⚑ {esc(p['status'])}</span>")
            if is_complete(p):
                tag += " <span class='pill done'>✓ complete</span>"
            w("<div class='card lane'>")
            pacts = ""
            if server:
                pf = esc(p["file"])
                pacts = (f"<span class='acts pacts'>"
                         f"<button class='act' data-pact='rename' data-file='{pf}' data-name='{esc(p['name'])}' title='Rename'>✎</button>"
                         f"<button class='act' data-pact='archive' data-file='{pf}' data-name='{esc(p['name'])}' title='Archive (moves the file to archive/projects/)'>🗄</button>"
                         "</span>")
            w(f"<div class='lane-head'><span class='nm'>{esc(p['name'])}{tag}{pacts}</span>"
              f"<span class='ct'>{done_n}/{tot} · {pct}%</span></div>")
            w(f"<div class='meter'><span style='width:{pct}%'></span></div>")
            if p["done_when"]:
                w(f"<p class='dw'>{md(p['done_when'])}</p>")
            # {ongoing} steps stay in the numbered line - they just carry a tag, and
            # the flag skips past them, so an item you chip away at never reads as
            # the thing blocking the project. You can leave it and tick the next one.
            steps = p["steps"]
            first_open = next((i for i, x in enumerate(steps)
                               if not x["done"] and not x.get("ongoing")), None)

            w("<div class='flow'>")
            for i, stp in enumerate(steps):
                ong = stp.get("ongoing")
                cls = "step done" if stp["done"] else (
                    "step now" if i == first_open else "step")
                if ong:
                    cls += " ong"
                num = "✓" if stp["done"] else str(i + 1)
                extra = ""
                if ong:
                    if stp.get("prog"):
                        a, b = stp["prog"]
                        pc = round(100 * a / b) if b else 0
                        extra = (f"<span class='prog'>ongoing · {a} of {b}</span>"
                                 f"<span class='pbar'><span style='width:{pc}%'></span></span>")
                    else:
                        extra = "<span class='tag'>ongoing</span>"
                elif i == first_open:
                    extra = ("<span class='flag'>⚑ STUCK HERE</span>" if done_n
                             else "<span class='flag'>⚑ START HERE</span>")
                w(f"<div class='{cls}'>" + checkbox(stp, server) +
                  f"<span class='n'>{num}</span>"
                  f"<span class='t'>{md(stp['title'])}</span>{extra}"
                  + acts(stp, move=True) + "</div>")
                if i < len(steps) - 1:
                    w("<div class='conn'></div>")
            if server:
                # Add steps to a project from the board, like the
                # blank box in each task lane. Enter saves under the file's ## Steps.
                w(f"<div class='addstep'><input type='text' data-addstep='{esc(p['file'])}' "
                  "placeholder='+ Add a step… (Enter to save, end with 1h to set time)'></div>")
            w("</div></div>")
    

        if wrap and interactive:
            w("</div>")

    # goals sort by their `order:` number, not alphabetically
    goals = sorted((p for p in projects if p.get("kind") == "goal"),
                   key=lambda p: (is_complete(p), p.get("order", 99), p["name"]))
    works = sorted((p for p in projects if p.get("kind") != "goal"),
                   key=lambda p: (is_complete(p), p["name"]))
    lane_board(goals, "goals", "Semester goals")
    lane_board(works, "projects", "Projects")

    # ================= HELD UP =================
    if interactive:
        w("<div class='panel' id='panel-held' hidden>")
    else:
        w("<h2>Where you're held up</h2>")
    w("<div class='card'>")
    ages = [(TODAY - p["touched"]).days for p in stalled]
    if not any(ages):
        w("<p class='dw' style='margin:0'><b>Nothing is stalled.</b> Every active project "
          "was touched today. This ranking fills in as files sit untouched — come back in "
          "a week and it will order them.</p>")
    else:
        w("<div class='bars'>")
        mx = max(ages + [1])
        for p in stalled:
            d = (TODAY - p["touched"]).days
            pct = max(4, round(100 * d / mx))
            shade = ("var(--b650)" if d >= 21 else "var(--b550)" if d >= 14
                     else "var(--b450)" if d >= 7 else "var(--b350)" if d >= 3
                     else "var(--b250)")
            label = "today" if d == 0 else f"{d}d ago"
            w(f"<div class='bar'><span>{esc(p['name'])}</span>"
              f"<span class='t'><span style='width:{pct}%;background:{shade}'></span></span>"
              f"<span class='n'>{label}</span></div>")
        w("</div><p class='note'>Days since the project file last changed. Darker means "
          "longer. Nothing here needs updating by you — it comes from the file's own "
          "timestamp.</p>")
    w("</div>")
    if interactive:
        w("</div>")
        w("<script>" + JS + "</script>")
    if server:
        # Adding a task is only offered in server mode, for the same reason the
        # checkboxes are: a file:// page cannot write to tasks.md.
        opts = "".join("<option>%s</option>" % a for a in AREA_ORDER
                       if a not in ("Goals", "Projects"))
        w("""<style>
#addbar{position:fixed;left:0;right:0;bottom:0;background:#fff;
 border-top:1px solid #e0ddd9;padding:10px 14px;display:flex;gap:8px;
 align-items:center;z-index:50;box-shadow:0 -2px 12px rgba(0,0,0,.05)}
#addbar input[type=text]{flex:1;min-width:120px;padding:8px 11px;font:inherit;
 font-size:14px;border:1px solid #ddd9d4;border-radius:8px}
#addbar input[name=est]{width:74px;flex:none}
#addbar input[name=due]{flex:none;padding:7px 9px;font:inherit;font-size:14px;
 border:1px solid #ddd9d4;border-radius:8px}
#addbar select{padding:8px 9px;font:inherit;font-size:14px;
 border:1px solid #ddd9d4;border-radius:8px;background:#fff}
#addbar button{padding:8px 15px;font:inherit;font-size:14px;font-weight:600;
 border:0;border-radius:8px;background:#22201d;color:#fff;cursor:pointer}
body{padding-bottom:62px}
</style>
<form id="addbar" autocomplete="off">
  <select name="area">%s</select>
  <input type="text" name="text" placeholder="Add a task…" required>
  <input type="text" name="est" value="30m" title="estimate — 30m, 1h, 1h30">
  <input type="date" name="due" title="due date (optional)">
  <select name="repeat" title="repeat weekly (optional)">
    <option value="">No repeat</option><option value="Mon">Every Mon</option>
    <option value="Tue">Every Tue</option><option value="Wed">Every Wed</option>
    <option value="Thu">Every Thu</option><option value="Fri">Every Fri</option>
    <option value="Sat">Every Sat</option><option value="Sun">Every Sun</option>
  </select>
  <button type="submit">Add</button>
</form>
<script>
document.getElementById('addbar').addEventListener('submit',function(e){
  e.preventDefault();
  var f=e.target, b=f.querySelector('button');
  b.disabled=true;
  fetch('/add',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({area:f.area.value,text:f.text.value,est:f.est.value,due:f.due.value||null,repeat:f.repeat.value||null})})
   .then(function(r){ if(r.ok){location.reload();} else {
     r.text().then(function(t){alert('Could not add: '+t);});
     b.disabled=false; } })
   .catch(function(){alert('Server not reachable.');b.disabled=false;});
});
</script>""" % opts)
        w("""<script>
// Inline add: type in a lane's blank box, Enter saves (Shift+Enter is ignored -
// a task is one line). A trailing "1h" / "45m" / "1h30" becomes the estimate.
// The page reloads so the new task gets its real checkbox, then focus returns
// to the same lane's blank box so the next one can be typed straight away.
document.addEventListener('keydown',function(e){
  var t=e.target;
  if(!t.dataset||!t.dataset.addarea) return;
  if(e.key==='Escape'){t.value='';t.blur();return;}
  if(e.key!=='Enter') return;
  e.preventDefault();
  var text=t.value.replace(/\\s+/g,' ').trim(), est='30m';
  if(!text) return;
  var m=text.match(/^(.*\\S)\\s+\\{?(\\d+(?:h\\d*|m|h\\d*m))\\}?$/);
  if(m){text=m[1];est=m[2];}
  t.disabled=true;
  fetch('/add',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({area:t.dataset.addarea,text:text,est:est})})
   .then(function(r){ if(r.ok){
       sessionStorage.setItem('refocus',t.dataset.addarea); location.reload();
     } else { r.text().then(function(x){alert('Could not add: '+x);}); t.disabled=false; } })
   .catch(function(){alert('Server not reachable.');t.disabled=false;});
});
document.addEventListener('click',function(e){
  var b=e.target.closest&&e.target.closest('.addnew');
  if(b) b.querySelector('textarea').focus();
});
// Drag a task to reorder it, or into another lane to change its area. The drop
// posts the task and the task now after it; the server moves the tasks.md line.
var dragEl=null, dropped=false;
document.addEventListener('dragstart',function(e){
  var s=e.target.closest&&e.target.closest('.sticky[draggable=true]');
  if(!s) return;
  dragEl=s; dropped=false; s.classList.add('dragging');
  e.dataTransfer.effectAllowed='move'; e.dataTransfer.setData('text/plain',s.dataset.raw);
});
function dropLane(e){
  var l=e.target.closest&&e.target.closest('.swim');
  return (l&&l.querySelector('textarea[data-addarea]'))?l:null;
}
document.addEventListener('dragover',function(e){
  if(!dragEl) return;
  var lane=dropLane(e); if(!lane) return;
  e.preventDefault();
  document.querySelectorAll('.swim.dropok').forEach(function(x){if(x!==lane)x.classList.remove('dropok');});
  lane.classList.add('dropok');
  var g=lane.querySelector('.swim-grid'), add=g.querySelector('.addnew');
  var t=e.target.closest('.sticky');
  if(t&&t!==dragEl&&!t.classList.contains('addnew')){
    var r=t.getBoundingClientRect();
    g.insertBefore(dragEl,(e.clientX<r.left+r.width/2)?t:t.nextSibling);
  } else if(!t||t.classList.contains('addnew')){
    g.insertBefore(dragEl,add);
  }
});
document.addEventListener('drop',function(e){
  if(!dragEl) return;
  var lane=dropLane(e); if(!lane) return;
  e.preventDefault(); dropped=true;
  var nx=dragEl.nextElementSibling;
  while(nx&&!nx.dataset.raw&&!nx.classList.contains('addnew')) nx=nx.nextElementSibling;
  var before=(nx&&nx.dataset.raw)?nx.dataset.raw:null;
  fetch('/move',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({raw:dragEl.dataset.raw,area:lane.dataset.area,before:before})})
   .then(function(r){ if(r.ok){location.reload();} else {
     r.text().then(function(x){alert('Could not move: '+x);location.reload();}); } })
   .catch(function(){alert('Server not reachable.');location.reload();});
});
document.addEventListener('dragend',function(){
  if(!dragEl) return;
  dragEl.classList.remove('dragging');
  document.querySelectorAll('.swim.dropok').forEach(function(x){x.classList.remove('dropok');});
  if(!dropped) location.reload();   // dropped outside a lane: put everything back
  dragEl=null;
});
// Edit / delete / reorder / new project (everything works from the board).
function post(url,body){
  return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
   .then(function(r){ if(r.ok){location.reload();}
     else {r.text().then(function(x){alert('Could not save: '+x);});} })
   .catch(function(){alert('Server not reachable.');});
}
document.addEventListener('click',function(e){
  var b=e.target.closest&&e.target.closest('button[data-pact]'); if(!b) return;
  e.preventDefault(); e.stopPropagation();
  if(b.dataset.pact==='rename'){
    var n=prompt('Rename "'+b.dataset.name+'" to:',b.dataset.name);
    if(n===null||!n.trim()||n.trim()===b.dataset.name) return;
    post('/rename-project',{file:b.dataset.file,name:n});
  } else if(confirm('Archive "'+b.dataset.name+'"?\\n\\nIt leaves the board and its file moves to archive/projects/. Nothing is deleted.')){
    post('/archive-project',{file:b.dataset.file});
  }
},true);
document.addEventListener('click',function(e){
  var b=e.target.closest&&e.target.closest('button.act[data-act]'); if(!b) return;
  e.preventDefault(); e.stopPropagation();
  var d={file:b.dataset.file,raw:b.dataset.raw};
  if(b.dataset.act==='del'){
    var name=b.dataset.raw.replace(/\\s*\\{[^}]*\\}/g,'');
    if(!confirm('Delete "'+name+'"?\\n\\nA copy is kept in archive/deleted-tasks.md.')) return;
    post('/delete',d);
  } else if(b.dataset.act==='edit'){
    var t=prompt('Edit the task. Keep or change the tags:\\n{30m} or {1h} = time · {due 2026-11-15} = due date · {every Fri} = repeats weekly',b.dataset.raw);
    if(t===null||t.trim()===b.dataset.raw.trim()) return;
    d.text=t; post('/edit',d);
  } else if(b.dataset.act==='up'||b.dataset.act==='down'){
    d.dir=b.dataset.act==='up'?-1:1; post('/move-step',d);
  }
},true);
document.addEventListener('submit',function(e){
  var f=e.target; if(!f.classList||!f.classList.contains('newproj')) return;
  e.preventDefault();
  var el=f.elements;
  post('/new-project',{name:el['name'].value,done_when:el['done_when'].value,area:el['area'].value,
                       kind:f.dataset.kind});
});
document.addEventListener('click',function(e){
  var b=e.target.closest&&e.target.closest('button.addarea'); if(!b) return;
  var n=prompt('Name of the new area (a lane on the board):'); if(!n||!n.trim()) return;
  post('/add-area',{name:n});
});
// Add a project step: same rules as the lane boxes, posted to /add-step.
document.addEventListener('keydown',function(e){
  var t=e.target;
  if(!t.dataset||!t.dataset.addstep) return;
  if(e.key==='Escape'){t.value='';t.blur();return;}
  if(e.key!=='Enter') return;
  e.preventDefault();
  var text=t.value.replace(/\\s+/g,' ').trim(), est='30m';
  if(!text) return;
  var m=text.match(/^(.*\\S)\\s+\\{?(\\d+(?:h\\d*|m|h\\d*m))\\}?$/);
  if(m){text=m[1];est=m[2];}
  t.disabled=true;
  fetch('/add-step',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({file:t.dataset.addstep,text:text,est:est})})
   .then(function(r){ if(r.ok){
       sessionStorage.setItem('refocusstep',t.dataset.addstep); location.reload();
     } else { r.text().then(function(x){alert('Could not add: '+x);}); t.disabled=false; } })
   .catch(function(){alert('Server not reachable.');t.disabled=false;});
});
(function(){
  var f=sessionStorage.getItem('refocusstep'); if(!f) return;
  sessionStorage.removeItem('refocusstep');
  setTimeout(function(){
    var t=document.querySelector('input[data-addstep="'+f+'"]');
    if(t){t.scrollIntoView({block:'center'});t.focus();}
  },50);
})();
(function(){
  var a=sessionStorage.getItem('refocus'); if(!a) return;
  sessionStorage.removeItem('refocus');
  var t=document.querySelector('textarea[data-addarea="'+a+'"]');
  if(t){t.scrollIntoView({block:'center'});t.focus();}
})();
</script>""")
        w("""<script>
document.addEventListener('click',function(e){
  var s=e.target.closest('button.star');
  if(!s) return;
  e.preventDefault(); e.stopPropagation(); s.disabled=true;
  fetch('/star',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({file:s.dataset.file,raw:s.dataset.raw})})
   .then(function(r){ if(r.ok){location.reload();} else {
     r.text().then(function(t){alert('Could not save: '+t);}); s.disabled=false; } })
   .catch(function(){alert('Server not reachable.');s.disabled=false;});
},true);
// A ticked task moves out of the way immediately; a popup offers Undo and Edit for 45 seconds.
var toastTimer=null;
function toast(title,file,raw){
  var t=document.getElementById('donetoast');
  // attached to <html>, not <body>, so no page-level scrolling setting can pull it off screen
  if(!t){t=document.createElement('div');t.id='donetoast';t.setAttribute('role','status');
         document.documentElement.appendChild(t);}
  t.innerHTML='';
  var m=document.createElement('span'); m.className='tm';
  m.textContent='Moved to Done: '+title; t.appendChild(m);
  [['Undo',function(){
      fetch('/toggle',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({file:file,raw:raw,done:false})}).then(function(){location.reload();});}],
   ['Edit',function(){
      var n=prompt('Edit the task. Keep or change the tags:\\n{30m} or {1h} = time · {due 2026-11-15} = due date · {every Fri} = repeats weekly',raw);
      if(n===null||n.trim()===raw.trim()) return;
      post('/edit',{file:file,raw:raw,text:n});}],
   ['✕',function(){t.classList.remove('show');}]].forEach(function(b){
    var x=document.createElement('button'); x.textContent=b[0]; x.onclick=b[1]; t.appendChild(x);});
  t.classList.add('show');
  clearTimeout(toastTimer); toastTimer=setTimeout(function(){t.classList.remove('show');},45000);
}
document.addEventListener('change',function(e){
  var c=e.target;
  if(c.type!=='checkbox'||!c.dataset.file) return;
  var file=c.dataset.file, raw=c.dataset.raw, done=c.checked;
  if(!done){   // unticking (Done list or a project step): save and reload as before
    c.disabled=true;
    fetch('/toggle',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file:file,raw:raw,done:false})})
     .then(function(r){ if(r.ok){location.reload();} else {
       r.text().then(function(t){alert('Could not save: '+t);}); c.checked=true; c.disabled=false; } })
     .catch(function(){alert('Server not reachable.');c.checked=true;c.disabled=false;});
    return;
  }
  // Ticking: move it out of the way first, then save in the background.
  var sel='input[type=checkbox][data-file="'+CSS.escape(file)+'"][data-raw="'+CSS.escape(raw)+'"]';
  var cards=[], steps=[];
  document.querySelectorAll(sel).forEach(function(x){
    x.checked=true;
    var s=x.closest('.sticky'); if(s){cards.push(s);return;}
    var p=x.closest('.step'); if(p) steps.push(p);
  });
  cards.forEach(function(s){s.classList.add('leaving');setTimeout(function(){s.style.display='none';},250);});
  steps.forEach(function(p){p.classList.remove('now');p.classList.add('done');});
  var title=raw.replace(/\\s*\\{[^}]*\\}/g,'');
  fetch('/toggle',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({file:file,raw:raw,done:true})})
   .then(function(r){ if(r.ok){ toast(title,file,raw); } else {
     r.text().then(function(t){alert('Could not save: '+t);});
     cards.forEach(function(s){s.style.display='';s.classList.remove('leaving');});
     steps.forEach(function(p){p.classList.remove('done');});
     document.querySelectorAll(sel).forEach(function(x){x.checked=false;}); } })
   .catch(function(){alert('Server not reachable.');
     cards.forEach(function(s){s.style.display='';s.classList.remove('leaving');});
     document.querySelectorAll(sel).forEach(function(x){x.checked=false;});});
});
</script>""")
    w("</div></body></html>")
    return "\n".join(A)




if __name__ == "__main__":
    main()



def main():
    """Write a static copy of the board next to the data (read-only, opens without the server)."""
    tasks, projects = read_tasks(), read_projects()
    io.open(os.path.join(ROOT, "dashboard.html"), "w", encoding="utf-8").write(render(tasks, projects, True))
    print(f"tasks: {len([t for t in tasks if not t['done']])} open · projects: {len(projects)}")


if __name__ == "__main__":
    main()
