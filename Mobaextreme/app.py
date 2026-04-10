"""
ZOLT.AI — Core Competency Viewer + Screener (v9)
Handles both legacy JSON and v5 JSON (provenance, static descriptors).
Serves source PDFs from Investor Presentation folder.
Run:  python app.py
Open: http://localhost:6533
"""

import os, json, glob, re
from flask import Flask, render_template, jsonify, abort, send_file, request, Response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(BASE_DIR, "Input")
IP_DIR = os.path.join(BASE_DIR, "Investor Presentation")
PORT = int(os.environ.get("PORT", 6533))

app = Flask(__name__)


# ─── Period Sorting ───────────────────────────────────────────────

def period_sort_key(p):
    p_clean = p.strip()
    if p_clean.lower() == 'static':
        return (5, 0, 0)
    fy_match = re.search(r'FY\s*(\d{2,4})', p_clean, re.I)
    fy_num = 0
    if fy_match:
        fy_num = int(fy_match.group(1))
        if fy_num < 100:
            fy_num += 2000
    q = re.match(r'^Q([1-4])\s*FY', p_clean, re.I) or re.match(r'^([1-4])Q\s*FY', p_clean, re.I)
    if q:
        return (0, fy_num, int(q.group(1)))
    h = re.match(r'^H([12])\s*FY', p_clean, re.I)
    if h:
        return (1, fy_num, int(h.group(1)))
    nm = re.match(r'^9M\s*FY', p_clean, re.I)
    if nm:
        return (2, fy_num, 0)
    fy_only = re.match(r'^FY\s*(\d{2,4})$', p_clean, re.I)
    if fy_only:
        return (3, fy_num, 0)
    year = re.search(r'(\d{4})', p_clean)
    if year:
        return (4, int(year.group(1)), 0)
    return (4, 9999, 0)


def sort_periods(periods):
    return sorted(periods, key=period_sort_key)


def period_group_label(p):
    if p.strip().lower() == 'static':
        return 'Static'
    key = period_sort_key(p)
    return {0: 'Quarterly', 1: 'Half Year', 2: '9 Month', 3: 'Annual', 4: 'Other', 5: 'Static'}[key[0]]


app.jinja_env.filters['sort_periods'] = sort_periods
app.jinja_env.filters['period_group'] = period_group_label


# ─── Data Normalization ──────────────────────────────────────────

def normalize_metric(m):
    result = {
        "particulars": m.get("particulars", ""),
        "unit": m.get("unit", ""),
        "metric_type": m.get("metric_type", "periodic"),
        "_provenance": {},
    }
    metric_type = m.get("metric_type", "")

    if metric_type == "static_descriptor":
        val = str(m.get("value", ""))
        result["values"] = {"Static": val}
        result["_provenance"]["Static"] = {
            "ref": m.get("ref", ""), "src": m.get("src", ""),
            "pg": m.get("pg", ""), "conf": m.get("conf", ""),
        }
    elif "values" in m:
        raw_values = m["values"]
        normalized_values = {}
        provenance = {}
        for period, val in raw_values.items():
            if isinstance(val, dict):
                normalized_values[period] = str(val.get("v", ""))
                provenance[period] = {
                    "ref": val.get("ref", ""), "src": val.get("src", ""),
                    "pg": val.get("pg", ""), "conf": val.get("conf", ""),
                }
            else:
                normalized_values[period] = str(val)
        result["values"] = normalized_values
        result["_provenance"] = provenance
    elif "value" in m:
        val = str(m.get("value", ""))
        result["values"] = {"Static": val}
        result["metric_type"] = "static_descriptor"
        result["_provenance"]["Static"] = {
            "ref": m.get("ref", ""), "src": m.get("src", ""),
            "pg": m.get("pg", ""), "conf": m.get("conf", ""),
        }
    else:
        result["values"] = {}
    return result


def normalize_discovery_metric(m):
    return {
        "metric": m.get("metric", ""),
        "unit": m.get("unit", ""),
        "metric_type": m.get("metric_type", "periodic"),
        "periods_available": m.get("periods_available", []),
    }


def normalize_json(data):
    for seg in data.get("operational_scorecard", []):
        seg["data"] = [normalize_metric(m) for m in seg.get("data", [])]
    data["accolades"] = [normalize_metric(a) for a in data.get("accolades", [])]
    for seg in data.get("discovery", []):
        seg["metrics_found"] = [normalize_discovery_metric(m) for m in seg.get("metrics_found", [])]
    data["discovery_accolades"] = [normalize_discovery_metric(m) for m in data.get("discovery_accolades", [])]
    return data


# ─── Data Loading ─────────────────────────────────────────────────

def load_json(fpath):
    with open(fpath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return normalize_json(data)


def scan_companies():
    companies = []
    for fpath in sorted(glob.glob(os.path.join(INPUT_DIR, "*.json"))):
        fname = os.path.basename(fpath)
        try:
            data = load_json(fpath)
            segments = data.get("operational_scorecard", [])
            accolades = data.get("accolades", [])
            total_metrics = sum(len(s["data"]) for s in segments)
            total_dp = sum(len(m.get("values", {})) for s in segments for m in s["data"])
            acc_dp = sum(len(a.get("values", {})) for a in accolades)
            company_name = data.get("company_name") or os.path.splitext(fname)[0]
            companies.append({
                "filename": fname, "name": company_name,
                "segments": len(segments),
                "segment_names": [s["segment_name"] for s in segments],
                "accolades": len(accolades), "metrics": total_metrics,
                "datapoints": total_dp + acc_dp,
                "documents_scanned": data.get("documents_scanned", "—"),
                "time_range": data.get("time_range", "—"),
                "has_discovery": len(data.get("discovery", [])) > 0,
            })
        except Exception as e:
            print(f"  SKIP {fname}: {e}")
    return companies


def build_screener_data():
    seg_index = {}
    metric_index = {}
    company_index = {}
    all_seg_names = []
    all_metric_names = set()

    for fpath in sorted(glob.glob(os.path.join(INPUT_DIR, "*.json"))):
        fname = os.path.basename(fpath)
        try:
            data = load_json(fpath)
            company_name = data.get("company_name") or os.path.splitext(fname)[0]
            segments = data.get("operational_scorecard", [])

            # Build company index entry
            if company_name not in company_index:
                company_index[company_name] = {"filename": fname, "metrics": []}

            for seg in segments:
                seg_name = seg["segment_name"]
                if seg_name not in seg_index:
                    seg_index[seg_name] = []
                    all_seg_names.append(seg_name)

                seg_entry = {"company": company_name, "filename": fname, "metrics": []}
                for m in seg["data"]:
                    mname = m["particulars"]
                    unit = m.get("unit", "")
                    values = m.get("values", {})
                    prov = m.get("_provenance", {})
                    all_metric_names.add(mname)

                    # Build enriched values: {"period": {"v": "val", "ref": "...", "src": "...", "pg": "..."}}
                    enriched = {}
                    for p, v in values.items():
                        entry = {"v": v}
                        if p in prov:
                            entry["ref"] = prov[p].get("ref", "")
                            entry["src"] = prov[p].get("src", "")
                            entry["pg"] = prov[p].get("pg", "")
                            entry["conf"] = prov[p].get("conf", "")
                        enriched[p] = entry

                    seg_entry["metrics"].append({
                        "name": mname, "unit": unit,
                        "values": values,       # flat for display
                        "enriched": enriched,    # with provenance
                    })

                    if mname not in metric_index:
                        metric_index[mname] = []
                    metric_index[mname].append({
                        "company": company_name, "filename": fname,
                        "segment": seg_name, "unit": unit,
                        "values": values, "enriched": enriched,
                    })

                    # Also add to company index
                    company_index[company_name]["metrics"].append({
                        "name": mname, "unit": unit, "segment": seg_name,
                        "values": values, "enriched": enriched,
                    })

                seg_index[seg_name].append(seg_entry)
        except Exception as e:
            print(f"  SKIP {fname}: {e}")

    sorted_metrics = sorted(metric_index.keys(), key=lambda k: -len(metric_index[k]))
    sorted_companies = sorted(company_index.keys(), key=lambda k: -len(company_index[k]["metrics"]))
    return {
        "seg_index": seg_index, "metric_index": metric_index,
        "company_index": company_index,
        "seg_names": all_seg_names, "metric_names": sorted_metrics,
        "company_names": sorted_companies,
        "seg_count": len(all_seg_names), "metric_count": len(all_metric_names),
        "company_count": len(company_index),
        "period_group": period_group_label, "sort_periods": sort_periods,
    }


# ─── PDF Serving ──────────────────────────────────────────────────

def find_pdf(filename):
    """Search for a PDF file across all company folders in Investor Presentation."""
    if not os.path.exists(IP_DIR):
        return None
    for company_folder in os.listdir(IP_DIR):
        fpath = os.path.join(IP_DIR, company_folder, filename)
        if os.path.exists(fpath):
            return fpath
    # Also try with _IP.pdf suffix
    base = os.path.splitext(filename)[0]
    for company_folder in os.listdir(IP_DIR):
        folder_path = os.path.join(IP_DIR, company_folder)
        if not os.path.isdir(folder_path):
            continue
        for f in os.listdir(folder_path):
            if f == filename or base in f:
                return os.path.join(folder_path, f)
    return None


# ─── Routes ───────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", companies=scan_companies())


@app.route("/company/<path:filename>")
def company_view(filename):
    fpath = os.path.join(INPUT_DIR, filename)
    if not os.path.exists(fpath):
        abort(404)
    data = load_json(fpath)
    company_name = data.get("company_name") or os.path.splitext(filename)[0]
    return render_template("dashboard.html", data=data, company_name=company_name, filename=filename)


@app.route("/screener")
def screener():
    sd = build_screener_data()
    return render_template("screener.html", sd=sd)


@app.route("/chat")
def chat_page():
    companies = scan_companies()
    return render_template("chat.html", companies=companies)


@app.route("/pdf/<path:filename>")
def serve_pdf(filename):
    """Serve a PDF from Investor Presentation folder. Use ?page=N to hint page."""
    fpath = find_pdf(filename)
    if fpath and os.path.exists(fpath):
        return send_file(fpath, mimetype='application/pdf')
    abort(404)


@app.route("/api/companies")
def api_companies():
    return jsonify(scan_companies())


# ─── Chat Engine v2 ───────────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434"
CHAT_MODEL = "qwen3:4b"
CHAT_MODEL_LIGHT = "llama3.2:1b"

STOP_WORDS = {'what','how','why','when','where','which','the','and','for','are','was',
              'has','had','have','been','will','can','did','does','about','from','with',
              'that','this','show','tell','give','list','all','many','much','is','of','in','to',
              'me','my','do','it','at','on','by','an','or','if','up','so','no','not','but',
              'its','their','them','these','those','than','then','into','been','would','could',
              'should','what','latest','current','recent','data','value','values',
              'company','wide','segment','total'}
GENERIC_WORDS = {'limited','ltd','india','pvt','private','corporation','corp','company','group','industries','wide'}

def build_search_index():
    """Build a comprehensive search index for fast lookup."""
    word_index = {}       # word -> set of company names
    metric_db = []        # list of {company, segment, metric, unit, values} dicts
    company_names = []    # all company names
    for fpath in sorted(glob.glob(os.path.join(INPUT_DIR, "*.json"))):
        try:
            data = load_json(fpath)
            company_name = data.get("company_name") or os.path.splitext(os.path.basename(fpath))[0]
            company_names.append(company_name)
            # Index company name words
            for word in re.findall(r'[a-zA-Z]{2,}', company_name.lower()):
                word_index.setdefault(word, set()).add(company_name)
            for seg in data.get("operational_scorecard", []):
                seg_name = seg["segment_name"]
                for word in re.findall(r'[a-zA-Z]{2,}', seg_name.lower()):
                    word_index.setdefault(word, set()).add(company_name)
                for m in seg["data"]:
                    metric_name = m["particulars"]
                    unit = m.get("unit", "")
                    values = m.get("values", {})
                    # Index metric name words
                    for word in re.findall(r'[a-zA-Z]{2,}', metric_name.lower()):
                        word_index.setdefault(word, set()).add(company_name)
                    # Store structured metric
                    vals = {p: v for p, v in values.items() if v and v != '—'}
                    if vals:
                        metric_db.append({
                            "company": company_name, "segment": seg_name,
                            "metric": metric_name, "unit": unit, "values": vals,
                        })
        except Exception:
            pass
    return word_index, metric_db, company_names

_word_index = None
_metric_db = None
_company_names = None

def get_search_data():
    global _word_index, _metric_db, _company_names
    if _word_index is None:
        _word_index, _metric_db, _company_names = build_search_index()
    return _word_index, _metric_db, _company_names


def find_relevant_context(query, selected_companies=None):
    """Build focused context string for model."""
    matches = search_metrics(query, selected_companies, limit=60)
    if not matches:
        return ""
    lines = []
    cur_comp = None
    for m in matches:
        if m["company"] != cur_comp:
            lines.append(f"\n=== {m['company']} ===")
            cur_comp = m["company"]
        val_str = ", ".join(f"{p}: {v}" for p, v in m["values"].items())
        lines.append(f"[{m['segment']}] {m['metric']} ({m['unit']}): {val_str}")
    return "\n".join(lines)


def search_metrics(query, selected_companies=None, limit=20):
    """Core search: find matching metrics. Segment-aware."""
    word_index, metric_db, company_names = get_search_data()
    q = query.lower().strip()
    query_words = set(re.findall(r'[a-zA-Z]{2,}', q))
    meaningful = query_words - STOP_WORDS

    # ── Parse state from query ──
    state = parse_input_state(query, metric_db, company_names)

    # Companies from state + sidebar selection
    companies = set(selected_companies or [])
    if state["company"]:
        companies.add(state["company"])

    company_words_used = set()
    if state["company"]:
        company_words_used.update(re.findall(r'[a-zA-Z]{2,}', state["company"].lower()))
    segment_words_used = set()
    if state["segment"]:
        segment_words_used.update(re.findall(r'[a-zA-Z]{2,}', state["segment"].lower()))

    # If no company from state, try word matching
    if not companies:
        comp_scores = {}
        for word in meaningful:
            for comp in word_index.get(word, []):
                comp_scores[comp] = comp_scores.get(comp, 0) + 1
        if comp_scores:
            max_score = max(comp_scores.values())
            companies = {c for c, s in comp_scores.items() if s >= max(1, max_score - 1)}
            if len(companies) > 5:
                companies = set(sorted(comp_scores, key=comp_scores.get, reverse=True)[:5])
            for word in meaningful:
                if word in word_index:
                    company_words_used.add(word)

    # Metric search words
    metric_words = meaningful - company_words_used - segment_words_used - GENERIC_WORDS

    # ── Time filter detection ──
    time_filter = None
    tm3 = re.search(r'q([1-4])\s*fy\s*(\d{2,4})', q)
    if tm3:
        time_filter = f"Q{tm3.group(1)}FY{tm3.group(2)}"
    else:
        tm2 = re.search(r'fy\s*(\d{2,4})', q)
        if tm2:
            time_filter = f"FY{tm2.group(1)}"

    want_latest = any(w in q for w in ['latest','current','recent','last','newest'])

    # ── Score and filter ──
    results = []

    # FIRST: Check for exact metric name match in query
    # If user selected a specific metric from autocomplete, match it exactly
    exact_metric = None
    for m in metric_db:
        if companies and m["company"] not in companies:
            continue
        if state["segment"] and m["segment"] != state["segment"]:
            continue
        # Check if full metric name appears in the query
        if m["metric"].lower() in q:
            if exact_metric is None or len(m["metric"]) > len(exact_metric):
                exact_metric = m["metric"]

    for m in metric_db:
        # Company filter
        if companies and m["company"] not in companies:
            continue
        # Segment filter
        if state["segment"] and m["segment"] != state["segment"]:
            continue

        # If we found an exact metric name, ONLY return that metric
        if exact_metric:
            if m["metric"].lower() != exact_metric.lower():
                continue
            score = 100  # highest priority
        else:
            # Fuzzy word matching
            search_text = (m["metric"] + " " + m["segment"]).lower()
            if metric_words:
                score = sum(1 for w in metric_words if w in search_text)
                if score == 0:
                    continue
            else:
                if state["company"] or companies:
                    score = 1
                else:
                    continue

        filtered_vals = m["values"]
        if time_filter:
            filtered_vals = {p: v for p, v in m["values"].items()
                           if time_filter.lower().replace(' ','') in p.lower().replace(' ','')}
            if not filtered_vals:
                continue

        if want_latest and filtered_vals:
            sorted_periods_list = sort_periods(list(filtered_vals.keys()))
            last_p = sorted_periods_list[-1]
            filtered_vals = {last_p: filtered_vals[last_p]}

        results.append({**m, "values": filtered_vals, "_score": score})

    results.sort(key=lambda x: -x["_score"])
    return results[:limit]


def pick_model(query, num_companies):
    q = query.lower()
    if any(w in q for w in ['compare','versus','vs','difference','better','worse','trend','analyze','analysis','why','explain']):
        return CHAT_MODEL
    if num_companies > 1:
        return CHAT_MODEL
    return CHAT_MODEL_LIGHT if CHAT_MODEL_LIGHT else CHAT_MODEL


# ─── Level 1: Direct Data Lookup → Table Format ──────────────────

def try_direct_lookup(query, selected_companies=None):
    """Answer directly from data in table format. Returns HTML string or None."""
    q = query.lower().strip()

    # Needs model for analytical questions
    model_triggers = ['compare','versus','vs','why','explain','analyze','analysis',
                      'better','worse','should','recommend','predict','forecast',
                      'what do you think','opinion','insight']
    if any(t in q for t in model_triggers):
        return None

    matches = search_metrics(query, selected_companies, limit=10)
    if not matches:
        return None

    # ── Build output: bar chart + compact rows ──
    parts = []
    current_company = None

    for m in matches:
        comp = m["company"]
        if comp != current_company:
            parts.append(f"**{comp}**")
            current_company = comp

        metric_name = m["metric"]
        unit = m["unit"]
        vals = m["values"]
        sorted_p = sort_periods(list(vals.keys()))

        if len(sorted_p) == 1:
            p = sorted_p[0]
            parts.append(f"📊 **{metric_name}** ({unit}): **{vals[p]}** ({p})")
        else:
            parts.append(f"📊 **{metric_name}** ({unit})")
            # Bar chart data marker — frontend will render this
            numeric_vals = []
            for p in sorted_p:
                try:
                    nv = float(vals[p].replace(',','').replace('%','').replace('+',''))
                    numeric_vals.append({"p": p, "v": nv, "label": vals[p]})
                except ValueError:
                    numeric_vals.append({"p": p, "v": 0, "label": vals[p]})
            if len(numeric_vals) >= 2:
                import json as _json
                parts.append(f"<!--BARCHART:{_json.dumps(numeric_vals)}-->")
            # Change calculation only — no need to repeat values, bar chart shows them
            try:
                first_v = float(vals[sorted_p[0]].replace(',','').replace('%','').replace('+',''))
                last_v = float(vals[sorted_p[-1]].replace(',','').replace('%','').replace('+',''))
                if first_v != 0:
                    pct = ((last_v - first_v) / abs(first_v)) * 100
                    icon = "📈" if pct > 0 else "📉" if pct < 0 else "➡️"
                    parts.append(f"{icon} **{pct:+.1f}%** ({sorted_p[0]} → {sorted_p[-1]})")
            except (ValueError, ZeroDivisionError):
                pass

    if not parts:
        return None

    return "\n".join(parts)


# ─── Suggest Endpoint v3: State Machine ──────────────────────────

def parse_input_state(full_input, metric_db, company_names):
    """Detect what's already locked in the input: company, segment, metric words."""
    fi = full_input.lower().strip()
    state = {"company": None, "segment": None, "remaining": fi}

    # 1. Check if a full company name is in the input
    for comp in sorted(company_names, key=len, reverse=True):  # longest first to avoid partial matches
        if comp.lower() in fi:
            state["company"] = comp
            # Remove company name from remaining text
            state["remaining"] = fi.replace(comp.lower(), "").strip()
            break

    if not state["company"]:
        return state

    # 2. Check if a segment name for that company is in the remaining text
    segments_for_company = set()
    for m in metric_db:
        if m["company"] == state["company"]:
            segments_for_company.add(m["segment"])

    for seg in sorted(segments_for_company, key=len, reverse=True):
        if seg.lower() in fi:
            state["segment"] = seg
            state["remaining"] = state["remaining"].replace(seg.lower(), "").strip()
            break

    return state


@app.route("/api/chat/suggest", methods=["POST"])
def chat_suggest():
    """State machine suggestions: company → segment → metric."""
    data = request.json or {}
    query = data.get("query", "").lower().strip()  # last word being typed
    full_input = data.get("full_input", "").strip()
    selected = data.get("selected_companies", [])

    if len(query) < 1:
        return jsonify({"suggestions": [], "type": "none"})

    word_index, metric_db, company_names = get_search_data()

    # Parse current state from FULL input (not just last word)
    state = parse_input_state(full_input, metric_db, company_names)

    # If company selected from sidebar but not in input
    if not state["company"] and selected:
        state["company"] = selected[0]

    suggestions = []
    seen = set()

    # ── STATE 1: No company yet → show matching companies ──
    if not state["company"]:
        # Show companies matching the query word
        for comp in company_names:
            if query in comp.lower() and comp not in seen:
                suggestions.append({"text": comp, "type": "company"})
                seen.add(comp)

        # ALSO check if query matches a metric name → show "metric (company)" style
        if len(suggestions) < 5:
            for m in metric_db:
                if query in m["metric"].lower() and m["metric"] not in seen:
                    label = f"{m['metric']}  →  {m['company'][:30]}"
                    suggestions.append({"text": m["metric"], "type": "metric", "hint": m["company"]})
                    seen.add(m["metric"])
                    if len(suggestions) >= 10:
                        break

        type_order = {"company": 0, "metric": 1}
        suggestions.sort(key=lambda x: type_order.get(x["type"], 2))
        return jsonify({"suggestions": suggestions[:10], "type": "company_search"})

    # ── STATE 2: Company locked, no segment yet ──
    if state["company"] and not state["segment"]:
        clean_query = re.sub(r'[^a-z]', '', query)

        # Check if query word matches any metric directly → skip segment
        if len(clean_query) >= 3:
            metric_matches = []
            for m in metric_db:
                if m["company"] != state["company"]:
                    continue
                if clean_query in m["metric"].lower() and m["metric"] not in seen:
                    metric_matches.append(m["metric"])
                    seen.add(m["metric"])
            if metric_matches:
                for met in metric_matches[:15]:
                    suggestions.append({"text": met, "type": "metric"})
                return jsonify({"suggestions": suggestions[:15], "type": "metric_direct"})

        # Show segments for this company
        segments = set()
        for m in metric_db:
            if m["company"] == state["company"] and m["segment"] not in segments:
                if len(clean_query) >= 2 and clean_query in m["segment"].lower():
                    suggestions.append({"text": m["segment"], "type": "segment"})
                    segments.add(m["segment"])

        # If no match or short query, show ALL segments
        if not suggestions:
            segments = set()
            for m in metric_db:
                if m["company"] == state["company"] and m["segment"] not in segments:
                    suggestions.append({"text": m["segment"], "type": "segment"})
                    segments.add(m["segment"])

        return jsonify({"suggestions": suggestions[:15], "type": "segment_select"})

    # ── STATE 3: Company + Segment locked → show ALL metrics in that segment ──
    if state["company"] and state["segment"]:
        all_metrics = []
        for m in metric_db:
            if m["company"] != state["company"] or m["segment"] != state["segment"]:
                continue
            if m["metric"] not in seen:
                all_metrics.append(m["metric"])
                seen.add(m["metric"])

        # If query is meaningful (not just segment tail), filter
        clean_query = re.sub(r'[^a-z]', '', query)
        if len(clean_query) >= 3:
            filtered = [met for met in all_metrics if clean_query in met.lower()]
            if filtered:
                all_metrics = filtered

        for met in all_metrics:
            suggestions.append({"text": met, "type": "metric"})

        return jsonify({"suggestions": suggestions[:20], "type": "metric_select"})

    return jsonify({"suggestions": [], "type": "none"})


@app.route("/api/chat", methods=["POST"])
def chat_api():
    """Hybrid chat: direct data lookup for factual queries, model for analysis."""
    data = request.json or {}
    query = data.get("query", "")
    selected = data.get("selected_companies", [])
    history = data.get("history", [])

    if not query.strip():
        return jsonify({"error": "Empty query"}), 400

    # ── Level 1: Try direct data lookup first ──
    direct = try_direct_lookup(query, selected)
    if direct:
        # Return as instant SSE (no model call)
        def instant():
            yield f"data: {json.dumps({'token': direct})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        return Response(instant(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── Level 2: Model needed for analysis/comparison ──
    import urllib.request
    context = find_relevant_context(query, selected)

    if not context.strip():
        def no_data():
            yield f"data: {json.dumps({'token': '⚠️ No matching data found. Try selecting a company from the sidebar first, or use a more specific metric name.'})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        return Response(no_data(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    system_prompt = f"""You are ZOLT.AI, an operational intelligence assistant.
Answer using ONLY the data below. Be concise. Use tables for comparisons. Never invent data.
If data is missing, say "Not available in data." Do NOT guess or add information not present below.

DATA:
{context}
"""

    selected_comps = selected or []
    use_model = pick_model(query, len(selected_comps))
    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-4:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": query})

    def stream():
        try:
            payload = json.dumps({
                "model": use_model,
                "messages": messages,
                "stream": True,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                for line in resp:
                    if line:
                        try:
                            chunk = json.loads(line.decode("utf-8"))
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                yield f"data: {json.dumps({'token': token})}\n\n"
                            if chunk.get("done"):
                                yield f"data: {json.dumps({'done': True})}\n\n"
                                break
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/chat/models", methods=["GET"])
def chat_models():
    """List available Ollama models."""
    import urllib.request
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name") or m.get("model", "?") for m in data.get("models", [])]
            return jsonify({"models": models, "active": CHAT_MODEL})
    except Exception as e:
        return jsonify({"models": [], "error": str(e)}), 500


if __name__ == "__main__":
    os.makedirs(INPUT_DIR, exist_ok=True)
    companies = scan_companies()
    # Pre-build search index for chat
    _word_index, _metric_db, _company_names = build_search_index()
    print(f"\n  ZOLT.AI Core Competency Viewer + Screener (v9) + Chat")
    print(f"  Input folder: {INPUT_DIR}")
    print(f"  IP folder: {IP_DIR}")
    print(f"  Companies found: {len(companies)}")
    print(f"  Chat index: {len(_word_index)} keywords, {len(_metric_db)} metrics")
    print(f"  Chat model: {CHAT_MODEL} via {OLLAMA_URL}")
    for c in companies:
        print(f"    • {c['name']} — {c['time_range']} — {c['documents_scanned']} docs")
    print(f"\n  Open: http://localhost:{PORT}")
    print(f"  Screener: http://localhost:{PORT}/screener\n")
    app.run(host="0.0.0.0", port=PORT)