from flask import Flask, render_template, request, jsonify
import requests
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from scipy.stats import poisson
from dotenv import load_dotenv

load_dotenv()  # Charge les variables depuis .env

app = Flask(__name__)

# ─── Configuration API Football ───
API_TOKEN = os.getenv("API_TOKEN", "c67e9f5362d54bcdb5042f6f3e2ec0c2")
BASE_URL = "http://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_TOKEN}

# ─── Configuration Email ───
MAIL_USER     = os.getenv("MAIL_USER")
MAIL_PASS     = os.getenv("MAIL_PASS")
MAIL_RECEIVER = os.getenv("MAIL_RECEIVER", MAIL_USER)  # Par défaut s'envoie à soi-même

# Compétitions
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

TEAMS_CACHE_FILE = "teams_cache.json"
CACHE_DURATION   = timedelta(days=2)


# ─────────────────────────────────────────
#  FONCTIONS ÉQUIPES / CACHE
# ─────────────────────────────────────────

def fetch_teams_from_api(competition_code):
    url = f"{BASE_URL}/competitions/{competition_code}/teams"
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        data = response.json()
        return {
            team["name"]: {
                "id": team["id"],
                "logo": team.get("crest", "https://via.placeholder.com/50")
            } for team in data["teams"]
        }
    except requests.RequestException as e:
        print(f"Erreur équipes {competition_code}: {e}")
        return {}

def update_teams_cache():
    all_teams = {}
    league_teams = {}
    for comp in COMPETITIONS:
        teams = fetch_teams_from_api(comp["code"])
        if teams:
            league_teams[comp["name"]] = sorted(teams.keys())
            all_teams.update(teams)
    cache_data = {
        "teams": all_teams,
        "league_teams": league_teams,
        "last_updated": datetime.now().isoformat()
    }
    with open(TEAMS_CACHE_FILE, "w") as f:
        json.dump(cache_data, f, indent=2)
    return all_teams, league_teams

def get_teams():
    if os.path.exists(TEAMS_CACHE_FILE):
        with open(TEAMS_CACHE_FILE, "r") as f:
            cache_data = json.load(f)
        last_updated = datetime.fromisoformat(cache_data["last_updated"])
        if datetime.now() - last_updated < CACHE_DURATION:
            return cache_data["teams"], cache_data["league_teams"]
    return update_teams_cache()


# ─────────────────────────────────────────
#  FONCTIONS PRÉDICTION
# ─────────────────────────────────────────

def get_team_matches(team_id):
    url = f"{BASE_URL}/teams/{team_id}/matches"
    params = {
        "status": "FINISHED",
        "dateFrom": (datetime.today() - timedelta(days=90)).strftime('%Y-%m-%d'),
        "dateTo": datetime.today().strftime('%Y-%m-%d'),
        "limit": 10
    }
    try:
        response = requests.get(url, headers=HEADERS, params=params)
        response.raise_for_status()
        return response.json()["matches"]
    except requests.RequestException:
        return []

def get_relevant_matches(home_team, away_team, team_ids):
    home_matches = get_team_matches(team_ids[home_team]["id"])
    away_matches = get_team_matches(team_ids[away_team]["id"])
    head_to_head = [m for m in home_matches if m["awayTeam"]["id"] == team_ids[away_team]["id"]]
    return head_to_head + home_matches[:5] + away_matches[:5]

def get_team_stats(matches, team_id):
    if not matches:
        return {"goals_avg_scored": 0, "goals_avg_conceded": 0,
                "half_time_win_rate": 0, "second_half_win_rate": 0, "both_teams_score_rate": 0}
    goals_scored = goals_conceded = half_time_wins = second_half_wins = both_teams_score = 0
    games = len(matches)
    for match in matches:
        hid = match["homeTeam"]["id"]
        aid = match["awayTeam"]["id"]
        hg  = match["score"]["fullTime"]["home"] or 0
        ag  = match["score"]["fullTime"]["away"] or 0
        hh  = match["score"]["halfTime"]["home"] or 0
        ah  = match["score"]["halfTime"]["away"] or 0
        hs  = hg - hh
        as_ = ag - ah
        if hid == team_id:
            goals_scored   += hg
            goals_conceded += ag
            half_time_wins += 1 if hh > ah else 0
            second_half_wins += 1 if hs > as_ else 0
            both_teams_score += 1 if hg > 0 and ag > 0 else 0
        elif aid == team_id:
            goals_scored   += ag
            goals_conceded += hg
            half_time_wins += 1 if ah > hh else 0
            second_half_wins += 1 if hs > as_ else 0
            both_teams_score += 1 if hg > 0 and ag > 0 else 0
    return {
        "goals_avg_scored":    round(goals_scored   / max(1, games), 2),
        "goals_avg_conceded":  round(goals_conceded / max(1, games), 2),
        "half_time_win_rate":  round(half_time_wins / max(1, games), 2),
        "second_half_win_rate":round(second_half_wins / max(1, games), 2),
        "both_teams_score_rate":round(both_teams_score / max(1, games), 2)
    }

def predict_result(home_team, away_team, matches, team_ids):
    hs = get_team_stats(matches, team_ids[home_team]["id"])
    as_ = get_team_stats(matches, team_ids[away_team]["id"])
    h = hs["goals_avg_scored"] + 1
    a = as_["goals_avg_scored"] + 1
    t = h + a + 1
    p = {"V1": h/t, "X": 1/t, "V2": a/t}
    return max(p, key=p.get)

def predict_double_chance(home_team, away_team, matches, team_ids):
    hs = get_team_stats(matches, team_ids[home_team]["id"])
    as_ = get_team_stats(matches, team_ids[away_team]["id"])
    h = hs["goals_avg_scored"] + 1
    a = as_["goals_avg_scored"] + 1
    t = h + a + 1
    p = {"1X": h/t + 1/t, "X2": 1/t + a/t, "12": h/t + a/t}
    return max(p, key=p.get)

def predict_goals(home_team, away_team, matches, team_ids):
    hs = get_team_stats(matches, team_ids[home_team]["id"])
    as_ = get_team_stats(matches, team_ids[away_team]["id"])
    hg = poisson.mean(hs["goals_avg_scored"] * as_["goals_avg_conceded"])
    ag = poisson.mean(as_["goals_avg_scored"] * hs["goals_avg_conceded"])
    return round(hg + ag, 2), hg, ag

def predict_over_under_2_5(home_team, away_team, matches, team_ids):
    total, hg, ag = predict_goals(home_team, away_team, matches, team_ids)
    prob_over = 1 - poisson.cdf(2.5, total)
    return ("Plus de 2.5 buts" if prob_over > 0.5 else "Moins de 2.5 buts"), round(prob_over * 100, 2)

def predict_both_teams_score(home_team, away_team, matches, team_ids):
    total, hg, ag = predict_goals(home_team, away_team, matches, team_ids)
    prob_btts = 1 - (poisson.pmf(0, hg) * poisson.pmf(0, ag))
    return ("Oui" if prob_btts > 0.5 else "Non"), round(prob_btts * 100, 2)

def predict_exact_score(home_team, away_team, matches, team_ids):
    total, hg, ag = predict_goals(home_team, away_team, matches, team_ids)
    return f"{int(round(hg, 0))}-{int(round(ag, 0))}"

def predict_half_time_winner(home_team, away_team, matches, team_ids):
    hs = get_team_stats(matches, team_ids[home_team]["id"])
    as_ = get_team_stats(matches, team_ids[away_team]["id"])
    hp = hs["half_time_win_rate"]
    ap = as_["half_time_win_rate"]
    t = hp + ap + 0.1
    p = {"Equipe 1": hp/t, "X": 0.1/t, "Equipe 2": ap/t}
    return max(p, key=p.get)


# ─────────────────────────────────────────
#  FONCTION ENVOI EMAIL
# ─────────────────────────────────────────

def send_contact_email(name, email, subject, message):
    """Envoie un email de contact via Gmail SMTP."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[DilarFoot Contact] {subject}"
        msg["From"]    = MAIL_USER
        msg["To"]      = MAIL_RECEIVER
        msg["Reply-To"] = email  # Répondre directement à l'expéditeur

        # Corps HTML de l'email
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #0D1117; color: #F0F6FC; border-radius: 12px; overflow: hidden;">
            <div style="background: #161B22; padding: 24px 32px; border-bottom: 2px solid #00FF87;">
                <h2 style="margin: 0; color: #00FF87; font-size: 1.2rem;">📩 Nouveau message — DilarFoot</h2>
            </div>
            <div style="padding: 28px 32px;">
                <table style="width: 100%; border-collapse: collapse;">
                    <tr>
                        <td style="padding: 10px 0; color: #8B949E; font-size: 0.85rem; width: 100px;">Nom</td>
                        <td style="padding: 10px 0; color: #F0F6FC; font-weight: bold;">{name}</td>
                    </tr>
                    <tr style="border-top: 1px solid #1F2937;">
                        <td style="padding: 10px 0; color: #8B949E; font-size: 0.85rem;">Email</td>
                        <td style="padding: 10px 0;"><a href="mailto:{email}" style="color: #00FF87;">{email}</a></td>
                    </tr>
                    <tr style="border-top: 1px solid #1F2937;">
                        <td style="padding: 10px 0; color: #8B949E; font-size: 0.85rem;">Sujet</td>
                        <td style="padding: 10px 0; color: #F0F6FC;">{subject}</td>
                    </tr>
                    <tr style="border-top: 1px solid #1F2937;">
                        <td style="padding: 10px 0; color: #8B949E; font-size: 0.85rem; vertical-align: top;">Message</td>
                        <td style="padding: 10px 0; color: #F0F6FC; line-height: 1.6;">{message.replace(chr(10), '<br>')}</td>
                    </tr>
                </table>
            </div>
            <div style="background: #161B22; padding: 16px 32px; text-align: center; font-size: 0.75rem; color: #484F58;">
                Envoyé via le formulaire de contact DilarFoot · {datetime.now().strftime('%d/%m/%Y à %H:%M')}
            </div>
        </div>
        """

        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(MAIL_USER, MAIL_PASS)
            server.sendmail(MAIL_USER, MAIL_RECEIVER, msg.as_string())

        return True, "Message envoyé avec succès."

    except smtplib.SMTPAuthenticationError:
        return False, "Erreur d'authentification Gmail. Vérifiez le mot de passe d'application."
    except smtplib.SMTPException as e:
        return False, f"Erreur SMTP : {str(e)}"
    except Exception as e:
        return False, f"Erreur inattendue : {str(e)}"


# ─────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────

@app.route('/', methods=['GET', 'POST'])
def index():
    team_ids, league_teams = get_teams()
    teams = sorted(team_ids.keys())
    predictions = None
    home_team = away_team = None
    home_logo = away_logo = "https://via.placeholder.com/50"
    error = None
    home_stats = away_stats = None
    is_vip = False

    if request.method == 'POST':
        home_team = request.form['home_team']
        away_team = request.form['away_team']
        if home_team == away_team:
            error = "Veuillez sélectionner deux équipes différentes."
        elif home_team in team_ids and away_team in team_ids:
            home_logo = team_ids[home_team]["logo"]
            away_logo = team_ids[away_team]["logo"]
            historical_matches = get_relevant_matches(home_team, away_team, team_ids)
            if historical_matches:
                home_stats = get_team_stats(historical_matches, team_ids[home_team]["id"])
                away_stats = get_team_stats(historical_matches, team_ids[away_team]["id"])
                total_goals, hg, ag = predict_goals(home_team, away_team, historical_matches, team_ids)
                over_under, over_prob = predict_over_under_2_5(home_team, away_team, historical_matches, team_ids)
                btts, btts_prob = predict_both_teams_score(home_team, away_team, historical_matches, team_ids)
                predictions = {
                    "result":           predict_result(home_team, away_team, historical_matches, team_ids),
                    "double_chance":    predict_double_chance(home_team, away_team, historical_matches, team_ids),
                    "goals":            f"{total_goals} buts (intervalle : {int(total_goals - 1)}-{int(total_goals + 1)})",
                    "exact_score":      predict_exact_score(home_team, away_team, historical_matches, team_ids),
                    "half_winner":      predict_half_time_winner(home_team, away_team, historical_matches, team_ids),
                    "over_under":       f"{over_under} ({over_prob}%)",
                    "both_teams_score": f"{btts} ({btts_prob}%)"
                }
            else:
                predictions = "no_data"
                error = "Pas assez de données historiques pour ce match."

    return render_template('index.html', teams=teams, predictions=predictions, home_team=home_team,
                           away_team=away_team, home_logo=home_logo, away_logo=away_logo, error=error,
                           home_stats=home_stats, away_stats=away_stats, is_vip=is_vip, league_teams=league_teams)


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/contact')
def contact():
    return render_template('contact.html')


@app.route('/send-contact', methods=['POST'])
def send_contact():
    """Route AJAX appelée par le formulaire de contact."""
    data = request.get_json()

    name    = (data.get('name', '')    or '').strip()
    email   = (data.get('email', '')   or '').strip()
    subject = (data.get('subject', '') or '').strip()
    message = (data.get('message', '') or '').strip()

    # Validation serveur
    if not all([name, email, subject, message]):
        return jsonify({"success": False, "error": "Tous les champs sont obligatoires."}), 400

    if '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({"success": False, "error": "Adresse email invalide."}), 400

    success, msg = send_contact_email(name, email, subject, message)

    if success:
        return jsonify({"success": True, "message": "Message envoyé avec succès !"}), 200
    else:
        return jsonify({"success": False, "error": msg}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)