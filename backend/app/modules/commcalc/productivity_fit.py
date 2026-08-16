"""Pure talent-to-demand fit + POSITIVE recommendations for the Productivity lens. No DB / FastAPI.

The endpoint gathers, per rep at a store: output/hour (from the productivity module) and scheduled
hours-by-hour (from shifts), plus the store's demand-by-hour curve (trans_ts). This turns that into a
'are your best people on your busiest hours?' view and coaching/scheduling suggestions.

Human-in-the-loop by design: it RECOMMENDS (coach, reschedule, recognize) — it never takes an action or
suggests discipline. The manager decides.
"""


def peak_hours(demand_by_hour, top_frac=0.34):
    """The set of hours carrying the top `top_frac` of the store's demand (busiest ~third by default).
    Empty when there's no demand yet — the caller can pass a staffing proxy instead."""
    items = [(h, float(v or 0)) for h, v in (demand_by_hour or {}).items() if float(v or 0) > 0]
    if not items:
        return set()
    items.sort(key=lambda x: -x[1])
    total = sum(v for _, v in items)
    acc, peak = 0.0, set()
    for h, v in items:
        peak.add(h)
        acc += v
        if acc >= total * top_frac:
            break
    return peak


def analyze(reps, peak, min_hours=8.0):
    """reps: [{name, output_per_hour (float|None), hours_by_hour: {hour:int -> hours:float}}].
    peak: set of peak hours. Returns per-rep fit (ranked by output/hr) + ranked positive recommendations."""
    rows = []
    for r in reps:
        hbh = r.get("hours_by_hour") or {}
        total_h = sum(float(v or 0) for v in hbh.values())
        peak_h = sum(float(v or 0) for h, v in hbh.items() if h in peak)
        share = (peak_h / total_h) if total_h > 0 else None
        rows.append({"name": r.get("name"), "output_per_hour": r.get("output_per_hour"),
                     "hours": round(total_h, 1), "peak_hours": round(peak_h, 1),
                     "peak_share": None if share is None else round(share, 2)})
    ranked = sorted(rows, key=lambda x: (x["output_per_hour"] is None, -(x["output_per_hour"] or 0.0)))
    for i, r in enumerate(ranked):
        r["output_rank"] = i + 1
    n = len(ranked)
    recs = []
    if n and ranked[0].get("output_per_hour"):
        b = ranked[0]
        recs.append({"kind": "recognize", "rep": b["name"],
                     "text": f"{b['name']} leads output/hour — recognize them, and consider making them a peak-hour anchor or mentor."})
    if n >= 3 and peak:
        top_cut = max(1, n // 3)
        bot_cut = (2 * n) // 3
        for r in ranked:
            if r["peak_share"] is None or (r["hours"] or 0) < min_hours:
                continue
            if r["output_rank"] <= top_cut and r["peak_share"] < 0.5:
                recs.append({"kind": "schedule", "rep": r["name"],
                             "text": f"{r['name']} is a top performer (#{r['output_rank']} output/hr) but only {int(r['peak_share'] * 100)}% of their hours fall in peak demand — shift more of their time into the busy hours to lift sales."})
            elif r["output_rank"] > bot_cut and r["peak_share"] >= 0.6:
                recs.append({"kind": "coach", "rep": r["name"],
                             "text": f"{r['name']} works mostly peak hours ({int(r['peak_share'] * 100)}%) but ranks low on output/hr — a coaching or pairing opportunity where it matters most."})
    return {"reps": ranked, "recommendations": recs, "peak_hours": sorted(peak)}


if __name__ == "__main__":
    demand = {9: 2, 10: 3, 12: 20, 13: 18, 14: 15, 18: 5}   # midday peak
    pk = peak_hours(demand)
    assert 12 in pk and 13 in pk and 9 not in pk, pk
    reps = [
        {"name": "Ana", "output_per_hour": 40.0, "hours_by_hour": {9: 6, 10: 6, 11: 6}},   # top, all off-peak
        {"name": "Ben", "output_per_hour": 20.0, "hours_by_hour": {12: 8, 13: 8}},          # mid, peak
        {"name": "Cal", "output_per_hour": 8.0, "hours_by_hour": {12: 9, 13: 9}},           # low, all peak
    ]
    res = analyze(reps, pk)
    assert res["reps"][0]["name"] == "Ana" and res["reps"][0]["output_rank"] == 1
    kinds = {r["kind"]: r for r in res["recommendations"]}
    assert "recognize" in kinds and kinds["recognize"]["rep"] == "Ana"
    assert "schedule" in kinds and kinds["schedule"]["rep"] == "Ana", kinds   # top perf, off-peak
    assert "coach" in kinds and kinds["coach"]["rep"] == "Cal", kinds         # low perf, all peak
    print("productivity_fit self-test OK:", {k: v["rep"] for k, v in kinds.items()})
