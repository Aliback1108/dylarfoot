from flask import Flask, render_template, request, jsonify
import requests
from datetime import datetime, timedelta

app = Flask(__name__)

API_TOKEN = "c67e9f5362d54bcdb5042f6f3e2ec0c2"  # Remplace par ta clé Football-Data.org
BASE_URL = "http://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_TOKEN}
TEAM_IDS = {
    "Manchester City": 65, "Liverpool": 64, "Paris Saint-Germain": 524, "Real Madrid": 86,
    "Chelsea": 61, "Arsenal": 57, "Brentford": 402, "Ipswich Town": 349, "Club Brugge": 851,
    "Nottingham Forest": 351, "Lille": 521, "PSV": 674, "Barcelona": 81, "Atlético Madrid": 78,
    "Inter Milan": 108, "Lazio": 110, "Angers SCO": 556, "Stade de Reims": 547,
    "Brighton & Hove Albion": 397, "Fulham": 63, "AFC Bournemouth": 1044,
    "Wolverhampton Wanderers": 76, "Crystal Palace": 354, "Aston Villa": 58,
    "Southampton": 340, "Bayern Munich": 5, "Benfica": 503, "Manchester United": 66,
    "Tottenham Hotspur": 73, "Juventus": 109, "AC Milan": 98, "Napoli": 113,
    "AS Roma": 100, "Borussia Dortmund": 4, "RB Leipzig": 721, "Porto": 497, "Ajax": 678,
    "Real Sociedad": 92, "Getafe": 82, "Newcastle United": 67, "Club Deportivo Leganés": 745,
    "Leicester City": 338, "Everton": 62, "West Ham United": 563, "Valencia": 95,
    "Sevilla": 559, "Bayer Leverkusen": 3, "Atalanta": 102, "Fiorentina": 99,
    "Sporting CP": 498, "Villarreal": 94
}

def get_team_logo(team_id):
    url = f"{BASE_URL}/teams/{team_id}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json().get("crest", "")
    return ""

def get_team_matches(team_id):
    url = f"{BASE_URL}/teams/{team_id}/matches"
    params = {
        "status": "FINISHED",
        "dateFrom": (datetime.today() - timedelta(days=90)).strftime('%Y-%m-%d'),
        "dateTo": datetime.today().strftime('%Y-%m-%d'),
        "limit": 10
    }
    response = requests.get(url, headers=HEADERS, params=params)
    return response.json()["matches"] if response.status_code == 200 else []

def get_relevant_matches(home_team, away_team):
    home_matches = get_team_matches(TEAM_IDS[home_team])
    away_matches = get_team_matches(TEAM_IDS[away_team])
    head_to_head = [match for match in home_matches if match["awayTeam"]["id"] == TEAM_IDS[away_team]]
    return head_to_head + home_matches[:5] + away_matches[:5]

def get_team_stats(matches, team_id):
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

def predict_result(home_team, away_team, matches):
    home_stats = get_team_stats(matches, TEAM_IDS[home_team])
    away_stats = get_team_stats(matches, TEAM_IDS[away_team])
    home_strength = home_stats["goals_avg_scored"] + 1
    away_strength = away_stats["goals_avg_scored"] + 1
    total = home_strength + away_strength + 1
    probas = {"1": home_strength / total, "X": 1 / total, "2": away_strength / total}
    return max(probas, key=probas.get)

def predict_double_chance(home_team, away_team, matches):
    home_stats = get_team_stats(matches, TEAM_IDS[home_team])
    away_stats = get_team_stats(matches, TEAM_IDS[away_team])
    home_strength = home_stats["goals_avg_scored"] + 1
    away_strength = away_stats["goals_avg_scored"] + 1
    total = home_strength + away_strength + 1
    probas = {"1X": home_strength / total + 1 / total, "X2": 1 / total + away_strength / total, "12": home_strength / total + away_strength / total}
    return max(probas, key=probas.get)

def predict_goals(home_team, away_team, matches):
    home_stats = get_team_stats(matches, TEAM_IDS[home_team])
    away_stats = get_team_stats(matches, TEAM_IDS[away_team])
    total_goals = (home_stats["goals_avg_scored"] + away_stats["goals_avg_conceded"] +
                   away_stats["goals_avg_scored"] + home_stats["goals_avg_conceded"]) / 2
    return round(total_goals) or 1

def predict_over_under_2_5(home_team, away_team, matches):
    total_goals = predict_goals(home_team, away_team, matches)
    return "Plus de 2.5 buts" if total_goals > 2.5 else "Moins de 2.5 buts"

def predict_both_teams_score(home_team, away_team, matches):
    home_stats = get_team_stats(matches, TEAM_IDS[home_team])
    away_stats = get_team_stats(matches, TEAM_IDS[away_team])
    bts_rate = (home_stats["both_teams_score_rate"] + away_stats["both_teams_score_rate"]) / 2
    return "Oui" if bts_rate > 0.5 else "Non"

def predict_exact_score(home_team, away_team, matches):
    home_stats = get_team_stats(matches, TEAM_IDS[home_team])
    away_stats = get_team_stats(matches, TEAM_IDS[away_team])
    return f"{round(home_stats['goals_avg_scored']) or 1}-{round(away_stats['goals_avg_scored']) or 1}"

def predict_half_time_winner(home_team, away_team, matches):
    home_stats = get_team_stats(matches, TEAM_IDS[home_team])
    away_stats = get_team_stats(matches, TEAM_IDS[away_team])
    home_proba = 1 - (1 - home_stats["half_time_win_rate"]) * (1 - home_stats["second_half_win_rate"])
    away_proba = 1 - (1 - away_stats["half_time_win_rate"]) * (1 - away_stats["second_half_win_rate"])
    total = home_proba + away_proba + 0.1
    probas = {"1": home_proba / total, "X": 0.1 / total, "2": away_proba / total}
    return max(probas, key=probas.get)

@app.route('/', methods=['GET', 'POST'])
def index():
    teams = sorted(TEAM_IDS.keys())
    predictions = None
    home_team = away_team = None
    home_logo = away_logo = ""
    error = None
    home_stats = away_stats = None
    # Simuler un statut VIP (à remplacer par une vraie logique d'authentification)
    is_vip = False  # Change à True pour tester la version VIP

    if request.method == 'POST':
        home_team = request.form['home_team']
        away_team = request.form['away_team']
        if home_team == away_team:
            error = "Veuillez sélectionner deux équipes différentes."
        elif home_team in TEAM_IDS and away_team in TEAM_IDS:
            home_logo = get_team_logo(TEAM_IDS[home_team])
            away_logo = get_team_logo(TEAM_IDS[away_team])
            historical_matches = get_relevant_matches(home_team, away_team)
            if historical_matches:
                home_stats = get_team_stats(historical_matches, TEAM_IDS[home_team])
                away_stats = get_team_stats(historical_matches, TEAM_IDS[away_team])
                result = predict_result(home_team, away_team, historical_matches)
                double_chance = predict_double_chance(home_team, away_team, historical_matches)
                goals = predict_goals(home_team, away_team, historical_matches)
                exact_score = predict_exact_score(home_team, away_team, historical_matches)
                half_winner = predict_half_time_winner(home_team, away_team, historical_matches)
                over_under = predict_over_under_2_5(home_team, away_team, historical_matches)
                both_teams_score = predict_both_teams_score(home_team, away_team, historical_matches)
                predictions = {
                    "result": result,
                    "double_chance": double_chance,
                    "goals": goals,
                    "exact_score": exact_score,
                    "half_winner": half_winner,
                    "over_under": over_under,
                    "both_teams_score": both_teams_score
                }
            else:
                predictions = "no_data"

    return render_template('index.html', teams=teams, predictions=predictions, home_team=home_team, away_team=away_team, home_logo=home_logo, away_logo=away_logo, error=error, home_stats=home_stats, away_stats=away_stats, is_vip=is_vip)

if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)