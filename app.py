# app.py — Serveur Flask PredictFoot
from flask import Flask, jsonify, request, render_template, session
from flask_cors import CORS
import requests
import json
import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from prediction import calculer_stats_equipe, predire_match, calculer_stats_h2h

# REMPLACE 'your_function_name' PAR CETTE LISTE :
from database import (
    verifier_code, 
    creer_code, 
    creer_plusieurs_codes,
    revoquer_code, 
    reactiver_code,
    lister_codes, 
    supprimer_code,
    prolonger_code,
    lister_codes,
    rechercher_code,
    stats_codes
)

load_dotenv()

app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv("SECRET_KEY", "bradfoot-secret-2025")

# ── Config ──
API_TOKEN      = os.getenv("API_TOKEN", "c67e9f5362d54bcdb5042f6f3e2ec0c2")
BASE_URL       = "http://api.football-data.org/v4"
HEADERS        = {"X-Auth-Token": API_TOKEN}
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin2025")

COMPETITIONS = [
    {"code": "PL",  "name": "Premier League"},
    {"code": "FL1", "name": "Ligue 1"},
    {"code": "BL1", "name": "Bundesliga"},
    {"code": "SA",  "name": "Serie A"},
    {"code": "PD",  "name": "La Liga"},
    {"code": "CL",  "name": "UEFA Champions League"},
    {"code": "PPL", "name": "Primeira Liga"},
    {"code": "ELC", "name": "Championship"},
]

CACHE_FILE     = "teams_cache.json"
CACHE_DURATION = timedelta(hours=48)
STATS_CACHE    = {}

# Cache Top 2.5 : clé = date du jour
_top25_cache = {"day_key": None, "data": None}


# ═══════════════════════════════════════
#  UTILITAIRES
# ═══════════════════════════════════════

def api_get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=10)
            if r.status_code == 429:
                wait = 12 * (attempt + 1)
                print(f"  ⏳ Rate limit — attente {wait}s (tentative {attempt+1}/{retries})...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            if r.status_code == 429:
                wait = 12 * (attempt + 1)
                print(f"  ⏳ Rate limit — attente {wait}s...")
                time.sleep(wait)
            else:
                print(f"  ❌ HTTP error: {e}")
                return None
        except Exception as e:
            print(f"  ❌ API error: {e}")
            return None
    print(f"  ❌ Échec après {retries} tentatives: {url}")
    return None


def get_teams():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        age = datetime.now() - datetime.fromisoformat(cache["last_updated"])
        if age < CACHE_DURATION:
            print(f"✅ Cache équipes valide (âge: {str(age).split('.')[0]})")
            return cache["teams"], cache["league_teams"]

    print("🔄 Rechargement cache équipes (expire dans 48h)...")
    all_teams    = {}
    league_teams = {}
    for comp in COMPETITIONS:
        print(f"  📥 Récupération équipes : {comp['name']}...")
        data = api_get(f"{BASE_URL}/competitions/{comp['code']}/teams")
        if data and "teams" in data:
            nb = len(data["teams"])
            for team in data["teams"]:
                all_teams[team["name"]] = {
                    "id":   team["id"],
                    "logo": team.get("crest", "")
                }
            league_teams[comp["name"]] = sorted([t["name"] for t in data["teams"]])
            print(f"  ✅ {nb} équipes chargées pour {comp['name']}")
        else:
            print(f"  ⚠️  Aucune donnée pour {comp['name']}")
        time.sleep(1)

    total = len(all_teams)
    print(f"✅ Cache équipes créé : {total} équipes au total")

    cache = {
        "teams":        all_teams,
        "league_teams": league_teams,
        "last_updated": datetime.now().isoformat()
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    return all_teams, league_teams


def get_recent_matches(team_id, team_name="?", nb_jours=365, limit="30", venue=None):
    cache_key_raw = f"{team_id}_{nb_jours}_{limit}_all"

    if cache_key_raw not in STATS_CACHE:
        params = {
            "status":   "FINISHED",
            "dateFrom": (datetime.today() - timedelta(days=nb_jours)).strftime("%Y-%m-%d"),
            "dateTo":   datetime.today().strftime("%Y-%m-%d"),
            "limit":    limit
        }
        print(f"  🌐 API matchs {team_name} [tous] (derniers {nb_jours}j, limit={limit})...")
        data = api_get(f"{BASE_URL}/teams/{team_id}/matches", params=params)
        all_matches = data["matches"] if data and "matches" in data else []
        print(f"  📦 {len(all_matches)} matchs récupérés pour {team_name}")
        STATS_CACHE[cache_key_raw] = all_matches
    else:
        all_matches = STATS_CACHE[cache_key_raw]
        print(f"  💾 Cache HIT matchs {team_name} → {len(all_matches)} matchs")

    if venue == "home":
        result = [m for m in all_matches if m.get("homeTeam", {}).get("id") == team_id]
    elif venue == "away":
        result = [m for m in all_matches if m.get("awayTeam", {}).get("id") == team_id]
    else:
        result = all_matches

    return result


def get_head_to_head(home_id, away_id, home_name="?", away_name="?", limit=6):
    cache_key = f"h2h_{min(home_id, away_id)}_{max(home_id, away_id)}"
    if cache_key in STATS_CACHE:
        return STATS_CACHE[cache_key]

    date_from = (datetime.today() - timedelta(days=3 * 365)).strftime("%Y-%m-%d")
    date_to   = datetime.today().strftime("%Y-%m-%d")
    ids_pair  = {home_id, away_id}

    def _extract_h2h(matches):
        return [
            m for m in matches
            if m.get("homeTeam", {}).get("id") in ids_pair
            and m.get("awayTeam", {}).get("id") in ids_pair
        ]

    d1   = api_get(f"{BASE_URL}/teams/{home_id}/matches",
                   params={"status": "FINISHED", "dateFrom": date_from, "dateTo": date_to, "limit": "100"})
    pool = _extract_h2h(d1["matches"] if d1 and "matches" in d1 else [])

    if len(pool) < limit:
        time.sleep(1)
        d2    = api_get(f"{BASE_URL}/teams/{away_id}/matches",
                        params={"status": "FINISHED", "dateFrom": date_from, "dateTo": date_to, "limit": "100"})
        extra = _extract_h2h(d2["matches"] if d2 and "matches" in d2 else [])
        ids_deja = {m["id"] for m in pool}
        for m in extra:
            if m["id"] not in ids_deja:
                pool.append(m)
                ids_deja.add(m["id"])

    pool.sort(key=lambda m: m.get("utcDate", ""), reverse=True)
    result = pool[:limit]
    STATS_CACHE[cache_key] = result
    return result


def admin_requis(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_ok"):
            return jsonify({"error": "Non autorisé."}), 401
        return f(*args, **kwargs)
    return decorated


# ═══════════════════════════════════════
#  ROUTES PUBLIQUES
# ═══════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def route_status():
    return jsonify({"status": "ok", "message": "PredictFoot fonctionne 🎉"})


@app.route('/api/teams')
def route_teams():
    _, league_teams = get_teams()
    return jsonify({"leagues": league_teams})


@app.route('/api/predict', methods=['POST'])
def route_predict():
    body      = request.get_json() or {}
    home_name = (body.get("home") or "").strip()
    away_name = (body.get("away") or "").strip()
    vip_code  = (body.get("vip_code") or "").strip().upper()

    if not home_name or not away_name:
        return jsonify({"error": "Champs 'home' et 'away' requis."}), 400
    if home_name == away_name:
        return jsonify({"error": "Les deux équipes doivent être différentes."}), 400

    # Vérification VIP
    ok, info = verifier_code(vip_code, "prediction")
    if not ok:
        return jsonify({"error": "Code VIP invalide ou expiré.", "vip_required": True}), 401

    team_ids, _ = get_teams()

    if home_name not in team_ids:
        return jsonify({"error": f"Équipe introuvable : {home_name}"}), 404
    if away_name not in team_ids:
        return jsonify({"error": f"Équipe introuvable : {away_name}"}), 404

    home_id   = team_ids[home_name]["id"]
    away_id   = team_ids[away_name]["id"]
    home_logo = team_ids[home_name]["logo"]
    away_logo = team_ids[away_name]["logo"]

    print(f"\n{'='*60}")
    print(f"🔮 PRÉDICTION : {home_name} vs {away_name}")
    print(f"{'='*60}")

    home_matches = get_recent_matches(home_id, home_name, venue="home")
    away_matches = get_recent_matches(away_id, away_name, venue="away")
    h2h_matches  = get_head_to_head(home_id, away_id, home_name, away_name)

    if not home_matches and not away_matches:
        return jsonify({"error": "Pas assez de données historiques."}), 422

    home_stats = calculer_stats_equipe(home_matches, home_id, venue="home")
    away_stats = calculer_stats_equipe(away_matches, away_id, venue="away")
    h2h_stats  = calculer_stats_h2h(h2h_matches, home_id, away_id)
    pred       = predire_match(home_stats, away_stats, h2h_stats=h2h_stats)

    return jsonify({
        "home":       home_name,
        "away":       away_name,
        "home_logo":  home_logo,
        "away_logo":  away_logo,
        "home_stats": home_stats,
        "away_stats": away_stats,
        "h2h_stats":  h2h_stats,
        "prediction": pred
    })


@app.route('/api/verify-vip', methods=['POST'])
def route_verify_vip():
    body    = request.get_json() or {}
    code    = (body.get("code") or "").strip().upper()
    section = (body.get("section") or "").strip()

    if not code:
        return jsonify({"valid": False, "error": "Code manquant."}), 400

    ok, info = verifier_code(code, section)
    if ok:
        return jsonify({"valid": True, "section": info["section"], "message": "Accès VIP activé ✅"})
    return jsonify({"valid": False, "error": info}), 401


# ═══════════════════════════════════════
#  TOP 2.5 — J / J+1 / J+2
# ═══════════════════════════════════════

@app.route('/api/top25')
def route_top25():
    global _top25_cache

    # Vérification VIP via header ou param
    vip_code = (request.args.get("vip_code") or "").strip().upper()
    ok, info = verifier_code(vip_code, "top25")
    if not ok:
        return jsonify({"error": "Code VIP invalide ou expiré.", "vip_required": True}), 401

    today   = datetime.today()
    day_key = today.strftime("%Y-%m-%d")

    if _top25_cache["day_key"] == day_key and _top25_cache["data"]:
        print("✅ Top 2.5 servi depuis le cache")
        return jsonify(_top25_cache["data"])

    date_from = today.strftime("%Y-%m-%d")
    date_to   = (today + timedelta(days=2)).strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"⚽ TOP 2.5 — {date_from} → {date_to}")
    print(f"{'='*60}")

    team_ids, _ = get_teams()
    codes_comp  = [c["code"] for c in COMPETITIONS]

    matchs_periode = []
    for code in codes_comp:
        comp_name = next((c["name"] for c in COMPETITIONS if c["code"] == code), code)
        data = api_get(f"{BASE_URL}/competitions/{code}/matches",
                       params={"dateFrom": date_from, "dateTo": date_to})
        if data and "matches" in data:
            for m in data["matches"]:
                if m.get("status") in ("SCHEDULED", "TIMED"):
                    home_name = m["homeTeam"]["name"]
                    away_name = m["awayTeam"]["name"]
                    matchs_periode.append({
                        "home":      home_name,
                        "away":      away_name,
                        "home_id":   m["homeTeam"]["id"],
                        "away_id":   m["awayTeam"]["id"],
                        "league":    m.get("competition", {}).get("name", ""),
                        "date":      m.get("utcDate", "")[:10],
                        "heure":     m.get("utcDate", "")[:16].replace("T", " ") + " UTC",
                        "home_logo": team_ids.get(home_name, {}).get("logo", ""),
                        "away_logo": team_ids.get(away_name, {}).get("logo", ""),
                    })
        time.sleep(1)

    if not matchs_periode:
        return jsonify({"matches": [], "total": 0, "date_from": date_from, "date_to": date_to})

    resultats = []
    for i, match in enumerate(matchs_periode):
        h_id = match["home_id"]
        a_id = match["away_id"]
        try:
            if i > 0 and i % 5 == 0:
                time.sleep(8)

            home_matches = get_recent_matches(h_id, match["home"], venue="home")
            away_matches = get_recent_matches(a_id, match["away"], venue="away")
            h2h_matches  = get_head_to_head(h_id, a_id, match["home"], match["away"])

            if not home_matches and not away_matches:
                continue

            home_stats = calculer_stats_equipe(home_matches, h_id, venue="home")
            away_stats = calculer_stats_equipe(away_matches, a_id, venue="away")
            h2h_stats  = calculer_stats_h2h(h2h_matches, h_id, a_id)
            pred       = predire_match(home_stats, away_stats, h2h_stats=h2h_stats)

            resultats.append({
                "home":           match["home"],
                "away":           match["away"],
                "home_logo":      match["home_logo"],
                "away_logo":      match["away_logo"],
                "league":         match["league"],
                "date":           match["date"],
                "heure":          match["heure"],
                "p_over25":       pred["p_over25"],
                "p_under25":      pred["p_under25"],
                "score_probable": pred["score_probable"],
                "prob_score":     pred["prob_score"],
                "buts_total":     pred["buts_total"],
                "buts_dom":       pred["buts_dom"],
                "buts_ext":       pred["buts_ext"],
            })
        except Exception as e:
            print(f"  ❌ Erreur : {e}")
            continue

    resultats.sort(key=lambda r: r["p_over25"], reverse=True)
    top8 = resultats[:8]

    response = {"matches": top8, "total": len(resultats), "date_from": date_from, "date_to": date_to}
    _top25_cache = {"day_key": day_key, "data": response}
    return jsonify(response)


# ═══════════════════════════════════════
#  ROUTES ADMIN
# ═══════════════════════════════════════

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    body = request.get_json() or {}
    pwd  = (body.get("password") or "").strip()
    if pwd == ADMIN_PASSWORD:
        session["admin_ok"] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Mot de passe incorrect."}), 401


@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    session.pop("admin_ok", None)
    return jsonify({"success": True})


@app.route('/api/admin/stats')
@admin_requis
def admin_stats():
    return jsonify(stats_codes())


@app.route('/api/admin/codes')
@admin_requis
def admin_codes():
    codes  = lister_codes()
    result = []
    for c in codes:
        result.append({
            "code":       c["code"],
            "section":    c["section"],
            "expires_at": c["expires_at"],
            "is_active":  c["is_active"],
            "created_at":  c["created_at"],
            "usage_count": c.get("usage_count", 0),
            "note":        c.get("note", ""),
        })
    return jsonify({"codes": result})


@app.route('/api/admin/codes/create', methods=['POST'])
@admin_requis
def admin_create_codes():
    body        = request.get_json() or {}
    section     = body.get("section", "all")
    jours       = int(body.get("jours", 7))
    custom_code = (body.get("custom_code") or "").strip().upper()

    if custom_code:
        if len(custom_code) < 3:
            return jsonify({"success": False, "error": "Le code doit faire au moins 3 caractères."}), 400
        result = creer_code(section, jours, custom_code=custom_code)
        if result is None:
            return jsonify({"success": False, "error": "Ce code existe déjà."}), 409
        return jsonify({"success": True, "codes": [result]})

    prefix = (body.get("prefix", "VIP") or "VIP")[:4].upper()
    nb     = min(int(body.get("nb", 1)), 20)
    codes  = creer_plusieurs_codes(section, jours, prefix, nb)
    return jsonify({"success": True, "codes": codes})


@app.route('/api/admin/codes/revoke', methods=['POST'])
@admin_requis
def admin_revoke_code():
    body = request.get_json() or {}
    code = (body.get("code") or "").strip().upper()
    if not code:
        return jsonify({"success": False, "error": "Code manquant."}), 400
    revoquer_code(code)
    return jsonify({"success": True})


@app.route('/api/admin/codes/reactivate', methods=['POST'])
@admin_requis
def admin_reactivate_code():
    body = request.get_json() or {}
    code = (body.get("code") or "").strip().upper()
    if not code:
        return jsonify({"success": False, "error": "Code manquant."}), 400
    ok = reactiver_code(code)
    if ok:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Code introuvable."}), 404


@app.route('/api/admin/codes/delete', methods=['POST'])
@admin_requis
def admin_delete_code():
    body = request.get_json() or {}
    code = (body.get("code") or "").strip().upper()
    if not code:
        return jsonify({"success": False, "error": "Code manquant."}), 400
    ok = supprimer_code(code)
    if ok:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Code introuvable."}), 404


@app.route('/api/admin/codes/extend', methods=['POST'])
@admin_requis
def admin_extend_code():
    body  = request.get_json() or {}
    code  = (body.get("code") or "").strip().upper()
    jours = int(body.get("jours", 7))
    if not code:
        return jsonify({"success": False, "error": "Code manquant."}), 400
    ok = prolonger_code(code, jours)
    if ok:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Code introuvable."}), 404


@app.route('/api/admin/refresh-top25', methods=['POST'])
@admin_requis
def admin_refresh_top25():
    global _top25_cache
    _top25_cache = {"day_key": None, "data": None}
    return jsonify({"success": True, "message": "Cache Top 2.5 réinitialisé."})


@app.route('/api/admin/refresh-teams', methods=['POST'])
@admin_requis
def admin_refresh_teams():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    return jsonify({"success": True, "message": "Cache équipes supprimé, rechargement au prochain appel."})


# ═══════════════════════════════════════
#  LANCEMENT
# ═══════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"\n{'='*60}")
    print(f"🚀 PredictFoot démarre sur http://localhost:{port}")
    print(f"{'='*60}\n")
    app.run(host="0.0.0.0", port=port, debug=True)