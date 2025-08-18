from flask import Flask, render_template, request, jsonify
import requests
import json
import os
from datetime import datetime, timedelta

app = Flask(__name__)

# Configuration de l'API
API_TOKEN = os.getenv("API_TOKEN", "c67e9f5362d54bcdb5042f6f3e2ec0c2")  # Clé depuis variable d'environnement
BASE_URL = "http://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_TOKEN}

# Compétitions à inclure (codes de Football-Data.org)
COMPETITIONS = [
    {"code": "PL", "name": "Premier League"},  # Angleterre
    {"code": "FL1", "name": "Ligue 1"},        # France
    {"code": "BL1", "name": "Bundesliga"},     # Allemagne
    {"code": "SA", "name": "Serie A"},         # Italie
    {"code": "PD", "name": "La Liga"},         # Espagne
    {"code": "CL", "name": "UEFA Champions League"},  # Europe
    # Ajoute d'autres compétitions si besoin (ex. : "PPL" pour Portugal, "ELC" pour Championship)
]

# Chemin pour le cache des équipes
TEAMS_CACHE_FILE = "teams_cache.json"
CACHE_DURATION = timedelta(days=7)  # Mettre à jour toutes les semaines

def fetch_teams_from_api(competition_code):
    """Récupère les équipes d'une compétition via l'API."""
    url = f"{BASE_URL}/competitions/{competition_code}/teams"
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        data = response.json()
        teams = {
            team["name"]: {
                "id": team["id"],
                "logo": team.get("crest", "https://via.placeholder.com/50")
            } for team in data["teams"]
        }
        return teams
    except requests.RequestException as e:
        print(f"Erreur lors de la récupération des équipes pour {competition_code}: {e}")
        return {}

def update_teams_cache():
    """Met à jour le cache des équipes pour toutes les compétitions."""
    all_teams = {}
    league_teams = {}
    for comp in COMPETITIONS:
        teams = fetch_teams_from_api(comp["code"])
        if teams:
            league_teams[comp["name"]] = sorted(teams.keys())  # Trier par nom
            all_teams.update(teams)
    
    # Sauvegarder dans le cache
    cache_data = {
        "teams": all_teams,
        "league_teams": league_teams,
        "last_updated": datetime.now().isoformat()
    }
    with open(TEAMS_CACHE_FILE, "w") as f:
        json.dump(cache_data, f, indent=2)
    return all_teams, league_teams

def get_teams():
    """Récupère les équipes depuis le cache ou l'API."""
    if os.path.exists(TEAMS_CACHE_FILE):
        with open(TEAMS_CACHE_FILE, "r") as f:
            cache_data = json.load(f)
        last_updated = datetime.fromisoformat(cache_data["last_updated"])
        if datetime.now() - last_updated < CACHE_DURATION:
            return cache_data["teams"], cache_data["league_teams"]
    
    # Si le cache est obsolète ou n'existe pas, mettre à jour
    return update_teams_cache()

def get_team_matches(team_id):
    """Récupère les matchs récents d'une équipe."""
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
    """Récupère les matchs pertinents pour deux équipes."""
    home_matches = get_team_matches(team_ids[home_team]["id"])
    away_matches = get_team_matches(team_ids[away_team]["id"])
    head_to_head = [match for match in home_matches if match["awayTeam"]["id"] == team_ids[away_team]["id"]]
    return head_to_head + home_matches[:5] + away_matches[:5]

def get_team_stats(matches, team_id):
    """Calcule les statistiques d'une équipe."""
    if not matches:
        return {"goals_avg_scored": 0, "goals_avg_conceded": 0, "half_time_win_rate": 0, "second_half_win_rate": 0, "both_teams_score_rate": 0}
    goals_scored = goals_conceded = half_time_wins = second_half_wins = both_teams_score = 0
    games = len(matches)
    for match in matches:
        home_team_id = match["homeTeam"]["id"]
        away_team_id = match["awayTeam"]["id"]
        home_goals = match["score"]["fullTime"]["home"] or 0
        away_goals = match["score"]["fullTime"]["away"] or 0
        home_half = match["score"]["halfTime"]["home"] or 0
        away_half = match["score"]["halfTime"]["away"] or 0
        home_second = home_goals - home_half
        away_second = away_goals - away_half
        if home_team_id == team_id:
            goals_scored += home_goals
            goals_conceded += away_goals
            half_time_wins += 1 if home_half > away_half else 0
            second_half_wins += 1 if home_second > away_second else 0
            both_teams_score += 1 if home_goals > 0 and away_goals > 0 else 0
        elif away_team_id == team_id:
            goals_scored += away_goals
            goals_conceded += home_goals
            half_time_wins += 1 if away_half > home_half else 0
            second_half_wins += 1 if home_second > away_second else 0
            both_teams_score += 1 if home_goals > 0 and away_goals > 0 else 0
    return {
        "goals_avg_scored": round(goals_scored / max(1, games), 2),
        "goals_avg_conceded": round(goals_conceded / max(1, games), 2),
        "half_time_win_rate": round(half_time_wins / max(1, games), 2),
        "second_half_win_rate": round(second_half_wins / max(1, games), 2),
        "both_teams_score_rate": round(both_teams_score / max(1, games), 2)
    }

def predict_result(home_team, away_team, matches, team_ids):
    """Prédit le résultat 1X2."""
    home_stats = get_team_stats(matches, team_ids[home_team]["id"])
    away_stats = get_team_stats(matches, team_ids[away_team]["id"])
    home_strength = home_stats["goals_avg_scored"] + 1
    away_strength = away_stats["goals_avg_scored"] + 1
    total = home_strength + away_strength + 1
    probas = {"1": home_strength / total, "X": 1 / total, "2": away_strength / total}
    return max(probas, key=probas.get)

def predict_double_chance(home_team, away_team, matches, team_ids):
    """Prédit la double chance."""
    home_stats = get_team_stats(matches, team_ids[home_team]["id"])
    away_stats = get_team_stats(matches, team_ids[away_team]["id"])
    home_strength = home_stats["goals_avg_scored"] + 1
    away_strength = away_stats["goals_avg_scored"] + 1
    total = home_strength + away_strength + 1
    probas = {"1X": home_strength / total + 1 / total, "X2": 1 / total + away_strength / total, "12": home_strength / total + away_strength / total}
    return max(probas, key=probas.get)

def predict_goals(home_team, away_team, matches, team_ids):
    """Prédit le nombre total de buts."""
    home_stats = get_team_stats(matches, team_ids[home_team]["id"])
    away_stats = get_team_stats(matches, team_ids[away_team]["id"])
    total_goals = (home_stats["goals_avg_scored"] + away_stats["goals_avg_conceded"] +
                   away_stats["goals_avg_scored"] + home_stats["goals_avg_conceded"]) / 2
    return round(total_goals) or 1

def predict_over_under_2_5(home_team, away_team, matches, team_ids):
    """Prédit plus/moins de 2.5 buts."""
    total_goals = predict_goals(home_team, away_team, matches, team_ids)
    return "Plus de 2.5 buts" if total_goals > 2.5 else "Moins de 2.5 buts"

def predict_both_teams_score(home_team, away_team, matches, team_ids):
    """Prédit si les deux équipes marquent."""
    home_stats = get_team_stats(matches, team_ids[home_team]["id"])
    away_stats = get_team_stats(matches, team_ids[away_team]["id"])
    bts_rate = (home_stats["both_teams_score_rate"] + away_stats["both_teams_score_rate"]) / 2
    return "Oui" if bts_rate > 0.5 else "Non"

def predict_exact_score(home_team, away_team, matches, team_ids):
    """Prédit le score exact."""
    home_stats = get_team_stats(matches, team_ids[home_team]["id"])
    away_stats = get_team_stats(matches, team_ids[away_team]["id"])
    return f"{round(home_stats['goals_avg_scored']) or 1}-{round(away_stats['goals_avg_scored']) or 1}"

def predict_half_time_winner(home_team, away_team, matches, team_ids):
    """Prédit le vainqueur à la mi-temps."""
    home_stats = get_team_stats(matches, team_ids[home_team]["id"])
    away_stats = get_team_stats(matches, team_ids[away_team]["id"])
    home_proba = 1 - (1 - home_stats["half_time_win_rate"]) * (1 - home_stats["second_half_win_rate"])
    away_proba = 1 - (1 - away_stats["half_time_win_rate"]) * (1 - away_stats["second_half_win_rate"])
    total = home_proba + away_proba + 0.1
    probas = {"1": home_proba / total, "X": 0.1 / total, "2": away_proba / total}
    return max(probas, key=probas.get)

@app.route('/', methods=['GET', 'POST'])
def index():
    team_ids, league_teams = get_teams()
    teams = sorted(team_ids.keys())
    predictions = None
    home_team = away_team = None
    home_logo = away_logo = "https://via.placeholder.com/50"
    error = None
    home_stats = away_stats = None
    is_vip = False  # À remplacer par une vraie logique d'authentification

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
                predictions = {
                    "result": predict_result(home_team, away_team, historical_matches, team_ids),
                    "double_chance": predict_double_chance(home_team, away_team, historical_matches, team_ids),
                    "goals": predict_goals(home_team, away_team, historical_matches, team_ids),
                    "exact_score": predict_exact_score(home_team, away_team, historical_matches, team_ids),
                    "half_winner": predict_half_time_winner(home_team, away_team, historical_matches, team_ids),
                    "over_under": predict_over_under_2_5(home_team, away_team, historical_matches, team_ids),
                    "both_teams_score": predict_both_teams_score(home_team, away_team, historical_matches, team_ids)
                }
            else:
                predictions = "no_data"
                error = "Pas assez de données historiques pour ce match."

    return render_template('index.html', teams=teams, predictions=predictions, home_team=home_team,
                           away_team=away_team, home_logo=home_logo, away_logo=away_logo, error=error,
                           home_stats=home_stats, away_stats=away_stats, is_vip=is_vip, league_teams=league_teams)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
