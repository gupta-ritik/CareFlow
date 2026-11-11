"""Deterministic statistics (the analyst's numbers), the facts table claims cite, the critic, and the rule-based writer.

A finding needs at least MIN_CASES cases on each side and p < 0.05 (Mann-Whitney U for time measures, Fisher's exact
test for shares); otherwise it is labelled "not enough evidence" (too few cases) or "no clear difference".
"""
from __future__ import annotations

import re

from scipy import stats as sps

from ..redaction import redact
from .measures import DIMENSIONS, REGISTRY

MIN_CASES = 10
ALPHA = 0.05
NO_CAUSE = "CareFlow's data doesn't show why."


# ---- statistics ---------------------------------------------------------------------------------------------------
def _n(s: dict) -> int:
    return s["n"] if isinstance(s.get("n"), int) else 0


def _value(kind: str, s: dict):
    return s.get("median") if kind == "time" else s.get("rate") if kind == "rate" else s.get("n")


def compare(kind: str, focus: dict, base: dict) -> dict:
    nf, nb = _n(focus), _n(base)
    out = {"n_focus": nf, "n_baseline": nb, "p_value": None, "test": None}
    if focus.get("suppressed") or base.get("suppressed") or nf < MIN_CASES or nb < MIN_CASES:
        out["label"] = "not enough evidence"
        return out
    vf, vb = _value(kind, focus), _value(kind, base)
    if kind == "time":
        p = float(sps.mannwhitneyu(focus["_samples"], base["_samples"], alternative="two-sided").pvalue)
        out["test"] = "Mann-Whitney U"
    elif kind == "rate":
        hf, hb = focus["hits"], base["hits"]
        p = float(sps.fisher_exact([[hf, nf - hf], [hb, nb - hb]]).pvalue)
        out["test"] = "Fisher exact"
    else:
        out["label"] = "descriptive"
        out.update(diff=vf - vb, ratio=round(vf / vb, 2) if vb else None)
        return out
    out["p_value"] = round(p, 4)
    out["diff"] = round(vf - vb, 3 if kind == "rate" else 0) if vf is not None and vb is not None else None
    out["ratio"] = round(vf / vb, 2) if vb else None
    out["direction"] = "higher" if (vf or 0) > (vb or 0) else "lower"
    out["label"] = "finding" if p < ALPHA else "no clear difference"
    return out


def _group_name(key: dict) -> str:
    parts = []
    for d, v in key.items():
        if d == "ward":
            parts.append(f"Ward {v}")
        elif d == "weekday":
            parts.append({"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
                          "sat": "Saturday", "sun": "Sunday"}[v])
        elif d == "hour":
            parts.append(f"{int(v):02d}:00")
        else:
            parts.append(str(v).replace("_", " "))
    return ", ".join(parts)


def _merge(kind: str, groups: list[dict]) -> dict:
    if kind == "time":
        samples = [x for g in groups for x in g.get("_samples", [])]
        from .measures import stats_of
        return {**stats_of(samples), "_samples": samples}
    n = sum(_n(g) for g in groups)
    hits = sum(g.get("hits", 0) for g in groups)
    return {"n": n, "hits": hits, "rate": round(hits / n, 3) if n else None}


def analyse(result: dict) -> list[dict]:
    """Findings for one result. Each finding cites the result id and only numbers from its facts."""
    kind, rid = result["kind"], result["id"]
    params = result["params"]
    cmp = params.get("compare") or {}
    findings = []
    if "focus" in result and "baseline" in result:
        c = compare(kind, result["focus"], result["baseline"])
        findings.append({"result_id": rid, "type": "comparison", "focus_label": cmp.get("focus_label") or "the focus",
                         "baseline_label": cmp.get("baseline_label") or "the baseline", **c})
        # With a split (e.g. by department) and a period comparison: which group changed most, and is it significant?
        if result.get("groups") and result.get("baseline_groups") and c["label"] != "not enough evidence":
            base = {tuple(g["key"].items()): g for g in result["baseline_groups"]}
            best = None
            for g in result["groups"]:
                b = base.get(tuple(g["key"].items()))
                if not b or g.get("suppressed") or b.get("suppressed"):
                    continue
                gc = compare(kind, g, b)
                vf, vb = _value(kind, g), _value(kind, b)
                if gc["label"] == "finding" and gc.get("direction") == "higher" and vf is not None and vb is not None:
                    if best is None or vf - vb > best[2]:
                        best = (g, gc, vf - vb)
            if best:
                g, gc, _ = best
                findings.append({"result_id": rid, "type": "group_change", "group": g["key"], "group_label": _group_name(g["key"]),
                                 "focus_label": cmp.get("focus_label"), "baseline_label": cmp.get("baseline_label"), **gc})
        return findings
    if result.get("groups") and params.get("group_by") and params["group_by"] != ["day"]:
        ok = [g for g in result["groups"] if not g.get("suppressed") and _n(g) >= MIN_CASES and _value(kind, g) is not None]
        if len(ok) >= 2:
            top = (max if params.get("rank", "highest") == "highest" else min)(ok, key=lambda g: _value(kind, g))
            rest = _merge(kind, [g for g in result["groups"] if g is not top and not g.get("suppressed")])
            c = compare(kind, top, rest) if kind != "count" else {"label": "descriptive", "n_focus": _n(top), "n_baseline": _n(rest)}
            findings.append({"result_id": rid, "type": "ranking", "group": top["key"], "group_label": _group_name(top["key"]),
                             "rank": params.get("rank", "highest"), **c})
        else:
            findings.append({"result_id": rid, "type": "ranking", "label": "not enough evidence", "group": None})
        return findings
    findings.append({"result_id": rid, "type": "describe", "label": "descriptive", "n_focus": _n(result["total"])})
    return findings


# ---- facts: the only numbers a sentence may use -------------------------------------------------------------------
def _key_str(key: dict) -> str:
    return ",".join(f"{d}={v}" for d, v in key.items())


def facts(result: dict, findings: list[dict]) -> dict[str, float]:
    f: dict[str, float] = {"min_cases": MIN_CASES, "min_group": 5}

    def put(prefix: str, s: dict):
        if s.get("suppressed"):
            return
        for k in ("n", "median", "p90", "mean", "rate", "pct", "hits", "patients"):
            if isinstance(s.get(k), (int, float)) and not isinstance(s.get(k), bool):
                f[f"{prefix}.{k}"] = s[k]

    put("total", result["total"])
    for side in ("focus", "baseline"):
        if side in result:
            put(side, result[side])
    for g in result.get("groups", []):
        put(f"group[{_key_str(g['key'])}]", g)
    for g in result.get("baseline_groups", []):
        put(f"baseline_group[{_key_str(g['key'])}]", g)
    for fi in findings:
        for k in ("diff", "ratio", "p_value", "n_focus", "n_baseline"):
            if isinstance(fi.get(k), (int, float)):
                name = k if fi["type"] == "comparison" else f"{fi['type']}.{k}"
                f[name] = fi[k]
        if fi["type"] == "comparison" and isinstance(fi.get("diff"), (int, float)) and result["kind"] == "rate":
            f["diff_pct_points"] = round(fi["diff"] * 100, 1)
    return f


# ---- the critic --------------------------------------------------------------------------------------------------
_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?:\.\d+)?(?![\w])")
_CAUSE = re.compile(r"\b(staff(ing)?|short[- ]staffed|understaffed|roster|shifts?|holiday|festival|weather|rain|strike|absent|"
                    r"on leave|sick|lazy|morale|traffic|careless|negligen\w*)\b", re.I)
_MEDICAL = re.compile(r"\b(diagnos\w*|prescri\w*|dosage|dose|treatment|therapy|symptoms?|should take|medically)\b", re.I)
_TREND = re.compile(r"\b(longer|slower|shorter|faster|quicker|higher|lower|increased?|decreased?|rose|fell|spiked?|trend(ed|ing)?)\b", re.I)
_IDENTITY = re.compile(r"\bsynthetic patient \d+|\bSYN-\d+|\bMRN\b|\[PATIENT\]|\[MRN\]|\[PHONE\]|\[EMAIL\]", re.I)


def _close(x: float, v: float) -> bool:
    return abs(x - v) <= max(0.51, 0.02 * abs(v)) if abs(v) >= 1 else abs(x - v) <= 0.006


def number_ok(x: float, values: list[float]) -> bool:
    for v in values:
        if _close(x, v) or _close(x, v * 100) or (abs(v) >= 60 and abs(x - v / 60) <= 0.15):
            return True
    return False


def context_numbers(question: str, results: dict[str, dict]) -> list[float]:
    """Numbers a sentence may use without a claim: those in the question and in the cited periods and filters."""
    out = [float(n.replace(",", "")) for n in _NUM.findall(question)]
    for r in results.values():
        p = r["params"]
        periods_ = [p["period"]] + ([p["compare"]["period"]] if p.get("compare") and p["compare"].get("period") else [])
        for per in periods_:
            for key in ("start", "end"):
                y, m, d = per[key].split("-")
                out += [float(y), float(m), float(d)]
            out += [float(n) for n in _NUM.findall(per.get("label", ""))]
        for vals in [*p.get("filters", {}).values(), *((p.get("compare") or {}).get("focus", {}) or {}).values()]:
            out += [float(v) for v in vals if str(v).isdigit()]
            out += [float(x) for v in vals for x in re.findall(r"\d+", str(v))]
        out += [float(x) for x in re.findall(r"\d+", (p.get("compare") or {}).get("focus_label", "") or "")]
    return out


def check_sentence(sentence: dict, results: dict[str, dict], fact_tables: dict[str, dict], allowed: list[float], thin=()) -> str | None:
    """None if the sentence is supported; otherwise why it isn't."""
    text = sentence["text"]
    if _IDENTITY.search(text) or redact(text) != text:
        return "names or identifies a patient"
    if _MEDICAL.search(text):
        return "gives medical advice"
    if _CAUSE.search(text) and NO_CAUSE not in text:
        return "claims a cause the data can't show"
    cited = {c["result_id"] for c in sentence.get("claims", [])}
    if cited & set(thin) and _TREND.search(text) and "not enough evidence" not in text.lower():
        return "reports a difference where there is not enough evidence"
    claim_values = []
    for c in sentence.get("claims", []):
        table = fact_tables.get(c["result_id"])
        if table is None:
            return f"cites unknown result {c['result_id']}"
        if c["field"] not in table:
            return f"cites unknown field {c['field']} of {c['result_id']}"
        if not number_ok(float(c["value"]), [float(table[c["field"]])]) and not _close(float(c["value"]), float(table[c["field"]])):
            return f"{c['result_id']}.{c['field']} is {table[c['field']]}, not {c['value']}"
        claim_values.append(float(table[c["field"]]))
    for raw in _NUM.findall(text):
        x = float(raw.replace(",", ""))
        if not number_ok(x, claim_values) and not any(_close(x, a) for a in allowed):
            return f"the number {raw} isn't backed by a claim"
    return None


def critic(sentences: list[dict], question: str, results: dict[str, dict], fact_tables: dict[str, dict], thin=()) -> tuple[list[dict], list[str], bool]:
    """Returns (kept sentences, problems, had_cause_problem). Unsupported-cause sentences are replaced by NO_CAUSE."""
    allowed = context_numbers(question, results)
    kept, problems, cause = [], [], False
    for s in sentences:
        why = check_sentence(s, results, fact_tables, allowed, thin)
        if why is None:
            kept.append(s)
            continue
        problems.append(f"\"{s['text'][:80]}\": {why}")
        if why == "claims a cause the data can't show":
            cause = True
    if cause and not any(s["text"] == NO_CAUSE for s in kept):
        kept.append({"text": NO_CAUSE, "claims": []})
    return kept, problems, cause


# ---- the rule-based writer ---------------------------------------------------------------------------------------
def _mins(v) -> str:
    return f"{v} minutes"


def _val_text(kind: str, s: dict, prefix: str, rid: str) -> tuple[str, list[dict]]:
    if kind == "time":
        return _mins(s["median"]), [{"result_id": rid, "field": f"{prefix}.median", "value": s["median"]}]
    if kind == "rate":
        return f"{s['pct']}%", [{"result_id": rid, "field": f"{prefix}.pct", "value": s["pct"]}]
    return f"{s['n']}", [{"result_id": rid, "field": f"{prefix}.n", "value": s["n"]}]


def _scope(r: dict) -> str:
    """'For TPA Gamma, ' when the result is filtered to one TPA, ward or payer type (and not split by it)."""
    f, split = r["params"].get("filters") or {}, set(r["params"].get("group_by") or [])
    parts = []
    if f.get("tpa") and "tpa" not in split:
        parts.append(" and ".join(f["tpa"]))
    if f.get("ward") and "ward" not in split:
        parts.append(" and ".join(f"Ward {w}" for w in f["ward"]))
    if f.get("payer_type") and "payer_type" not in split:
        parts.append(" and ".join(p.replace("_", "-") + " patients" for p in f["payer_type"]))
    return f"For {', '.join(parts)}, " if parts else ""


def _stat_word(kind: str) -> str:
    return "median " if kind == "time" else ""


def write_rules(findings: list[dict], results: dict[str, dict]) -> list[dict]:
    """2-5 short sentences, each with claims, from the findings. Always passes the critic."""
    out: list[dict] = []
    thin: set[str] = set()
    for fi in findings:
        r = results[fi["result_id"]]
        kind, rid, label = r["kind"], r["id"], r["label"]
        noun = r["noun"]
        if fi["type"] == "comparison":
            f, b = r["focus"], r["baseline"]
            if fi["label"] == "not enough evidence":
                if fi["focus_label"] in thin:  # one "not enough evidence" per period is enough
                    continue
                thin.add(fi["focus_label"])
                nf = f["n"] if isinstance(f.get("n"), int) else None
                if nf is not None:
                    out.append({"text": f"There is not enough evidence to call this a trend: {fi['focus_label']} had only {nf} {noun}, "
                                        f"and CareFlow needs at least {MIN_CASES} on each side to compare.",
                                "claims": [{"result_id": rid, "field": "focus.n", "value": nf}, {"result_id": rid, "field": "min_cases", "value": MIN_CASES}]})
                else:
                    out.append({"text": f"There is not enough evidence to call this a trend: {fi['focus_label']} had fewer than 5 {noun}, "
                                        f"too few to conclude anything.", "claims": [{"result_id": rid, "field": "min_group", "value": 5}]})
                continue
            fv, fc = _val_text(kind, f, "focus", rid)
            bv, bc = _val_text(kind, b, "baseline", rid)
            if fi["label"] == "finding":
                word = ("longer" if kind == "time" else "higher") if fi["direction"] == "higher" else ("shorter" if kind == "time" else "lower")
                extra, ec = "", []
                if fi.get("ratio") and kind != "count":
                    extra, ec = f", {fi['ratio']} times the baseline", [{"result_id": rid, "field": "ratio", "value": fi["ratio"]}]
                out.append({"text": f"The {_stat_word(kind)}{label} was {word} for {fi['focus_label']}: {fv} against {bv} for "
                                    f"{fi['baseline_label']}{extra}.", "claims": fc + bc + ec})
            else:
                out.append({"text": f"The {_stat_word(kind)}{label} for {fi['focus_label']} was {fv}, not clearly different from "
                                    f"{bv} for {fi['baseline_label']}.", "claims": fc + bc})
        elif fi["type"] == "group_change":
            key = ",".join(f"{d}={v}" for d, v in fi["group"].items())
            g = next(x for x in r["groups"] if x["key"] == fi["group"])
            b = next(x for x in r["baseline_groups"] if x["key"] == fi["group"])
            gv, gc = _val_text(kind, g, f"group[{key}]", rid)
            bv, bc = _val_text(kind, b, f"baseline_group[{key}]", rid)
            out.append({"text": f"The biggest increase was in {fi['group_label']}: {gv} against {bv} in {fi['baseline_label']}.", "claims": gc + bc})
        elif fi["type"] == "ranking":
            if fi.get("group") is None:
                out.append({"text": f"There is not enough evidence to rank them: too few groups had at least {MIN_CASES} {noun}.",
                            "claims": [{"result_id": rid, "field": "min_cases", "value": MIN_CASES}]})
                continue
            key = ",".join(f"{d}={v}" for d, v in fi["group"].items())
            g = next(x for x in r["groups"] if x["key"] == fi["group"])
            gv, gc = _val_text(kind, g, f"group[{key}]", rid)
            top = {"time": "the longest", "rate": "the highest", "count": "the most"}[kind] if fi["rank"] == "highest" else \
                  {"time": "the shortest", "rate": "the lowest", "count": "the fewest"}[kind]
            dim = DIMENSIONS[r["params"]["group_by"][0]].label
            text = f"{fi['group_label']} had {top} {_stat_word(kind)}{label} of any {dim}: {gv} ({g['n']} {noun})"
            claims = gc + [{"result_id": rid, "field": f"group[{key}].n", "value": g["n"]}]
            if fi["label"] == "finding":
                text += ", a clear difference from the others."
            elif fi["label"] == "no clear difference":
                text += ", but it is not clearly different from the others."
            elif fi["label"] == "not enough evidence":
                text += ", but there is not enough evidence that this is a real difference."
            else:
                text += "."
            out.append({"text": text, "claims": claims})
        else:
            t = r["total"]
            if t.get("suppressed"):
                out.append({"text": f"There were fewer than 5 {noun} in this period, too few to report.", "claims": []})
                continue
            if _n(t) == 0:
                continue
            v, c = _val_text(kind, t, "total", rid)
            out.append({"text": f"The {_stat_word(kind)}{label} was {v} across {t['n']} {noun}.",
                        "claims": c + [{"result_id": rid, "field": "total.n", "value": t["n"]}]})
    seen, unique = set(), []
    for s in out:
        rid = next((c["result_id"] for c in s["claims"] if c["result_id"] in results), None)
        pre = _scope(results[rid]) if rid else ""
        if pre and not s["text"].startswith("There is"):
            s["text"] = pre + s["text"][0].lower() + s["text"][1:] if s["text"].startswith("The ") else pre + s["text"]
        s["text"] = s["text"][0].upper() + s["text"][1:]
        if s["text"] not in seen:
            seen.add(s["text"])
            unique.append(s)
    return unique[:5]


def based_on(results: dict[str, dict]) -> str:
    parts = []
    for r in results.values():
        p = r["params"]
        t = r["total"]
        n = t["n"] if isinstance(t.get("n"), int) else "fewer than 5"
        seg = f"{r['id']} {r['label']}: {n} {r['noun']}, {p['period']['label'] or p['period']['start'] + ' to ' + p['period']['end']}"
        if "baseline" in r and p.get("compare", {}).get("kind") == "period":
            bn = r["baseline"]["n"] if isinstance(r["baseline"].get("n"), int) else "fewer than 5"
            seg += f", compared with {bn} in {p['compare']['baseline_label'] or p['compare']['period']['label']}"
        elif "baseline" in r:
            seg += f" ({p['compare']['focus_label']} against {p['compare']['baseline_label']})"
        if p.get("filters"):
            seg += "; " + ", ".join(f"{k.replace('_', ' ')} = {', '.join(v)}" for k, v in p["filters"].items())
        parts.append(seg)
    return "Based on " + "; ".join(parts) + "." if parts else ""


def chart_spec(results: dict[str, dict]) -> dict | None:
    """One chart drawn only from a stored result: a line over days, or bars over a split."""
    for r in results.values():
        g = r["params"].get("group_by") or []
        if not g or not r.get("groups"):
            continue
        y = {"time": "median", "rate": "pct", "count": "n"}[r["kind"]]
        return {"type": "line" if g[0] == "day" else "bar", "result_id": r["id"], "x": g[0], "y": y,
                "unit": {"time": "minutes", "rate": "%", "count": r["noun"]}[r["kind"]], "title": f"{r['label']} by {DIMENSIONS[g[0]].label}"}
    for r in results.values():
        if "focus" in r and "baseline" in r and not r["focus"].get("suppressed") and not r["baseline"].get("suppressed"):
            y = {"time": "median", "rate": "pct", "count": "n"}[r["kind"]]
            return {"type": "bar", "result_id": r["id"], "x": "side", "y": y,
                    "unit": {"time": "minutes", "rate": "%", "count": r["noun"]}[r["kind"]], "title": r["label"]}
    return None


def follow_ups(understanding: dict, findings: list[dict], results: dict[str, dict]) -> list[str]:
    out = []
    for fi in findings:
        r = results[fi["result_id"]]
        if fi["type"] == "group_change" and "department" in fi["group"]:
            out.append(f"Was {fi['group']['department']} slower for one payer or ward?")
        if fi["type"] == "ranking" and fi.get("group"):
            out.append(f"How has {fi['group_label']} changed over the last 90 days?")
        if r["measure"] == "discharge_time" and fi["type"] == "comparison":
            out.append("Which department is slowest this month?")
    out += ["Which TPA is slowest in the last 30 days?", "What is the trend of discharge time over the last 90 days?"]
    return list(dict.fromkeys(out))[:3]


def measure_names() -> list[str]:
    return list(REGISTRY)
