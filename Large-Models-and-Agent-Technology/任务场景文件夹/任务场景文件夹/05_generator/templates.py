# -*- coding: utf-8 -*-
"""Task template definitions (TT-01 .. TT-10) and composite task patterns (CP-A .. CP-H)."""

TT = []

TT.append(dict(
    id="TT-01", name="Area Coverage Survey", category="survey", difficulty=2,
    agents=dict(min=2, max=4, rec=3),
    summary="Parallel-track (lawnmower) aerial survey of a bounded block with a hard coverage target.",
    title="Coverage survey of {area} ({coverage_pct}% target, {uav_count} UAVs)",
    objective=("Fly a complete parallel-track survey of {area} with {uav_count} UAVs at {altitude_m} m AGL and "
               "deliver a coverage record that reaches at least {coverage_pct} % of the block with {sidelap_pct} % "
               "side overlap between adjacent tracks."),
    brief=("Your team must survey the block {area} completely. Fly parallel tracks at {altitude_m} m AGL with "
           "{sidelap_pct} % side overlap and keep every UAV {min_sep_m} m apart. The survey is accepted only when "
           "the reported coverage of the block is at least {coverage_pct} %. Recover to {lz} when the coverage "
           "target is met or the task window closes."),
    params=[
        dict(name="area", type="zone_ref", unit="zone id", required=True, desc="Survey block identifier from the scenario zone catalogue", example="BLOCK-A1"),
        dict(name="coverage_pct", type="int", unit="%", required=True, desc="Minimum union coverage of the block", example=85, range=[80, 95]),
        dict(name="altitude_m", type="int", unit="m AGL", required=True, desc="Survey altitude; sets ground sample distance", example=60, range=[40, 90]),
        dict(name="sidelap_pct", type="enum", unit="%", required=True, desc="Side overlap between adjacent tracks", example=20, choices=[15, 20, 25, 30]),
        dict(name="uav_count", type="int", unit="UAVs", required=True, desc="Number of UAVs assigned by the agent", example=3, range=[2, 4]),
        dict(name="min_sep_m", type="int", unit="m", required=True, desc="Minimum separation between any two UAVs", example=15, range=[10, 25]),
        dict(name="lz", type="zone_ref", unit="zone id", required=True, desc="Recovery landing zone", example="LZ-CHURCH"),
    ],
    preconditions=["all assigned UAVs are on the ground at a valid landing zone", "battery state of charge >= 85%"],
    phases=[
        dict(phase="1-plan", actions=["decompose {area} into {uav_count} track strips", "assign one strip set per UAV", "declare track spacing and heading"], output="track plan with strip ids and entry points"),
        dict(phase="2-launch", actions=["take off in sequence", "climb to {altitude_m} m", "form a safe line before entering the block"], output="airborne team inside the block"),
        dict(phase="3-survey", actions=["fly strips with {sidelap_pct} % side overlap", "report coverage after each strip", "re-fly any strip that reports a gap"], output="coverage record per strip"),
        dict(phase="4-verify", actions=["aggregate coverage raster", "compare with {coverage_pct} % target", "declare complete or request a re-fly"], output="coverage verification statement"),
        dict(phase="5-recover", actions=["leave the block", "land at {lz} with separation", "report per-UAV flight time"], output="recovered fleet"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Team launch", metric="elapsed time from task start to last UAV inside {area}", target="<= 120 s and all {uav_count} UAVs airborne", verify="platform event log", weight=15),
        dict(id="CP-2", name="Track discipline", metric="cross-track error per strip and side overlap between adjacent strips", target="cross-track error <= 8 m; overlap >= {sidelap_pct} %", verify="platform track raster", weight=20),
        dict(id="CP-3", name="Coverage target", metric="union coverage of {area} at the recorded altitude", target=">= {coverage_pct} %", verify="platform coverage grid", weight=35),
        dict(id="CP-4", name="Airspace integrity", metric="time outside geofence and inside no-fly zones", target="0 s outside geofence, 0 s inside any no-fly zone", verify="platform geofence monitor", weight=15),
        dict(id="CP-5", name="Recovery", metric="landing separation and hard-landing flag", target="all UAVs on ground at {lz}, separation >= 10 m, no hard landing", verify="platform landing log", weight=15),
    ],
    failures=["any UAV leaves the geofence", "any UAV enters a no-fly zone", "coverage below 60% when the task window closes", "loss-of-control or collision flag"],
    constraints=["minimum separation {min_sep_m} m at all times", "single sortie per UAV for this task", "no manual pilot override"],
    gen=dict(sampling=dict(coverage_pct=[80, 85, 90, 95], altitude_m=[40, 50, 60, 70, 80], sidelap_pct=[15, 20, 25, 30], uav_count=[2, 3, 4]), variants=["day survey", "low-contrast surface", "wind 6-10 m/s"]),
    example=dict(area="BLOCK-A1", coverage_pct=85, altitude_m=60, sidelap_pct=20, uav_count=3, min_sep_m=15, lz="LZ-CHURCH"),
))

TT.append(dict(
    id="TT-02", name="Point Inspection", category="inspection", difficulty=2,
    agents=dict(min=1, max=3, rec=2),
    summary="Close-range multi-angle inspection of one point of interest with standoff and image quality rules.",
    title="Point inspection of {poi} ({standoff_m} m standoff, {images} images)",
    objective=("Inspect {poi} from {standoff_m} m standoff at {capture_alt_m} m AGL, capture at least {images} "
               "images per viewing angle with the gimbal pitched {pitch_deg} deg down, and hold each capture "
               "position for {hover_s} s."),
    brief=("Inspect {poi}. Hold a standoff of {standoff_m} m (tolerance 3 m) at {capture_alt_m} m AGL, aim the "
           "gimbal {pitch_deg} deg down and take at least {images} images per face. Hover at least {hover_s} s at "
           "each capture point so the image is stable. Do not enter the {min_sep_m} m keep-out radius around "
           "{poi} and keep {uav_sep_m} m from your teammate."),
    params=[
        dict(name="poi", type="zone_ref", unit="zone id", required=True, desc="Point of interest identifier", example="POI-HOSPITAL-ROOF"),
        dict(name="standoff_m", type="int", unit="m", required=True, desc="Horizontal distance kept from the target", example=15, range=[8, 25]),
        dict(name="capture_alt_m", type="int", unit="m AGL", required=True, desc="Capture altitude", example=25, range=[15, 45]),
        dict(name="images", type="int", unit="images/angle", required=True, desc="Minimum images per viewing angle", example=4, range=[3, 6]),
        dict(name="hover_s", type="int", unit="s", required=True, desc="Minimum hover time per capture point", example=8, range=[5, 12]),
        dict(name="pitch_deg", type="int", unit="deg", required=True, desc="Gimbal pitch at capture", example=30, range=[0, 45]),
        dict(name="min_sep_m", type="int", unit="m", required=True, desc="Keep-out radius around the target", example=8, range=[5, 10]),
        dict(name="uav_sep_m", type="int", unit="m", required=True, desc="Minimum separation between UAVs", example=20, range=[15, 30]),
    ],
    preconditions=["scene loaded and target visible from the approach direction", "onboard storage >= 2 GB free per UAV"],
    phases=[
        dict(phase="1-plan", actions=["choose approach azimuth", "split capture angles between UAVs", "declare hover points"], output="inspection plan with capture points"),
        dict(phase="2-approach", actions=["climb to {capture_alt_m} m", "move to the {standoff_m} m standoff ring"], output="stable standoff positions"),
        dict(phase="3-capture", actions=["hover {hover_s} s per point", "capture {images} images per angle with {pitch_deg} deg pitch"], output="image set with metadata"),
        dict(phase="4-verify", actions=["check image count and pitch per angle", "re-capture any angle short of {images} images"], output="inspection verification"),
        dict(phase="5-depart", actions=["retreat along the planned azimuth", "land or hand over"], output="cleared airspace around the target"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Standoff", metric="horizontal distance to {poi} during capture", target="{standoff_m} +/- 3 m", verify="platform range telemetry", weight=20),
        dict(id="CP-2", name="Image set", metric="images captured per viewing angle at {capture_alt_m} m", target=">= {images} images for each of >= 3 angles", verify="platform payload log", weight=30),
        dict(id="CP-3", name="Hover stability", metric="hover time at each declared capture point", target=">= {hover_s} s per point", verify="platform state log", weight=15),
        dict(id="CP-4", name="Gimbal aim", metric="gimbal pitch during capture", target="{pitch_deg} deg +/- 5 deg", verify="gimbal telemetry", weight=15),
        dict(id="CP-5", name="Separation", metric="UAV-to-UAV distance and UAV-to-target distance", target=">= {uav_sep_m} m between UAVs and >= {min_sep_m} m to the target", verify="platform proximity monitor", weight=20),
    ],
    failures=["entering the {min_sep_m} m keep-out radius", "fewer than one complete angle set at task end", "loss of link during capture with no recovery"],
    constraints=["hover only at declared capture points", "no flight below {capture_alt_m} m in the inspection ring"],
    gen=dict(sampling=dict(standoff_m=[8, 12, 15, 20], capture_alt_m=[15, 20, 25, 30, 45], images=[3, 4, 5, 6], hover_s=[5, 6, 8, 10, 12], pitch_deg=[0, 15, 30, 45]), variants=["day", "backlit", "after rain"]),
    example=dict(poi="POI-HOSPITAL-ROOF", standoff_m=15, capture_alt_m=25, images=4, hover_s=8, pitch_deg=30, min_sep_m=8, uav_sep_m=20),
))

TT.append(dict(
    id="TT-03", name="Payload Delivery", category="logistics", difficulty=2,
    agents=dict(min=2, max=4, rec=2),
    summary="Pick up a payload, deliver it to a drop zone inside an accuracy and time budget, keep it intact.",
    title="Payload delivery to {drop_zone} ({payload_kg} kg, +/-{accuracy_m} m)",
    objective=("Move {payload_kg} kg from {pickup} to {drop_zone} and release it so that the impact point is within "
               "{accuracy_m} m of the drop-zone centre, with an impact speed below 3 m/s."),
    brief=("Deliver {payload_kg} kg from {pickup} to {drop_zone} before t = {deadline_s} s. Release from at least "
           "{drop_alt_m} m so the payload is not damaged; the landing point must be within {accuracy_m} m of the "
           "drop-zone centre and the impact speed below 3 m/s. One teammate may carry the payload while the other "
           "screens the corridor. Recover to {lz} afterwards."),
    params=[
        dict(name="pickup", type="zone_ref", unit="zone id", required=True, desc="Pickup point with the payload attached on the ground", example="PICKUP-DEPOT"),
        dict(name="drop_zone", type="zone_ref", unit="zone id", required=True, desc="Target drop zone", example="DZ-SCHOOL-YARD"),
        dict(name="payload_kg", type="float", unit="kg", required=True, desc="Payload mass", example=1.5, range=[0.5, 2.5]),
        dict(name="drop_alt_m", type="int", unit="m AGL", required=True, desc="Release altitude", example=15, range=[8, 20]),
        dict(name="accuracy_m", type="enum", unit="m", required=True, desc="Circular error allowed at the drop zone", example=3, choices=[2, 3, 4, 5]),
        dict(name="deadline_s", type="int", unit="s", required=True, desc="Latest release time", example=420, range=[300, 600]),
        dict(name="lz", type="zone_ref", unit="zone id", required=True, desc="Recovery landing zone", example="LZ-PARK"),
    ],
    preconditions=["payload secured on the pickup point", "carrier UAV shows payload mass in its pre-takeoff check"],
    phases=[
        dict(phase="1-plan", actions=["choose the delivery UAV and the escort role", "pick a route that avoids no-fly zones"], output="route and role assignment"),
        dict(phase="2-pickup", actions=["land or hover at {pickup}", "latch the payload", "verify mass reading"], output="payload latched"),
        dict(phase="3-transit", actions=["fly the route", "report position on corridor waypoints"], output="arrival over {drop_zone}"),
        dict(phase="4-drop", actions=["descend to {drop_alt_m} m", "align within {accuracy_m} m of the centre", "release and confirm"], output="payload on the ground"),
        dict(phase="5-recover", actions=["climb clear of the drop area", "land at {lz}"], output="recovered fleet"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Payload secured", metric="latch event and mass reading", target="payload latched at {pickup} before t = 120 s", verify="platform payload telemetry", weight=20),
        dict(id="CP-2", name="Drop accuracy", metric="distance from impact point to {drop_zone} centre", target="<= {accuracy_m} m", verify="platform impact record", weight=30),
        dict(id="CP-3", name="Payload integrity", metric="impact speed and damage flag", target="impact speed < 3 m/s and damage flag = 0", verify="platform impact record", weight=20),
        dict(id="CP-4", name="Time budget", metric="release time", target="<= {deadline_s} s after task start", verify="platform event log", weight=15),
        dict(id="CP-5", name="Recovery", metric="all UAVs landed", target="all UAVs on the ground at {lz}", verify="platform landing log", weight=15),
    ],
    failures=["payload released above 25 m", "payload damage flag raised", "delivery after the task window closes"],
    constraints=["single payload per task", "no release over a no-fly zone", "escort UAV keeps >= 20 m from the carrier"],
    gen=dict(sampling=dict(payload_kg=[0.5, 1.0, 1.5, 2.0, 2.5], drop_alt_m=[8, 12, 15, 20], accuracy_m=[2, 3, 4, 5], deadline_s=[300, 360, 420, 540]), variants=["calm", "crosswind 8 m/s", "crowded corridor"]),
    example=dict(pickup="PICKUP-DEPOT", drop_zone="DZ-SCHOOL-YARD", payload_kg=1.5, drop_alt_m=15, accuracy_m=3, deadline_s=420, lz="LZ-PARK"),
))

TT.append(dict(
    id="TT-04", name="Formation Corridor Transit", category="formation", difficulty=3,
    agents=dict(min=3, max=5, rec=4),
    summary="Assemble a formation, hold it through a narrow corridor, and arrive on time with the formation intact.",
    title="Formation transit through {corridor} ({formation}, {spacing_m} m spacing)",
    objective=("Assemble a {formation} formation with {spacing_m} m spacing and transit {corridor} end to end "
               "within {time_budget_s} s, keeping formation error within tolerance and no separation conflict."),
    brief=("Fly from {entry} to {exit} through {corridor} in a {formation} formation with {spacing_m} m spacing. "
           "Assemble the formation before the corridor entry; inside the corridor there are obstacle edges within "
           "{corridor_width_m} m. Keep formation error small, never trigger a separation warning, and complete the "
           "transit within {time_budget_s} s.{emcon_text}"),
    params=[
        dict(name="corridor", type="zone_ref", unit="zone id", required=True, desc="Corridor geometry identifier", example="CORRIDOR-URBAN-1"),
        dict(name="entry", type="zone_ref", unit="zone id", required=True, desc="Corridor entry waypoint", example="WP-ENTRY-N"),
        dict(name="exit", type="zone_ref", unit="zone id", required=True, desc="Corridor exit waypoint", example="WP-EXIT-S"),
        dict(name="formation", type="enum", unit="shape", required=True, desc="Formation shape", example="column", choices=["line", "column", "V", "diamond"]),
        dict(name="spacing_m", type="int", unit="m", required=True, desc="Nominal spacing between neighbours", example=20, range=[10, 30]),
        dict(name="time_budget_s", type="int", unit="s", required=True, desc="Transit time budget entry to exit", example=300, range=[240, 480]),
        dict(name="corridor_width_m", type="int", unit="m", required=True, desc="Usable corridor width", example=60, range=[40, 90]),
        dict(name="emcon", type="bool", unit="flag", required=True, desc="Radio silence inside the corridor", example=False),
    ],
    preconditions=["all UAVs airborne and inside the staging area", "formation shape agreed by all members"],
    phases=[
        dict(phase="1-plan", actions=["choose the formation geometry and slot assignment", "pick entry and exit alignment"], output="slot table"),
        dict(phase="2-assemble", actions=["hold at the staging point", "build the {formation} formation with {spacing_m} m spacing"], output="formation ready at entry"),
        dict(phase="3-transit", actions=["fly the corridor at constant speed", "correct drift back to slot"], output="fleet at the exit"),
        dict(phase="4-verify", actions=["check formation error at exit", "report transit time"], output="transit statement"),
        dict(phase="5-recover", actions=["break formation safely", "land at {lz}"], output="recovered fleet"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Formation assembly", metric="formation error at corridor entry", target="<= 0.5 x {spacing_m} m at entry", verify="platform formation monitor", weight=20),
        dict(id="CP-2", name="Formation hold", metric="mean and peak formation error inside the corridor", target="mean <= 3 m, peak <= 6 m", verify="platform formation monitor", weight=30),
        dict(id="CP-3", name="Transit time", metric="time from entry to exit", target="<= {time_budget_s} s", verify="platform event log", weight=20),
        dict(id="CP-4", name="Separation safety", metric="separation warnings between members", target="0 warnings below the {spacing_m} m nominal spacing minus 30%", verify="platform proximity monitor", weight=15),
        dict(id="CP-5", name="Comms discipline", metric="radio transmissions inside the corridor", target="{emcon_target}", verify="platform link log", weight=15),
    ],
    failures=["corridor obstacle strike", "formation broken for more than 30 s", "UAV exits the corridor volume"],
    constraints=["constant speed inside the corridor", "no re-planning that changes slot order mid-corridor"],
    gen=dict(sampling=dict(formation=["line", "column", "V", "diamond"], spacing_m=[10, 15, 20, 25, 30], time_budget_s=[240, 300, 360, 480], emcon=[True, False]), variants=["day", "dusk", "crosswind"]),
    example=dict(corridor="CORRIDOR-URBAN-1", entry="WP-ENTRY-N", exit="WP-EXIT-S", formation="column", spacing_m=20, time_budget_s=300, corridor_width_m=60, emcon=False),
))

TT.append(dict(
    id="TT-05", name="Perimeter Patrol and Intrusion Response", category="security", difficulty=3,
    agents=dict(min=2, max=4, rec=3),
    summary="Sustain a perimeter patrol, detect an intrusion, identify it positively and shadow it before it leaves.",
    title="Perimeter patrol and intrusion response at {perimeter} (sector {intruder_sector})",
    objective=("Keep a continuous patrol of {perimeter} at {patrol_alt_m} m, detect an intrusion entering at sector "
               "{intruder_sector} within {detect_window_s} s, identify it positively and shadow it inside "
               "{intercept_radius_m} m of the perimeter."),
    brief=("Patrol the closed perimeter {perimeter} at {patrol_alt_m} m, one UAV per assigned sector, and log a "
           "complete loop every {loop_period_s} s. An intruder will enter from sector {intruder_sector}; detect it "
           "within {detect_window_s} s, identify it (hover >= 5 s within 60 m) and shadow it while it stays within "
           "{intercept_radius_m} m of the perimeter. Do not leave the geofence while chasing."),
    params=[
        dict(name="perimeter", type="zone_ref", unit="zone id", required=True, desc="Closed perimeter identifier", example="PERIM-DEPOT"),
        dict(name="patrol_alt_m", type="int", unit="m AGL", required=True, desc="Patrol altitude", example=80, range=[50, 120]),
        dict(name="loop_period_s", type="int", unit="s", required=True, desc="Time to complete one perimeter loop", example=180, range=[120, 300]),
        dict(name="intruder_sector", type="enum", unit="sector", required=True, desc="Sector where the intrusion starts", example="S3", choices=["S1", "S2", "S3", "S4"]),
        dict(name="detect_window_s", type="int", unit="s", required=True, desc="Detection latency budget", example=45, range=[20, 90]),
        dict(name="intercept_radius_m", type="int", unit="m", required=True, desc="Shadowing radius around the perimeter", example=300, range=[150, 500]),
    ],
    preconditions=["perimeter patrol route approved", "electro-optical payload calibrated"],
    phases=[
        dict(phase="1-plan", actions=["split {perimeter} into sectors", "assign one UAV per sector with overlap"], output="sector plan"),
        dict(phase="2-patrol", actions=["fly the loop at {patrol_alt_m} m", "report loop completion each {loop_period_s} s"], output="continuous patrol log"),
        dict(phase="3-detect", actions=["report the intrusion within {detect_window_s} s", "hand the track to the nearest UAV"], output="intrusion track"),
        dict(phase="4-identify", actions=["close to 60 m", "hover 5 s for identification", "shadow within {intercept_radius_m} m"], output="identification record"),
        dict(phase="5-resume", actions=["return to the abandoned sector", "rebuild the patrol loop"], output="patrol restored"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Patrol continuity", metric="perimeter length covered per loop and loop period", target=">= 90% of the perimeter each loop, period <= {loop_period_s} s", verify="platform route log", weight=20),
        dict(id="CP-2", name="Detection latency", metric="time from intrusion to first detection report", target="<= {detect_window_s} s", verify="platform detection log", weight=25),
        dict(id="CP-3", name="Positive identification", metric="hover time within 60 m of the intruder", target=">= 5 s hover and ID record submitted", verify="platform payload/ID log", weight=20),
        dict(id="CP-4", name="Shadowing", metric="intruder distance from the perimeter while shadowed", target="intruder never beyond {intercept_radius_m} m unshadowed for > 20 s", verify="platform track log", weight=20),
        dict(id="CP-5", name="Patrol resume", metric="time to restore the abandoned sector", target="<= 90 s after the intruder leaves", verify="platform route log", weight=15),
    ],
    failures=["perimeter left uncovered for more than 120 s", "geofence breach during the pursuit", "intruder never detected"],
    constraints=["minimum altitude {patrol_alt_m} m - 20 m", "no pursuit outside {intercept_radius_m} m"],
    gen=dict(sampling=dict(patrol_alt_m=[60, 80, 100], loop_period_s=[120, 180, 240], detect_window_s=[30, 45, 60, 90], intercept_radius_m=[200, 300, 400]), variants=["day", "night", "fog 2 km"]),
    example=dict(perimeter="PERIM-DEPOT", patrol_alt_m=80, loop_period_s=180, intruder_sector="S3", detect_window_s=45, intercept_radius_m=300),
))

TT.append(dict(
    id="TT-06", name="Cooperative Search and Rescue", category="search", difficulty=4,
    agents=dict(min=2, max=5, rec=3),
    summary="Sweep a search area cooperatively, localise a distress beacon, mark it and guide a ground element.",
    title="Search and rescue sweep of {search_area} (sweep {sweep_width_m} m, beacon in {beacon_cell})",
    objective=("Sweep {search_area} with {uav_count} UAVs at {detection_alt_m} m using a {sweep_width_m} m sweep "
               "width until the distress beacon is detected, localised to within {localize_err_m} m and marked."),
    brief=("A distress beacon is active somewhere in {search_area}. Sweep the area cooperatively at "
           "{detection_alt_m} m with {sweep_width_m} m spacing between tracks so that at least 95% of the area is "
           "observed once. When a UAV detects the signal, localise it to within {localize_err_m} m, mark the spot "
           "(hover >= 10 s and transmit the position) and hand the position to the rescue element. Do not leave "
           "uncovered strips when the first detection happens."),
    params=[
        dict(name="search_area", type="zone_ref", unit="zone id", required=True, desc="Search area identifier", example="AREA-RIDGE"),
        dict(name="uav_count", type="int", unit="UAVs", required=True, desc="UAVs committed to the sweep", example=3, range=[2, 5]),
        dict(name="sweep_width_m", type="enum", unit="m", required=True, desc="Spacing between adjacent search tracks", example=80, choices=[60, 80, 100, 120]),
        dict(name="detection_alt_m", type="int", unit="m AGL", required=True, desc="Search altitude", example=70, range=[40, 100]),
        dict(name="beacon_cell", type="enum", unit="cell", required=True, desc="Cell holding the beacon (unknown to the agent)", example="C4", choices=["C2", "C3", "C4", "C5", "C6"]),
        dict(name="localize_err_m", type="int", unit="m", required=True, desc="Positional error allowed at marking", example=20, range=[10, 30]),
    ],
    preconditions=["search area boundaries published", "beacon frequency known to the payload"],
    phases=[
        dict(phase="1-plan", actions=["split {search_area} into {uav_count} sweep lanes", "declare track spacing {sweep_width_m} m"], output="sweep plan"),
        dict(phase="2-sweep", actions=["fly the lanes at {detection_alt_m} m", "report lane completion"], output="area coverage log"),
        dict(phase="3-detect", actions=["report the beacon to the team", "convert bearing into a position"], output="beacon position estimate"),
        dict(phase="4-mark", actions=["close to the beacon", "hover >= 10 s and transmit", "hand the position over"], output="marked distress point"),
        dict(phase="5-complete", actions=["finish any uncovered lane", "recover to {lz}"], output="complete sweep statement"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Sweep coverage", metric="area observed at least once before task end", target=">= 95% of {search_area}", verify="platform coverage grid", weight=25),
        dict(id="CP-2", name="Detection", metric="beacon detection event", target="detected before the task window closes", verify="platform sensor log", weight=20),
        dict(id="CP-3", name="Localisation", metric="error of the transmitted beacon position", target="<= {localize_err_m} m", verify="platform ground-truth comparison", weight=20),
        dict(id="CP-4", name="Marking and handover", metric="hover time at the beacon and handover message", target="hover >= 10 s and handover message sent", verify="platform payload log", weight=20),
        dict(id="CP-5", name="No uncovered strip", metric="uncovered strips left when detection happens", target="0 strips wider than 1.5 x {sweep_width_m}", verify="platform coverage grid", weight=15),
    ],
    failures=["beacon not detected within the window", "marked position error above 40 m", "abandoning the sweep to chase a false signal"],
    constraints=["minimum separation {min_sep_m} m", "no flight inside the marked danger radius before the rescue element arrives"],
    gen=dict(sampling=dict(uav_count=[2, 3, 4, 5], sweep_width_m=[60, 80, 100, 120], detection_alt_m=[40, 60, 70, 100], localize_err_m=[10, 20, 30]), variants=["day", "dusk", "smoke 1.5 km"]),
    example=dict(search_area="AREA-RIDGE", uav_count=3, sweep_width_m=80, detection_alt_m=70, beacon_cell="C4", localize_err_m=20),
))

TT.append(dict(
    id="TT-07", name="Communication Relay Chain", category="comms", difficulty=3,
    agents=dict(min=2, max=4, rec=3),
    summary="Build and hold an airborne relay chain so that two ground elements keep a usable link.",
    title="Relay chain between {site_a} and {site_b} ({hops} hops, {hold_min} min)",
    objective=("Position {hops} UAVs so that {site_a} and {site_b} keep an end-to-end link with RSSI >= "
               "{min_rssi_dbm} dBm for {hold_min} min, and rebuild the chain within 45 s if one relay is lost."),
    brief=("Link {site_a} and {site_b}, which are {separation_m} m apart and cannot see each other. Fly a relay "
           "chain of {hops} UAVs at {chain_alt_m} m AGL, keep the end-to-end link above {min_rssi_dbm} dBm for "
           "{hold_min} min, and rebuild the chain within 45 s if a relay drops. No UAV may hover closer than "
           "{min_sep_m} m to another."),
    params=[
        dict(name="site_a", type="zone_ref", unit="zone id", required=True, desc="First ground station", example="GS-BASE"),
        dict(name="site_b", type="zone_ref", unit="zone id", required=True, desc="Second ground station (blocked line of sight)", example="GS-VALLEY"),
        dict(name="hops", type="int", unit="relays", required=True, desc="Number of relay UAVs", example=2, range=[2, 4]),
        dict(name="chain_alt_m", type="int", unit="m AGL", required=True, desc="Relay altitude", example=120, range=[80, 160]),
        dict(name="min_rssi_dbm", type="int", unit="dBm", required=True, desc="Link quality floor", example=-85, range=[-95, -75]),
        dict(name="hold_min", type="int", unit="min", required=True, desc="Link holding time", example=10, range=[5, 15]),
        dict(name="separation_m", type="int", unit="m", required=True, desc="Distance between the two ground stations", example=4200, range=[3000, 6000]),
        dict(name="min_sep_m", type="int", unit="m", required=True, desc="Minimum separation between relays", example=100, range=[60, 150]),
    ],
    preconditions=["both ground stations powered and logging", "link budget table available"],
    phases=[
        dict(phase="1-plan", actions=["compute relay positions on the {site_a}-{site_b} line", "assign one altitude per relay to reduce interference"], output="relay slot table"),
        dict(phase="2-deploy", actions=["fly to the slots at {chain_alt_m} m", "declare link up"], output="chain established"),
        dict(phase="3-hold", actions=["hold the slots for {hold_min} min", "report RSSI every minute"], output="link quality log"),
        dict(phase="4-recover-link", actions=["replace a lost relay within 45 s", "re-balance the slots"], output="link restored"),
        dict(phase="5-withdraw", actions=["hand over or land at {lz}"], output="recovered fleet"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Chain setup", metric="time to first end-to-end link", target="<= 180 s", verify="platform link log", weight=20),
        dict(id="CP-2", name="Link continuity", metric="fraction of {hold_min} min with RSSI >= {min_rssi_dbm} dBm", target=">= 98%", verify="platform link log", weight=30),
        dict(id="CP-3", name="Re-form after loss", metric="time to restore the link after an injected relay loss", target="<= 45 s", verify="platform link log", weight=20),
        dict(id="CP-4", name="Packet delivery", metric="end-to-end packet loss", target="<= 2%", verify="platform link statistic", weight=15),
        dict(id="CP-5", name="Relay separation", metric="distance between relay stations", target=">= {min_sep_m} m", verify="platform proximity monitor", weight=15),
    ],
    failures=["link down for more than 60 s in total", "relay chain not established by t = 300 s", "both relays hovering at the same slot"],
    constraints=["relay altitude >= {chain_alt_m} m - 20 m", "no relay outside the geofence"],
    gen=dict(sampling=dict(hops=[2, 3, 4], chain_alt_m=[80, 100, 120, 140], min_rssi_dbm=[-95, -90, -85, -80], hold_min=[5, 8, 10, 15]), variants=["calm", "rain fade", "jamming burst"]),
    example=dict(site_a="GS-BASE", site_b="GS-VALLEY", hops=2, chain_alt_m=120, min_rssi_dbm=-85, hold_min=10, separation_m=4200, min_sep_m=100),
))

TT.append(dict(
    id="TT-08", name="Multi-Target Tracking", category="tracking", difficulty=4,
    agents=dict(min=2, max=5, rec=3),
    summary="Track several moving ground targets simultaneously, keep track quality and hand targets between UAVs.",
    title="Track {n_targets} moving targets in {track_area} ({duration_min} min)",
    objective=("Track {n_targets} independent ground targets inside {track_area} for {duration_min} min, keeping each "
               "target locked for at least 90% of the time and handing targets between UAVs without losing track."),
    brief=("{n_targets} vehicles move inside {track_area}. Keep every target under continuous observation for "
           "{duration_min} min from no closer than {min_standoff_m} m and no further than {max_standoff_m} m. When "
           "a target crosses into another UAV's sector, hand it over so that no track is lost for more than 10 s. "
           "Report target count and positions every 30 s."),
    params=[
        dict(name="track_area", type="zone_ref", unit="zone id", required=True, desc="Area containing the moving targets", example="AREA-MARKET"),
        dict(name="n_targets", type="int", unit="targets", required=True, desc="Number of moving targets", example=3, range=[2, 5]),
        dict(name="duration_min", type="int", unit="min", required=True, desc="Tracking duration", example=10, range=[5, 15]),
        dict(name="min_standoff_m", type="int", unit="m", required=True, desc="Closest approach to a target", example=60, range=[40, 100]),
        dict(name="max_standoff_m", type="int", unit="m", required=True, desc="Furthest acceptable standoff for track quality", example=250, range=[150, 400]),
    ],
    preconditions=["target telemetry enabled on the platform", "payload gimbal calibrated"],
    phases=[
        dict(phase="1-plan", actions=["divide {track_area} into observation sectors", "assign primary and backup observed per sector"], output="sector and target assignment"),
        dict(phase="2-acquire", actions=["find all {n_targets} targets", "declare lock per target"], output="all targets locked"),
        dict(phase="3-maintain", actions=["keep standoff between {min_standoff_m} and {max_standoff_m} m", "report counts every 30 s"], output="track log"),
        dict(phase="4-handoff", actions=["hand targets that cross sectors", "confirm the receiving UAV has the lock"], output="handoff records"),
        dict(phase="5-complete", actions=["report final target set", "recover to {lz}"], output="tracking summary"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Acquisition", metric="time until all {n_targets} targets are locked", target="<= 120 s", verify="platform tracking log", weight=20),
        dict(id="CP-2", name="Per-target continuity", metric="fraction of {duration_min} min with each target locked", target=">= 90% per target", verify="platform tracking log", weight=30),
        dict(id="CP-3", name="Standoff window", metric="distance to tracked targets", target="all lock samples within {min_standoff_m}-{max_standoff_m} m", verify="platform range telemetry", weight=20),
        dict(id="CP-4", name="Handoff quality", metric="track gap during sector handoff", target="no gap longer than 10 s", verify="platform tracking log", weight=15),
        dict(id="CP-5", name="Safety", metric="separation between tracking UAVs", target=">= 40 m at all times", verify="platform proximity monitor", weight=15),
    ],
    failures=["two or more targets lost at the same time", "entering the {min_standoff_m} m standoff of any target", "UAV-UAV separation warning"],
    constraints=["no target identification reveal before classification is complete", "no descent below 50 m over the track area"],
    gen=dict(sampling=dict(n_targets=[2, 3, 4, 5], duration_min=[5, 8, 10, 15], min_standoff_m=[40, 60, 80], max_standoff_m=[150, 200, 250, 400]), variants=["day", "night thermal", "occlusion by buildings"]),
    example=dict(track_area="AREA-MARKET", n_targets=3, duration_min=10, min_standoff_m=60, max_standoff_m=250),
))

TT.append(dict(
    id="TT-09", name="Grid Sampling and Revisit", category="sampling", difficulty=3,
    agents=dict(min=1, max=4, rec=2),
    summary="Collect sensor samples on a fixed grid and return to the same points at a required interval.",
    title="Grid sampling of {grid_area} ({n_points} points, revisit every {interval_min} min)",
    objective=("Collect at least {revisit_count} sensor samples at each of {n_points} grid points inside {grid_area} "
               "with a revisit interval of {interval_min} +/- 2 min, and keep every sample geotagged within 5 m."),
    brief=("Sample the {n_points} points of the {grid_area} grid with the {sensor} sensor. Every point needs at "
           "least {revisit_count} samples, taken {interval_min} +/- 2 min apart, and each sample must be geotagged "
           "within 5 m. Plan the order so that the revisit interval is respected without waiting on the ground."),
    params=[
        dict(name="grid_area", type="zone_ref", unit="zone id", required=True, desc="Sampling grid area", example="GRID-ORCHARD"),
        dict(name="n_points", type="int", unit="points", required=True, desc="Number of sample points", example=12, range=[8, 20]),
        dict(name="interval_min", type="int", unit="min", required=True, desc="Revisit interval", example=8, range=[6, 12]),
        dict(name="revisit_count", type="int", unit="samples/point", required=True, desc="Samples required per point", example=2, range=[2, 3]),
        dict(name="sensor", type="enum", unit="sensor", required=True, desc="Sensor used for the samples", example="thermal", choices=["RGB", "thermal", "multispectral", "gas"]),
    ],
    preconditions=["sensor warmed up and calibrated", "grid geometry published to the team"],
    phases=[
        dict(phase="1-plan", actions=["order the {n_points} points into a route that meets the {interval_min} min revisit", "assign points to UAVs"], output="sampling schedule"),
        dict(phase="2-sample", actions=["fly to each point", "hold >= 3 s and take the {sensor} sample"], output="sample log"),
        dict(phase="3-revisit", actions=["return to each point {interval_min} min after the first sample", "take the repeat sample"], output="revisit log"),
        dict(phase="4-upload", actions=["upload samples", "verify checksums and geotags"], output="complete data set"),
        dict(phase="5-recover", actions=["land at {lz}"], output="recovered fleet"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Sample completeness", metric="samples collected per grid point", target=">= {revisit_count} samples at all {n_points} points", verify="platform payload log", weight=30),
        dict(id="CP-2", name="Revisit timing", metric="interval between samples at the same point", target="{interval_min} +/- 2 min", verify="platform payload log", weight=20),
        dict(id="CP-3", name="Geotag accuracy", metric="sample position error", target="<= 5 m per sample", verify="platform payload metadata", weight=20),
        dict(id="CP-4", name="Data integrity", metric="uploaded samples and checksum status", target="100% uploaded, checksums OK", verify="platform data log", weight=15),
        dict(id="CP-5", name="Recovery", metric="all UAVs landed at {lz}", target="all on ground, no hard landing", verify="platform landing log", weight=15),
    ],
    failures=["fewer than half of the points visited", "revisit interval outside 50% tolerance", "corrupted sample set"],
    constraints=["hover >= 3 s at every point", "no sampling above 30 m AGL"],
    gen=dict(sampling=dict(n_points=[8, 10, 12, 16, 20], interval_min=[6, 8, 10, 12], revisit_count=[2, 3], sensor=["RGB", "thermal", "multispectral", "gas"]), variants=["day", "dawn mist", "night"]),
    example=dict(grid_area="GRID-ORCHARD", n_points=12, interval_min=8, revisit_count=2, sensor="thermal"),
))

TT.append(dict(
    id="TT-10", name="Fault-Tolerant Reallocation and Return", category="recovery", difficulty=5,
    agents=dict(min=3, max=5, rec=4),
    summary="A UAV fails mid-mission: declare the fault, reallocate coverage to the rest of the fleet, recover safely.",
    title="Fault recovery for {fault_uav} ({fault_type} at t={fault_at_s} s) during {critical_task}",
    objective=("When {fault_uav} declares a {fault_type} fault at t = {fault_at_s} s, reallocate its share of "
               "{critical_task} to the remaining fleet so that critical coverage stays above "
               "{critical_coverage_pct} %, and recover {fault_uav} to {rtb_zone} without any other conflict."),
    brief=("During {critical_task} the UAV {fault_uav} will suffer a {fault_type} fault at about t = {fault_at_s} s. "
           "The mission still requires at least {critical_coverage_pct} % of the critical work. Reallocate the "
           "faulted UAV's share within 90 s of the fault declaration, keep the remaining work safe, and bring "
           "{fault_uav} back to {rtb_zone} without breaching the geofence. Report what was dropped."),
    params=[
        dict(name="fault_uav", type="enum", unit="uav id", required=True, desc="UAV that will fail", example="UAV-2", choices=["UAV-1", "UAV-2", "UAV-3", "UAV-4", "UAV-5"]),
        dict(name="fault_type", type="enum", unit="fault", required=True, desc="Injected fault", example="battery", choices=["battery", "GPS", "motor", "link"]),
        dict(name="fault_at_s", type="int", unit="s", required=True, desc="Fault injection time", example=240, range=[120, 480]),
        dict(name="critical_task", type="zone_ref", unit="zone id", required=True, desc="Mission element that must stay above the coverage floor", example="BLOCK-A1"),
        dict(name="critical_coverage_pct", type="int", unit="%", required=True, desc="Coverage that must be kept after the fault", example=80, range=[70, 90]),
        dict(name="rtb_zone", type="zone_ref", unit="zone id", required=True, desc="Return-to-base landing zone", example="LZ-CHURCH"),
    ],
    preconditions=["mission already running with {critical_task} assigned", "fault injection enabled in the scenario"],
    phases=[
        dict(phase="1-detect", actions=["receive the fault declaration", "confirm the degraded capability"], output="fault statement within 30 s"),
        dict(phase="2-reallocate", actions=["re-split the faulted UAV's remaining work", "declare new assignments"], output="reallocation plan within 90 s"),
        dict(phase="3-continue", actions=["fly the reallocated work", "keep {critical_coverage_pct} % coverage"], output="continued mission"),
        dict(phase="4-recover", actions=["route {fault_uav} to {rtb_zone}", "land it safely"], output="faulted UAV on the ground"),
        dict(phase="5-report", actions=["report dropped work and residual risk", "propose a resume plan"], output="recovery report"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Fault declaration", metric="time from injection to acknowledged fault report", target="<= 30 s", verify="platform event log", weight=20),
        dict(id="CP-2", name="Reallocation", metric="time to publish new assignments and coverage kept afterwards", target="<= 90 s and >= {critical_coverage_pct} % of {critical_task}", verify="platform coverage grid", weight=30),
        dict(id="CP-3", name="Safe return", metric="landing of {fault_uav} at {rtb_zone}, geofence events", target="landed at {rtb_zone}, 0 geofence breaches", verify="platform landing log", weight=20),
        dict(id="CP-4", name="Deconfliction", metric="separation warnings during the replan", target="0 warnings below 30 m", verify="platform proximity monitor", weight=15),
        dict(id="CP-5", name="Recovery report", metric="work dropped and resume plan submitted", target="report contains dropped tasks and a resume plan", verify="platform report channel", weight=15),
    ],
    failures=["fault not reported within 120 s", "faulted UAV crashes or lands outside {rtb_zone}", "critical coverage falls below 60%"],
    constraints=["faulted UAV may not continue work after the declaration", "no new task may be started after t = 600 s"],
    gen=dict(sampling=dict(fault_uav=["UAV-1", "UAV-2", "UAV-3", "UAV-4"], fault_type=["battery", "GPS", "motor", "link"], fault_at_s=[120, 180, 240, 360, 480], critical_coverage_pct=[70, 75, 80, 85, 90]), variants=["single fault", "fault during a handoff"]),
    example=dict(fault_uav="UAV-2", fault_type="battery", fault_at_s=240, critical_task="BLOCK-A1", critical_coverage_pct=80, rtb_zone="LZ-CHURCH"),
))

# ---------------------------------------------------------------- composites
CP = []

CP.append(dict(
    id="CP-A", name="Emergency MEDEVAC Delivery with Escort", category="composite", difficulty=3,
    base=["TT-03", "TT-05"],
    summary="Priority medical payload delivery while a second UAV escorts and clears the corridor.",
    title="Priority MEDEVAC delivery to {drop_zone} with escort",
    objective=("Deliver the {payload_kg} kg medical kit to {drop_zone} inside {deadline_s} s while the escort UAV "
               "screens the corridor and reports any obstruction."),
    brief=("A casualty needs the medical kit at {drop_zone} within {deadline_s} s. Fly the carrier on the shortest "
           "safe route at {cruise_mps} m/s, keep the escort {escort_sep_m} m ahead on the corridor, and release "
           "within {accuracy_m} m of the drop-zone centre from at least {drop_alt_m} m. If the corridor is blocked, "
           "the escort must declare the blocking object before the carrier enters it."),
    params=[
        dict(name="drop_zone", type="zone_ref", required=True, desc="Drop zone", example="DZ-SCHOOL-YARD"),
        dict(name="payload_kg", type="float", unit="kg", required=True, desc="Kit mass", example=1.2, range=[0.5, 2.5]),
        dict(name="deadline_s", type="int", unit="s", required=True, desc="Delivery deadline", example=360, range=[240, 480]),
        dict(name="cruise_mps", type="int", unit="m/s", required=True, desc="Carrier cruise speed", example=14, range=[10, 18]),
        dict(name="escort_sep_m", type="int", unit="m", required=True, desc="Escort lead distance", example=80, range=[50, 120]),
        dict(name="accuracy_m", type="enum", unit="m", required=True, desc="Drop accuracy", example=3, choices=[2, 3, 5]),
        dict(name="drop_alt_m", type="int", unit="m AGL", required=True, desc="Release altitude", example=12, range=[8, 20]),
    ],
    phases=[
        dict(phase="1-plan", actions=["assign carrier and escort", "declare the corridor to {drop_zone}"], output="role plan"),
        dict(phase="2-escort", actions=["escort flies {escort_sep_m} m ahead", "declares obstructions"], output="cleared corridor"),
        dict(phase="3-deliver", actions=["release at {drop_alt_m} m within {accuracy_m} m", "confirm delivery"], output="kit on the ground"),
        dict(phase="4-recover", actions=["both UAVs recover to the nearest landing zone"], output="recovered fleet"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Role separation", metric="escort lead distance and carrier separation", target="escort leads by {escort_sep_m} +/- 20 m; carrier-escort >= 40 m", verify="platform formation monitor", weight=20),
        dict(id="CP-2", name="Delivery accuracy", metric="impact distance to {drop_zone} centre and impact speed", target="<= {accuracy_m} m and < 3 m/s", verify="platform impact record", weight=30),
        dict(id="CP-3", name="Deadline", metric="release time", target="<= {deadline_s} s", verify="platform event log", weight=20),
        dict(id="CP-4", name="Obstruction reporting", metric="blocking-object reports from the escort before carrier entry", target="1 report per blocking object, no carrier entry before the report", verify="platform message log", weight=15),
        dict(id="CP-5", name="Recovery", metric="both UAVs landed", target="both on ground, no hard landing", verify="platform landing log", weight=15),
    ],
    failures=["kit released above 25 m", "delivery after the deadline", "carrier enters a corridor section the escort has not cleared"],
    constraints=["escort never loses sight of the carrier", "no release over people-dense areas"],
    gen=dict(sampling=dict(deadline_s=[240, 300, 360, 480], cruise_mps=[10, 12, 14, 18]), variants=["day", "rain", "night"]),
))

CP.append(dict(
    id="CP-B", name="Multi-zone Inspection under Radio Silence", category="composite", difficulty=4,
    base=["TT-02", "TT-04"],
    summary="Inspect several points while the team flies the whole segment in EMCON and reports at the end.",
    title="Inspection of {poi_a} and {poi_b} under EMCON",
    objective=("Inspect {poi_a} and {poi_b} in one sortie with no radio transmission between {emcon_start_s} s and "
               "{emcon_end_s} s, then upload all images when the link is allowed again."),
    brief=("Inspect {poi_a} and then {poi_b} in a single sortie. From t = {emcon_start_s} s to t = "
           "{emcon_end_s} s the team must fly in complete radio silence: no telemetry, no messages, no updates. "
           "Agree on the timeline before takeoff, because during EMCON nobody can re-plan. After EMCON the team has "
           "{upload_window_s} s to upload all images and confirm the completed angles."),
    params=[
        dict(name="poi_a", type="zone_ref", required=True, desc="First point of interest", example="POI-HOSPITAL-ROOF"),
        dict(name="poi_b", type="zone_ref", required=True, desc="Second point of interest", example="POI-SUBSTATION"),
        dict(name="emcon_start_s", type="int", unit="s", required=True, desc="Start of radio silence", example=120, range=[60, 180]),
        dict(name="emcon_end_s", type="int", unit="s", required=True, desc="End of radio silence", example=480, range=[360, 600]),
        dict(name="upload_window_s", type="int", unit="s", required=True, desc="Time to upload after EMCON", example=120, range=[60, 180]),
        dict(name="images", type="int", unit="images/angle", required=True, desc="Images per angle", example=3, range=[3, 5]),
        dict(name="standoff_m", type="int", unit="m", required=True, desc="Standoff for inspection", example=15, range=[8, 25]),
    ],
    phases=[
        dict(phase="1-brief", actions=["agree on the timeboxed route before takeoff", "fix the capture order"], output="frozen plan"),
        dict(phase="2-inspect", actions=["capture {images} images per angle at {poi_a} then {poi_b}", "record everything onboard"], output="image set in onboard storage"),
        dict(phase="3-emcon", actions=["transmit nothing between {emcon_start_s} and {emcon_end_s} s"], output="zero transmissions"),
        dict(phase="4-upload", actions=["upload all images within {upload_window_s} s", "confirm the angle coverage"], output="verified inspection"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Radio silence", metric="transmissions during the EMCON window", target="0 transmitted packets between {emcon_start_s} and {emcon_end_s} s", verify="platform link log", weight=25),
        dict(id="CP-2", name="Inspection completeness", metric="angles and images captured at {poi_a} and {poi_b}", target=">= {images} images for >= 3 angles at both points", verify="payload log after upload", weight=30),
        dict(id="CP-3", name="Standoff", metric="distance during capture", target="{standoff_m} +/- 3 m", verify="platform range telemetry", weight=20),
        dict(id="CP-4", name="Upload window", metric="time from EMCON end to complete upload", target="<= {upload_window_s} s", verify="platform data log", weight=15),
        dict(id="CP-5", name="Recovery", metric="all UAVs landed", target="all on ground at the nearest landing zone", verify="platform landing log", weight=10),
    ],
    failures=["any transmission inside the EMCON window", "an inspection angle missed with no time to recover", "images lost on landing"],
    constraints=["EMCON is absolute: no ping, no heartbeat, no re-plan", "single sortie"],
    gen=dict(sampling=dict(emcon_start_s=[60, 120, 180], emcon_end_s=[360, 480, 600], upload_window_s=[60, 90, 120, 180]), variants=["day", "night"]),
))

CP.append(dict(
    id="CP-C", name="Dynamic Re-tasking after Wind Shift", category="composite", difficulty=4,
    base=["TT-01"],
    summary="A gust front invalidates the survey plan; the team must re-plan coverage without losing time.",
    title="Re-tasked survey of {area} after a wind shift at t={shift_at_s} s",
    objective=("Complete a {coverage_pct} % survey of {area} although the wind shifts by {shift_deg} deg at t = "
               "{shift_at_s} s, forcing the tracks to be re-planned with a new heading and spacing."),
    brief=("You are surveying {area} to {coverage_pct} % at {altitude_m} m. At about t = {shift_at_s} s the wind "
           "shifts by {shift_deg} deg and gusts to {gust_mps} m/s. Continuing on the old track heading will leave "
           "gaps, so re-plan the tracks and redistribute the strips. Coverage accuracy still matters more than "
           "finishing early."),
    params=[
        dict(name="area", type="zone_ref", required=True, desc="Survey block", example="BLOCK-A3"),
        dict(name="coverage_pct", type="int", unit="%", required=True, desc="Coverage target", example=85, range=[80, 95]),
        dict(name="altitude_m", type="int", unit="m AGL", required=True, desc="Survey altitude", example=60, range=[40, 80]),
        dict(name="shift_at_s", type="int", unit="s", required=True, desc="Time of the wind shift", example=240, range=[120, 420]),
        dict(name="shift_deg", type="int", unit="deg", required=True, desc="Wind direction change", example=40, range=[20, 70]),
        dict(name="gust_mps", type="int", unit="m/s", required=True, desc="Gust speed after the shift", example=11, range=[8, 15]),
    ],
    phases=[
        dict(phase="1-survey", actions=["start the baseline survey", "report coverage per strip"], output="partial coverage"),
        dict(phase="2-detect-shift", actions=["notice the wind shift at {shift_at_s} s", "declare the affected strips"], output="impact statement"),
        dict(phase="3-replan", actions=["rotate the track heading by the new wind angle", "re-split the remaining area"], output="re-plan"),
        dict(phase="4-complete", actions=["fly the new tracks", "reach {coverage_pct} %"], output="complete coverage"),
        dict(phase="5-report", actions=["report the re-plan cost in time and energy"], output="re-plan report"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Shift detection", metric="time from the shift to the impact statement", target="<= 45 s", verify="platform event log", weight=20),
        dict(id="CP-2", name="Re-plan quality", metric="overlap between old and new track sets and duplicate coverage", target="duplicate coverage <= 15% after the shift", verify="platform coverage grid", weight=20),
        dict(id="CP-3", name="Final coverage", metric="union coverage of {area}", target=">= {coverage_pct} %", verify="platform coverage grid", weight=35),
        dict(id="CP-4", name="Airspace integrity", metric="geofence and no-fly-zone events", target="0 events", verify="platform geofence monitor", weight=15),
        dict(id="CP-5", name="Report", metric="re-plan report submitted", target="report contains the new heading, spacing and time cost", verify="platform report channel", weight=10),
    ],
    failures=["coverage below 70% at task end", "geofence breach while compensating for the wind", "no re-plan after the shift"],
    constraints=["new track heading within +/- 60 deg of the wind-perpendicular", "no re-entry into already covered strips except for overlap control"],
    gen=dict(sampling=dict(shift_at_s=[120, 240, 360, 420], shift_deg=[20, 30, 40, 70], gust_mps=[8, 10, 11, 15]), variants=["day", "dust"]),
))

CP.append(dict(
    id="CP-D", name="Landing Zone Conflict Resolution", category="composite", difficulty=3,
    base=["TT-03", "TT-10"],
    summary="The assigned landing zone is occupied on arrival; the team must negotiate an alternative and land safely.",
    title="Landing zone conflict at {lz} with {n_waiting} UAVs waiting",
    objective=("Land the whole team after the mission although {lz} is occupied by {n_waiting} UAVs, using an "
               "alternative landing zone and a landing sequence that keeps separation."),
    brief=("The mission is over and the team must land. On arrival {lz} is occupied by {n_waiting} UAVs that are "
           "not leaving in the next {hold_s} s. Choose an alternative landing zone, declare a landing sequence, and "
           "keep at least {land_sep_m} m between UAVs at touchdown. Do not hover below {min_alt_m} m while "
           "waiting."),
    params=[
        dict(name="lz", type="zone_ref", required=True, desc="Occupied landing zone", example="LZ-CHURCH"),
        dict(name="alt_lz", type="zone_ref", required=True, desc="Alternative landing zone", example="LZ-RIVER-PARK"),
        dict(name="n_waiting", type="int", unit="UAVs", required=True, desc="UAVs occupying the primary landing zone", example=2, range=[1, 3]),
        dict(name="hold_s", type="int", unit="s", required=True, desc="Estimated occupation time", example=90, range=[60, 180]),
        dict(name="land_sep_m", type="int", unit="m", required=True, desc="Required touchdown separation", example=8, range=[5, 15]),
        dict(name="min_alt_m", type="int", unit="m AGL", required=True, desc="Minimum holding altitude", example=30, range=[20, 50]),
    ],
    phases=[
        dict(phase="1-detect", actions=["notice that the primary landing zone is occupied", "hold at {min_alt_m} m"], output="conflict report"),
        dict(phase="2-decide", actions=["choose {alt_lz}", "declare the landing sequence"], output="landing plan"),
        dict(phase="3-land", actions=["land in sequence with {land_sep_m} m separation"], output="all UAVs on the ground"),
        dict(phase="4-report", actions=["report the impact on the mission timeline"], output="closure report"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Conflict declaration", metric="time from arrival to the conflict report", target="<= 30 s", verify="platform event log", weight=20),
        dict(id="CP-2", name="Alternative zone", metric="landing zone actually used and occupancy", target="all UAVs land at {alt_lz} with >= {land_sep_m} m separation", verify="platform landing log", weight=35),
        dict(id="CP-3", name="Holding altitude", metric="altitude while waiting", target="never below {min_alt_m} m before the landing phase", verify="platform altitude log", weight=20),
        dict(id="CP-4", name="Sequence discipline", metric="landing order violations and go-arounds", target="landing order respected, 0 go-arounds", verify="platform landing log", weight=15),
        dict(id="CP-5", name="Closure report", metric="timeline impact reported", target="report submitted with the delay in seconds", verify="platform report channel", weight=10),
    ],
    failures=["touchdown without the declared separation", "any UAV below {min_alt_m} m before the landing phase"],
    constraints=["no landing at the occupied zone before the waiting UAVs leave", "one landing at a time"],
    gen=dict(sampling=dict(n_waiting=[1, 2, 3], hold_s=[60, 90, 180]), variants=["day", "dusk"]),
))

CP.append(dict(
    id="CP-E", name="Survey Quality Audit", category="composite", difficulty=3,
    base=["TT-01"],
    summary="Re-fly a sample of an earlier survey and decide whether the block has to be surveyed again.",
    title="Quality audit of the earlier {area} survey",
    objective=("Re-fly {audit_pct} % of {area} and compare it with the earlier survey to decide whether the block "
               "meets the {coverage_pct} % coverage claim, reporting any gap wider than {gap_m} m."),
    brief=("{area} was already surveyed once. As the audit team, re-fly {audit_pct} % of the block with a "
           "{sweep_width_m} m track spacing at {altitude_m} m, compare with the stored survey, and report every gap "
           "wider than {gap_m} m. If the audit shows the claim is wrong, state the corrected coverage number and "
           "what has to be re-flown."),
    params=[
        dict(name="area", type="zone_ref", required=True, desc="Block to audit", example="BLOCK-A2"),
        dict(name="audit_pct", type="enum", unit="%", required=True, desc="Fraction of the block re-flown", example=25, choices=[20, 25, 30]),
        dict(name="coverage_pct", type="int", unit="%", required=True, desc="Coverage claimed by the earlier survey", example=90, range=[85, 95]),
        dict(name="sweep_width_m", type="int", unit="m", required=True, desc="Audit track spacing", example=80, range=[60, 120]),
        dict(name="gap_m", type="int", unit="m", required=True, desc="Smallest gap that must be reported", example=30, range=[20, 60]),
        dict(name="altitude_m", type="int", unit="m AGL", required=True, desc="Audit altitude", example=70, range=[40, 90]),
    ],
    phases=[
        dict(phase="1-plan", actions=["choose the audited sample of {audit_pct} %", "declare the audit tracks"], output="audit plan"),
        dict(phase="2-fly", actions=["fly the audit tracks at {altitude_m} m", "record the coverage raster"], output="audit raster"),
        dict(phase="3-compare", actions=["overlay the audit on the stored survey", "list gaps wider than {gap_m} m"], output="gap list"),
        dict(phase="4-decide", actions=["state whether {coverage_pct} % holds", "define the re-fly set"], output="audit verdict"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Audit sample", metric="fraction of the block actually re-flown", target=">= {audit_pct} %", verify="platform coverage grid", weight=25),
        dict(id="CP-2", name="Gap reporting", metric="gaps wider than {gap_m} m reported", target="all gaps found and reported", verify="platform report comparison", weight=30),
        dict(id="CP-3", name="Verdict accuracy", metric="corrected coverage statement versus ground truth", target="corrected coverage within 5 percentage points", verify="platform ground truth", weight=25),
        dict(id="CP-4", name="Airspace integrity", metric="geofence and no-fly-zone events", target="0 events", verify="platform geofence monitor", weight=10),
        dict(id="CP-5", name="Re-fly definition", metric="re-fly set defined", target="re-fly set submitted with strip ids", verify="platform report channel", weight=10),
    ],
    failures=["audit plan identical to the original tracks", "verdict without a corrected coverage number"],
    constraints=["audit tracks must not simply repeat the original track headings", "no re-fly of the whole block"],
    gen=dict(sampling=dict(audit_pct=[20, 25, 30], sweep_width_m=[60, 80, 120], gap_m=[20, 30, 60]), variants=["day", "haze"]),
))

CP.append(dict(
    id="CP-F", name="Canyon Transit with Relay", category="composite", difficulty=5,
    base=["TT-04", "TT-07"],
    summary="Move a team through a canyon with no line of sight to base while keeping a relay alive above the rim.",
    title="Canyon transit of {corridor} with one relay above the rim",
    objective=("Transit {corridor} with {transit_uavs} UAVs while one relay stays above the rim so the base keeps "
               "a link at RSSI >= {min_rssi_dbm} dBm for the whole transit."),
    brief=("Fly {transit_uavs} UAVs through {corridor} in a {formation} formation. The canyon walls block the link "
           "to base, so one UAV must stay above the rim at {relay_alt_m} m as a relay for the whole transit and "
           "keep the link at {min_rssi_dbm} dBm or better. If the relay drops below the required link quality, the "
           "transit must pause until it is restored."),
    params=[
        dict(name="corridor", type="zone_ref", required=True, desc="Canyon corridor", example="CORRIDOR-URBAN-1"),
        dict(name="transit_uavs", type="int", unit="UAVs", required=True, desc="UAVs flying the corridor", example=2, range=[2, 3]),
        dict(name="formation", type="enum", unit="shape", required=True, desc="Transit formation", example="column", choices=["line", "column", "V"]),
        dict(name="relay_alt_m", type="int", unit="m AGL", required=True, desc="Relay altitude above the rim", example=140, range=[100, 180]),
        dict(name="min_rssi_dbm", type="int", unit="dBm", required=True, desc="Link floor during transit", example=-88, range=[-95, -80]),
        dict(name="transit_budget_s", type="int", unit="s", required=True, desc="Transit time budget", example=420, range=[300, 600]),
    ],
    phases=[
        dict(phase="1-plan", actions=["assign the relay UAV and its orbit above the rim", "declare the transit formation"], output="relay and transit plan"),
        dict(phase="2-relay", actions=["relay climbs to {relay_alt_m} m and declares link up"], output="link established"),
        dict(phase="3-transit", actions=["transit UAVs enter {corridor} in {formation}", "pause if the link drops"], output="fleet at the exit"),
        dict(phase="4-recover", actions=["relay hands over and lands", "transit UAVs land"], output="recovered fleet"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Relay link", metric="fraction of the transit with RSSI >= {min_rssi_dbm} dBm", target=">= 97%", verify="platform link log", weight=30),
        dict(id="CP-2", name="Transit completion", metric="time to move all transit UAVs through {corridor}", target="<= {transit_budget_s} s", verify="platform event log", weight=25),
        dict(id="CP-3", name="Formation integrity", metric="formation error inside the corridor", target="mean <= 5 m", verify="platform formation monitor", weight=20),
        dict(id="CP-4", name="Pause discipline", metric="transits continued while the link was below the floor", target="0 corridor metres flown below the link floor", verify="platform link and position log", weight=15),
        dict(id="CP-5", name="Recovery", metric="all UAVs landed", target="all on ground, no hard landing", verify="platform landing log", weight=10),
    ],
    failures=["relay leaves its orbit during the transit", "transit continues with no link for more than 20 s", "obstacle strike in the corridor"],
    constraints=["relay orbit radius <= 200 m", "transit speed <= 8 m/s inside the canyon"],
    gen=dict(sampling=dict(transit_uavs=[2, 3], relay_alt_m=[100, 120, 140, 180], min_rssi_dbm=[-95, -90, -88, -80]), variants=["day", "dusk", "dust"]),
))

CP.append(dict(
    id="CP-G", name="Night Thermal Priority Survey", category="composite", difficulty=3,
    base=["TT-01", "TT-09"],
    summary="A thermal-only survey of a priority block at night with a hard sensor and altitude constraint.",
    title="Night thermal survey of {area} ({coverage_pct}% target, no RGB)",
    objective=("Survey {area} at night with the thermal sensor only at {altitude_m} m, reaching {coverage_pct} % "
               "coverage and flagging every hotspot above {hotspot_c} degC."),
    brief=("It is night and only the thermal payload works. Survey {area} at {altitude_m} m to {coverage_pct} % "
           "coverage, and report every thermal hotspot above {hotspot_c} degC with its position and peak "
           "temperature. The RGB camera must not be used, and no illumination is allowed. Re-visit any hotspot "
           "once to confirm it before reporting."),
    params=[
        dict(name="area", type="zone_ref", required=True, desc="Priority block", example="BLOCK-A4"),
        dict(name="coverage_pct", type="int", unit="%", required=True, desc="Coverage target", example=90, range=[85, 95]),
        dict(name="altitude_m", type="int", unit="m AGL", required=True, desc="Thermal survey altitude", example=80, range=[60, 100]),
        dict(name="hotspot_c", type="int", unit="degC", required=True, desc="Hotspot threshold", example=120, range=[80, 200]),
        dict(name="confirm_revisit", type="bool", unit="flag", required=True, desc="Require a confirmation pass per hotspot", example=True),
    ],
    phases=[
        dict(phase="1-plan", actions=["plan thermal tracks at {altitude_m} m", "declare the hotspot threshold"], output="thermal survey plan"),
        dict(phase="2-survey", actions=["fly the tracks with the thermal payload", "record the thermal mosaic"], output="thermal raster"),
        dict(phase="3-confirm", actions=["re-visit each hotspot once", "record peak temperature"], output="confirmed hotspot list"),
        dict(phase="4-report", actions=["report positions and peaks", "recover"], output="hotspot report"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Coverage", metric="thermal union coverage of {area}", target=">= {coverage_pct} %", verify="platform coverage grid", weight=30),
        dict(id="CP-2", name="Sensor discipline", metric="RGB frames taken during the survey", target="0 RGB frames", verify="payload log", weight=20),
        dict(id="CP-3", name="Hotspot detection", metric="hotspots found above {hotspot_c} degC versus ground truth", target=">= 90% of true hotspots, <= 1 false positive", verify="payload and ground truth comparison", weight=25),
        dict(id="CP-4", name="Confirmation pass", metric="re-visits per hotspot", target=">= 1 re-visit per reported hotspot", verify="payload log", weight=15),
        dict(id="CP-5", name="Recovery", metric="all UAVs landed", target="all on ground at the nearest landing zone", verify="platform landing log", weight=10),
    ],
    failures=["RGB used during the survey", "no illumination allowed: any searchlight event", "coverage below 75%"],
    constraints=["no illumination", "thermal payload is the only sensor", "altitude >= 60 m"],
    gen=dict(sampling=dict(coverage_pct=[85, 90, 95], altitude_m=[60, 80, 100], hotspot_c=[80, 120, 200]), variants=["clear night", "cold night"]),
))

CP.append(dict(
    id="CP-H", name="Partial Fleet Loss Mission Continuity", category="composite", difficulty=5,
    base=["TT-10"],
    summary="Two UAVs are lost at once; the surviving team must keep the mission-critical core and report the rest.",
    title="Mission continuity after losing {lost_uavs} at t={loss_at_s} s",
    objective=("Keep the mission-critical core of {critical_task} running after {lost_uavs} are lost at t = "
               "{loss_at_s} s, delivering at least {keep_pct} % of the original plan with the surviving UAVs."),
    brief=("At about t = {loss_at_s} s, {lost_uavs} are lost (link and telemetry gone). The mission still needs at "
           "least {keep_pct} % of the {critical_task} work done. Re-plan the surviving fleet, protect the critical "
           "core of the plan, and declare what is dropped and why. Do not launch a search for the lost UAVs."),
    params=[
        dict(name="lost_uavs", type="enum", unit="uav list", required=True, desc="UAVs that disappear", example="UAV-3 and UAV-4", choices=["UAV-2 and UAV-4", "UAV-3 and UAV-4", "UAV-1 and UAV-3", "UAV-2 and UAV-5"]),
        dict(name="loss_at_s", type="int", unit="s", required=True, desc="Loss time", example=300, range=[180, 600]),
        dict(name="critical_task", type="zone_ref", required=True, desc="Mission-critical element", example="BLOCK-A1"),
        dict(name="keep_pct", type="int", unit="%", required=True, desc="Fraction of the plan that must still be delivered", example=60, range=[50, 80]),
    ],
    phases=[
        dict(phase="1-detect", actions=["detect the loss of {lost_uavs}", "declare it to the team"], output="loss statement"),
        dict(phase="2-replan", actions=["re-rank the remaining work", "re-split it across survivors"], output="continuity plan"),
        dict(phase="3-execute", actions=["execute the critical core", "report progress every 2 min"], output="delivered work"),
        dict(phase="4-report", actions=["report dropped work and residual risk"], output="continuity report"),
    ],
    checkpoints=[
        dict(id="CP-1", name="Loss declaration", metric="time from loss to acknowledged statement", target="<= 45 s", verify="platform event log", weight=20),
        dict(id="CP-2", name="Continuity", metric="fraction of the original plan delivered", target=">= {keep_pct} %", verify="platform coverage grid", weight=30),
        dict(id="CP-3", name="Critical core", metric="coverage of {critical_task} after the loss", target=">= 70% of {critical_task}", verify="platform coverage grid", weight=20),
        dict(id="CP-4", name="No futile search", metric="time spent searching for the lost UAVs", target="<= 30 s", verify="platform route log", weight=15),
        dict(id="CP-5", name="Report", metric="continuity report submitted", target="report lists dropped tasks with reasons", verify="platform report channel", weight=15),
    ],
    failures=["abandoning the critical core", "searching for lost UAVs", "no statement within 180 s"],
    constraints=["surviving UAVs may not leave the geofence", "no new tasks after t = 900 s"],
    gen=dict(sampling=dict(loss_at_s=[180, 300, 420, 600], keep_pct=[50, 60, 70, 80]), variants=["single event", "loss during handoff"]),
))
