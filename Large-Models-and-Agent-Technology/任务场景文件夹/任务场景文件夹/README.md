# MultiUAV-Plat — task templates and scenario package

Lightweight-track submission for the *Large-model agents for multi-UAV simulation* project.
This package is the **task / scenario design half** of the team deliverable: 10 English task
templates, 10 loadable mission scenarios, 209 task instances, a machine-readable index and a
validation report. The agent-side implementation is submitted separately by the teammate.

## Contents

| Path | What it is |
| --- | --- |
| `01_task_templates/` | 10 task templates, one JSON file each (`TT-01` … `TT-10`) |
| `01_task_templates/composite_patterns/` | 8 composite patterns (`CP-A` … `CP-H`) built from two templates each; supporting material, **not** counted in the 10 templates |
| `02_scenarios/` | 10 scenarios (`SC-01` … `SC-10`): map, geofence, no-fly zones, landing zones, zones, fleet, weather, comms, and every task instance |
| `03_design_document/` | design document (Chinese) describing templates, scenarios, checkpoints and integration |
| `04_tools/` | `all_tasks.csv` (flat list of 209 tasks), `scenario_summary.csv`, `validation_report.txt`, `agent_view_example.json` |
| `05_generator/` | the Python source that defines the templates and scenarios and regenerates every JSON file (`build.py` also runs the validator) |
| `manifest.json` | package inventory + validation status |

## Headline numbers

- task templates: **10**
- composite patterns: 8 (supporting)
- scenarios: **10**
- task instances: **209**, of which **144 (68.9%)** are instantiated from the 10 templates and 65 come from composite patterns
- tasks per scenario: 20–22 (requirement: ≥ 20)
- template-sourced ratio per scenario: 0.64–0.75 (requirement: ≥ 0.50)
- every task carries 5–6 checkpoints whose weights sum to **100**

## Data model

### Template file (`01_task_templates/TT-xx_*.json`)

```json
{
  "template_id": "TT-01",
  "name": "Area Coverage Survey",
  "category": "survey",
  "difficulty": 2,
  "agents": {"min": 2, "max": 4, "rec": 3},
  "objective": "one sentence, machine-safe summary",
  "brief": "the natural-language task text, with {parameter} placeholders",
  "parameters": [
    {"name": "area", "type": "zone_ref", "desc": "...", "example": "BLOCK-A1"},
    {"name": "coverage_pct", "type": "number", "unit": "%", "desc": "...", "range": [60, 95], "example": 85}
  ],
  "preconditions": ["..."],
  "mission_phases": [{"phase": "1-plan", "actions": ["..."], "output": "..."}],
  "checkpoints": [
    {"id": "CP-1", "name": "Team launch", "metric": "elapsed time ...", "target": "<= 120 s",
     "verify": "platform flight log", "weight": 20}
  ],
  "failure_conditions": ["any UAV leaves the geofence", "..."],
  "constraints": ["minimum separation {min_sep_m} m at all times"],
  "generation_rules": {"sampling": "...", "coverage_pct": [70, 95]},
  "instance_example": {"..." : "..."}
}
```

### Scenario file (`02_scenarios/SC-xx_*.json`)

```json
{
  "scenario_id": "SC-01",
  "name": "Urban Disaster Response - District 7",
  "difficulty": 4,
  "mission_window_s": 3600,
  "map": {"size_m": [4000, 4000], "origin": "SW corner, +x east, +y north",
          "altitude_ceiling_m": 150, "nfz": [...], "lz": [...]},
  "zones": [{"id": "BLOCK-A1", "kind": "survey_block", "center": [800, 2400], "radius_m": 350}],
  "fleet": [{"uav_id": "UAV-1", "model": "M20-quad", "endurance_min": 28, "cruise_mps": 12,
             "sensors": ["RGB", "thermal"], "role": "lead"}],
  "environment": {"wind_mps": 6, "gust_mps": 11, "visibility_km": 12, "time_of_day": "day", "temperature_c": 18},
  "comms": {"link_range_m": 1500, "relay_required_beyond_m": 1800, "degradation_events": [...]},
  "task_generation": {"seed": 20261104, "total_tasks": 21, "from_templates": 14,
                       "composite": 7, "template_ratio": 0.67},
  "tasks": [ { "task_id": "SC-01-T01", "...": "..." } ]
}
```

Zone `kind` values: `survey_block`, `poi`, `drop_zone`, `pickup`, `perimeter`, `search_area`,
`grid_area`, `corridor`, `track_area`, `ground_station`. Landing zones and no-fly zones live in
`map.lz` / `map.nfz`, not in `zones`.

### Task instance

| Field | Purpose |
| --- | --- |
| `task_id`, `template_id`, `source` | identity; `source` is `template` or `composite` |
| `title`, `category`, `difficulty`, `priority` | planning metadata (`priority`: `critical` / `high` / `normal`) |
| `window.start_s` / `window.end_s` | the only interval in which the task may be started |
| `depends_on` | earlier task ids that must be completed first (recovery, audit and MEDEVAC tasks have them) |
| `agents_required` | minimum number of UAVs the platform must allocate |
| `params` | the resolved parameter set (all placeholders substituted) |
| `brief`, `objective`, `mission_phases`, `constraints`, `failure_conditions` | **agent-facing** |
| `checkpoints` | **grading-side**: `id`, `name`, `metric`, `target`, `verification`, `weight` (sum = 100) |

## Platform integration

1. Load a scenario JSON; build the map from `map.size_m`, place `nfz` and `lz`, draw `zones`.
2. Spawn the fleet from `fleet`; apply `environment` and `comms`.
3. Push each task when its window opens; hand the agent only the agent-facing fields —
   `04_tools/agent_view_example.json` shows exactly that payload (checkpoints removed).
4. Score at window close: per task, `score = Σ(weight × checkpoint_pass) / 100`;
   per scenario, report `Σ task scores / task count`.
5. A triggered `failure_conditions` entry zeroes the task; it is not scored checkpoint by checkpoint.

## Validation

`04_tools/validation_report.txt` is regenerated on every build. Current state: `problems: none`.
Checks performed: checkpoint weights sum to 100 (templates, patterns and instances); ≥ 20 tasks per
scenario; template ratio > 0.50; every zone referenced by a parameter is defined in that scenario;
no unresolved `{placeholder}` left in agent-facing text; every task window fits inside
`mission_window_s`.

## Extending

Add a scenario by appending one dict (id, name, difficulty, mission_window_s, theme, map, zones,
fleet, environment, comms, plan) and re-running the generator; windows, checkpoint instantiation,
dependency wiring and the ratios are produced automatically. Seed is fixed at `20261104` for
reproducibility.

## Version

v1.0 — 2026-11-03. Task-scenario design half of the lightweight-track submission.
