# -*- coding: utf-8 -*-
"""Build task-template files, scenario files, indexes, CSV exports and the validation report."""
import csv
import json
import os
import re
import shutil

import templates as T
import scenarios_a
import scenarios_b

ROOT = r"C:\Users\93596\Desktop\任务场景文件夹"
DIR_TPL = os.path.join(ROOT, "01_task_templates")
DIR_CPL = os.path.join(DIR_TPL, "composite_patterns")
DIR_SCN = os.path.join(ROOT, "02_scenarios")
DIR_DOC = os.path.join(ROOT, "03_design_document")
DIR_TOOL = os.path.join(ROOT, "04_tools")

TEMPLATES = {t["id"]: t for t in T.TT}
COMPOSITES = {c["id"]: c for c in T.CP}
SCENARIOS = scenarios_a.SC + scenarios_b.SC

# nominal duration per template id, seconds
DUR = {"TT-01": 420, "TT-02": 300, "TT-03": 420, "TT-04": 300, "TT-05": 420, "TT-06": 480,
       "TT-07": 600, "TT-08": 420, "TT-09": 600, "TT-10": 480,
       "CP-A": 360, "CP-B": 480, "CP-C": 420, "CP-D": 240, "CP-E": 420, "CP-F": 540, "CP-G": 420, "CP-H": 420}

# category -> (window start fraction, window end fraction)
WINDOW = {"survey": (0.10, 0.55), "sampling": (0.12, 0.58), "search": (0.15, 0.70), "tracking": (0.20, 0.72),
          "security": (0.18, 0.75), "inspection": (0.20, 0.78), "logistics": (0.22, 0.80), "comms": (0.25, 0.85),
          "formation": (0.28, 0.70), "recovery": (0.55, 0.90), "composite": (0.35, 0.92)}

PRIORITY = {"recovery": "critical", "search": "high", "security": "high", "tracking": "high",
            "composite": "high", "inspection": "normal", "survey": "normal", "sampling": "normal",
            "comms": "normal", "formation": "normal", "logistics": "normal"}

# crew size for composite patterns (they combine two templates, so they never fly solo)
CP_AGENTS = {"CP-A": {"min": 2, "max": 3, "rec": 3}, "CP-B": {"min": 2, "max": 3, "rec": 2},
             "CP-C": {"min": 2, "max": 4, "rec": 3}, "CP-D": {"min": 2, "max": 3, "rec": 2},
             "CP-E": {"min": 1, "max": 2, "rec": 2}, "CP-F": {"min": 3, "max": 4, "rec": 3},
             "CP-G": {"min": 2, "max": 3, "rec": 2}, "CP-H": {"min": 2, "max": 4, "rec": 3}}


def scn_file_name(name):
    return name.replace(" - ", "-").replace(",", "").replace(" ", "-")


def fail(msg):
    raise SystemExit("BUILD FAILED: " + msg)


def derived_params(sid, p, scn):
    d = {}
    lz_ids = [z["id"] for z in scn["map"]["lz"]]
    if "lz" not in p:
        d["lz"] = p.get("rtb_zone") or lz_ids[0]
    if "min_sep_m" not in p:
        d["min_sep_m"] = 20
    if "emcon" in p:
        d["emcon_text"] = (" The corridor must be flown in complete radio silence: no transmissions between entry "
                           "and exit.") if p["emcon"] else ""
        d["emcon_target"] = "0 transmissions inside the corridor" if p["emcon"] else "link held, >= 1 status report per 60 s"
    return d


def fmt(text, params, sid, where):
    if not isinstance(text, str):
        return text
    try:
        return text.format(**params)
    except KeyError as exc:
        fail("%s %s: missing parameter %s" % (sid, where, exc))


def build_task(scn, idx, entry):
    tid = entry["t"]
    is_tpl = tid in TEMPLATES
    spec = TEMPLATES[tid] if is_tpl else COMPOSITES.get(tid)
    if spec is None:
        fail("%s: unknown template id %s" % (scn["id"], tid))
    p = dict(entry["p"])
    p.update(derived_params(scn["id"], p, scn))
    cat = spec["category"]
    w0, w1 = WINDOW[cat]
    span = scn["mission_window_s"]
    start = int(span * w0) + (idx % 4) * int(span * 0.03)
    end = min(span, start + DUR[tid])
    if end - start < 120:
        end = start + 120
    task = {
        "task_id": "%s-T%02d" % (scn["id"], idx + 1),
        "scenario_id": scn["id"],
        "template_id": tid,
        "template_name": spec["name"],
        "source": "template" if is_tpl else "composite",
        "title": fmt(spec["title"], p, scn["id"], "title"),
        "category": cat,
        "difficulty": spec["difficulty"],
        "priority": PRIORITY.get(cat, "normal"),
        "window": {"start_s": start, "end_s": end},
        "depends_on": [],
        "agents_required": (spec.get("agents") or CP_AGENTS.get(tid, {})).get("min", 1),
        "params": {k: v for k, v in sorted(p.items()) if not k.endswith("_text")},
        "brief": fmt(spec.get("brief", spec["objective"]), p, scn["id"], "brief"),
        "objective": fmt(spec["objective"], p, scn["id"], "objective"),
        "mission_phases": [{"phase": ph["phase"], "actions": [fmt(a, p, scn["id"], "phase") for a in ph["actions"]],
                            "output": fmt(ph.get("output", ""), p, scn["id"], "phase-out")} for ph in spec["phases"]],
        "constraints": [fmt(c, p, scn["id"], "constraint") for c in spec["constraints"]],
        "failure_conditions": [fmt(f, p, scn["id"], "failure") for f in spec["failures"]],
        "checkpoints": [dict(id=cp["id"], name=cp["name"], metric=fmt(cp["metric"], p, scn["id"], "cp-metric"),
                             target=fmt(cp["target"], p, scn["id"], "cp-target"),
                             verification=cp["verify"], weight=cp["weight"]) for cp in spec["checkpoints"]],
        "grading": "global checkpoint pass rate; each checkpoint is scored independently and weighted",
    }
    return task


def build_scenario(scn):
    zones = scn["zones"]
    zone_index = {z["id"]: z for z in zones}
    for z in scn["map"]["lz"]:
        zone_index[z["id"]] = dict(z, kind="landing_zone")
    for z in scn["map"]["nfz"]:
        zone_index[z["id"]] = dict(z, kind="no_fly_zone")
    tasks = []
    for idx, entry in enumerate(scn["plan"]):
        t = build_task(scn, idx, entry)
        for key, val in t["params"].items():
            if key in ("lz", "rtb_zone", "entry", "exit") and isinstance(val, str) and val in zone_index:
                continue
            if key in ("area", "poi", "poi_a", "poi_b", "drop_zone", "pickup", "grid_area", "track_area",
                       "search_area", "perimeter", "corridor", "critical_task", "lz", "alt_lz", "rtb_zone",
                       "site_a", "site_b") and isinstance(val, str):
                if val not in zone_index and not val.startswith("WP-"):
                    fail("%s: zone %s referenced but not defined" % (scn["id"], val))
        tasks.append(t)
    # semantics-preserving dependencies
    by_tpl = {}
    for i, t in enumerate(tasks):
        by_tpl.setdefault(t["template_id"], []).append(i)
    area_first = {}
    for i, t in enumerate(tasks):
        key = t["params"].get("area") or t["params"].get("search_area")
        area_first.setdefault(key, i)
    for i, t in enumerate(tasks):
        if t["template_id"] in ("TT-10", "CP-H"):
            for j, s in enumerate(tasks):
                if s["category"] in ("survey", "search") and s["window"]["start_s"] < t["window"]["start_s"]:
                    t["depends_on"] = [s["task_id"]]
                    break
        elif t["template_id"] == "CP-E":
            key = t["params"].get("area")
            j = area_first.get(key)
            if j is not None and j < i and tasks[j]["template_id"] == "TT-01":
                t["depends_on"] = [tasks[j]["task_id"]]
        elif t["template_id"] == "CP-A":
            for j, s in enumerate(tasks):
                if s["template_id"] == "TT-03":
                    t["depends_on"] = [s["task_id"]]
                    break
    n_tpl = sum(1 for t in tasks if t["source"] == "template")
    out = dict(
        schema_version="1.0",
        scenario_id=scn["id"], name=scn["name"], language="en", difficulty=scn["difficulty"],
        mission_theme=scn["theme"], mission_window_s=scn["mission_window_s"],
        map=scn["map"], zones=zones, fleet=scn["fleet"],
        environment=scn["environment"], comms=scn["comms"],
        task_generation=dict(
            seed=20261104, total_tasks=len(tasks), from_templates=n_tpl,
            composite=len(tasks) - n_tpl, template_ratio=round(n_tpl / len(tasks), 3),
            rule="tasks are instantiated from the 10 task templates with scenario-specific parameters; "
                 "composite tasks add contingency patterns that combine two templates",
        ),
        tasks=tasks,
    )
    return out


def template_file(tpl):
    return dict(
        schema_version="1.0", template_id=tpl["id"], name=tpl["name"], language="en",
        category=tpl["category"], difficulty=tpl["difficulty"], version="1.0",
        summary=tpl["summary"], objective=tpl["objective"], brief=tpl["brief"],
        agents=tpl.get("agents", {"min": 1, "max": 4, "rec": 2}),
        parameters=tpl["params"], preconditions=tpl["preconditions"],
        mission_phases=tpl["phases"], checkpoints=tpl["checkpoints"],
        failure_conditions=tpl["failures"], constraints=tpl["constraints"],
        generation_rules=tpl["gen"], instance_example=tpl["example"],
        grading="global checkpoint pass rate; checkpoint weights sum to 100",
    )


def composite_file(cp):
    return dict(
        schema_version="1.0", template_id=cp["id"], name=cp["name"], language="en", category=cp["category"],
        difficulty=cp["difficulty"], version="1.0", derived_from=cp["base"],
        summary=cp["summary"], objective=cp["objective"], brief=cp["brief"],
        agents=CP_AGENTS[cp["id"]],
        parameters=cp["params"], mission_phases=cp["phases"], checkpoints=cp["checkpoints"],
        failure_conditions=cp["failures"], constraints=cp["constraints"], generation_rules=cp["gen"],
        grading="global checkpoint pass rate; checkpoint weights sum to 100",
    )


def dump(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def main():
    for d in (DIR_TPL, DIR_CPL, DIR_SCN, DIR_DOC, DIR_TOOL):
        os.makedirs(d, exist_ok=True)
    for d in (DIR_TPL, DIR_CPL, DIR_SCN, DIR_TOOL):
        for name in os.listdir(d):
            if name.endswith((".json", ".csv", ".txt")):
                os.remove(os.path.join(d, name))

    problems = []
    tpl_files = []
    cp_files = []
    for i, tpl in enumerate(T.TT, 1):
        obj = template_file(tpl)
        if sum(c["weight"] for c in obj["checkpoints"]) != 100:
            problems.append("%s checkpoint weights != 100" % tpl["id"])
        name = "%s_%s.json" % (tpl["id"], tpl["name"].replace(" ", "_").replace("-", "_"))
        dump(os.path.join(DIR_TPL, name), obj)
        tpl_files.append((name, tpl))
    for cp in T.CP:
        obj = composite_file(cp)
        if sum(c["weight"] for c in obj["checkpoints"]) != 100:
            problems.append("%s checkpoint weights != 100" % cp["id"])
        name = "%s_%s.json" % (cp["id"], cp["name"].replace(" ", "_").replace("-", "_"))
        dump(os.path.join(DIR_CPL, name), obj)
        cp_files.append((name, cp))

    scn_objs = []
    for scn in SCENARIOS:
        obj = build_scenario(scn)
        if len(obj["tasks"]) < 20:
            problems.append("%s has %d tasks" % (scn["id"], len(obj["tasks"])))
        if obj["task_generation"]["template_ratio"] < 0.5:
            problems.append("%s template ratio %.2f" % (scn["id"], obj["task_generation"]["template_ratio"]))
        for t in obj["tasks"]:
            if sum(c["weight"] for c in t["checkpoints"]) != 100:
                problems.append("%s %s checkpoint weights != 100" % (scn["id"], t["task_id"]))
            blob = json.dumps({k: v for k, v in t.items() if k != "params"}, ensure_ascii=False)
            if re.search(r"\{[a-z_0-9]+\}", blob):
                problems.append("%s %s has an unresolved placeholder" % (scn["id"], t["task_id"]))
        name = "%s_%s.json" % (scn["id"], scn_file_name(scn["name"]))
        dump(os.path.join(DIR_SCN, name), obj)
        scn_objs.append(obj)

    # CSV exports
    rows = []
    for obj in scn_objs:
        for t in obj["tasks"]:
            rows.append([obj["scenario_id"], obj["name"], t["task_id"], t["source"], t["template_id"],
                         t["template_name"], t["title"], t["category"], t["difficulty"], t["priority"],
                         t["window"]["start_s"], t["window"]["end_s"], len(t["checkpoints"]),
                         ";".join(t["depends_on"])])
    with open(os.path.join(DIR_TOOL, "all_tasks.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario_id", "scenario_name", "task_id", "source", "template_id", "template_name", "title",
                    "category", "difficulty", "priority", "window_start_s", "window_end_s", "checkpoints",
                    "depends_on"])
        w.writerows(rows)

    with open(os.path.join(DIR_TOOL, "scenario_summary.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario_id", "name", "difficulty", "area_km2", "fleet", "tasks", "from_templates",
                    "composite", "template_ratio", "mission_window_s"])
        for obj in scn_objs:
            area = obj["map"]["size_m"][0] * obj["map"]["size_m"][1] / 1e6
            w.writerow([obj["scenario_id"], obj["name"], obj["difficulty"], "%.1f" % area, len(obj["fleet"]),
                        obj["task_generation"]["total_tasks"], obj["task_generation"]["from_templates"],
                        obj["task_generation"]["composite"], obj["task_generation"]["template_ratio"],
                        obj["mission_window_s"]])

    # what the agent sees for one task: no checkpoints, no grading hints
    first = scn_objs[0]["tasks"][0]
    agent_view = dict(
        task_id=first["task_id"], title=first["title"], category=first["category"],
        priority=first["priority"], window=first["window"], agents_required=first["agents_required"],
        brief=first["brief"], constraints=first["constraints"],
        failure_conditions=first["failure_conditions"], depends_on=first["depends_on"],
        scenario_context=dict(
            scenario_id=scn_objs[0]["scenario_id"], name=scn_objs[0]["name"],
            map=dict(size_m=scn_objs[0]["map"]["size_m"], altitude_ceiling_m=scn_objs[0]["map"]["altitude_ceiling_m"],
                     origin=scn_objs[0]["map"]["origin"]),
            lz=[z["id"] for z in scn_objs[0]["map"]["lz"]],
            nfz=[z["id"] for z in scn_objs[0]["map"]["nfz"]],
            zones={z["id"]: {k: v for k, v in z.items() if k in ("kind", "center", "radius_m", "width_m",
                                                                  "length_m", "size_m", "height_m")}
                   for z in scn_objs[0]["zones"]},
            fleet=scn_objs[0]["fleet"], environment=scn_objs[0]["environment"]),
        note="This is the exact payload the agent receives: checkpoints, grading and template ids are removed.",
    )
    dump(os.path.join(DIR_TOOL, "agent_view_example.json"), agent_view)

    total_tasks = sum(o["task_generation"]["total_tasks"] for o in scn_objs)
    manifest = dict(
        package="MultiUAV-Plat task templates and scenario design", language="en", version="1.0",
        track="lightweight model track (model < 150B total parameters)",
        generated_on="2026-11-03",
        contents=dict(task_templates=len(tpl_files), composite_patterns=len(cp_files),
                      scenarios=len(scn_objs), total_tasks=total_tasks),
        files=dict(templates=[n for n, _ in tpl_files],
                   composite_patterns=["composite_patterns/%s" % n for n, _ in cp_files],
                   scenarios=["%s_%s.json" % (o["scenario_id"], scn_file_name(o["name"])) for o in scn_objs]),
        validation=dict(status="pass" if not problems else "fail", problems=problems),
    )
    dump(os.path.join(ROOT, "manifest.json"), manifest)

    report = ["# validation report", "", "task templates: %d" % len(tpl_files),
              "composite patterns (supporting): %d" % len(cp_files),
              "scenarios: %d" % len(scn_objs),
              "total tasks: %d" % total_tasks, "", "problems: %s" % (problems if problems else "none")]
    with open(os.path.join(DIR_TOOL, "validation_report.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(report) + "\n")

    print("\n".join(report))
    for obj in scn_objs:
        g = obj["task_generation"]
        print("%s  %-52s tasks=%2d  tpl=%2d  comp=%2d  ratio=%.2f" %
              (obj["scenario_id"], obj["name"][:52], g["total_tasks"], g["from_templates"], g["composite"], g["template_ratio"]))


if __name__ == "__main__":
    main()
