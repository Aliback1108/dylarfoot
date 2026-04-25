# prediction.py — Moteur de prédiction DilarFoot (Dixon-Coles + H2H + venue)
import math
import numpy as np
from scipy.stats import poisson


def poids_temporel(date_str, xi=0.002):
    """
    Pondération temporelle : les matchs récents comptent plus.
    Match d'hier   → poids ≈ 0.998
    Match 6 mois   → poids ≈ 0.70
    """
    from datetime import datetime
    try:
        date_match = datetime.strptime(date_str[:10], "%Y-%m-%d")
        jours = (datetime.today() - date_match).days
        return math.exp(-xi * jours)
    except Exception:
        return 1.0


def calculer_stats_equipe(matches, team_id, venue=None):
    """
    Calcule attaque/défense avec pondération temporelle.
    venue = 'home'  → matchs à domicile uniquement
    venue = 'away'  → matchs à l'extérieur uniquement
    venue = None    → tous les matchs
    """
    buts_marques   = 0.0
    buts_encaisse  = 0.0
    poids_total    = 0.0
    forme          = []
    matchs_comptes = 0

    matchs_tries = sorted(
        matches,
        key=lambda m: m.get("utcDate", ""),
        reverse=True
    )

    for match in matchs_tries:
        score = match.get("score", {})
        ft    = score.get("fullTime", {})
        hg    = ft.get("home") or 0
        ag    = ft.get("away") or 0

        date_str  = match.get("utcDate", "")
        poids     = poids_temporel(date_str)
        home_id_m = match.get("homeTeam", {}).get("id")
        away_id_m = match.get("awayTeam", {}).get("id")

        est_domicile  = (home_id_m == team_id)
        est_exterieur = (away_id_m == team_id)

        # Filtre venue
        if venue == "home" and not est_domicile:
            continue
        if venue == "away" and not est_exterieur:
            continue

        if est_domicile:
            buts_marques  += hg * poids
            buts_encaisse += ag * poids
            poids_total   += poids
            matchs_comptes += 1
            if len(forme) < 5:
                if hg > ag:    forme.append("W")
                elif hg == ag: forme.append("D")
                else:          forme.append("L")

        elif est_exterieur:
            buts_marques  += ag * poids
            buts_encaisse += hg * poids
            poids_total   += poids
            matchs_comptes += 1
            if len(forme) < 5:
                if ag > hg:    forme.append("W")
                elif ag == hg: forme.append("D")
                else:          forme.append("L")

    if poids_total == 0:
        print(f"    ⚠️  Aucun match [{venue or 'all'}] → stats par défaut (1.2/1.2)")
        return {"attaque": 1.2, "defense": 1.2, "forme": [], "nb_matchs": 0}

    attaque = round(buts_marques  / poids_total, 3)
    defense = round(buts_encaisse / poids_total, 3)

    print(f"    📊 Stats [{venue or 'all'}] sur {matchs_comptes} matchs"
          f" → attaque={attaque} | defense={defense} | forme={forme}")

    return {
        "attaque":   attaque,
        "defense":   defense,
        "forme":     forme,
        "nb_matchs": matchs_comptes
    }


def calculer_stats_h2h(h2h_matches, home_id, away_id):
    """
    Analyse les 6 dernières confrontations directes.
    Retourne les victoires/nuls/défaites, moyennes de buts
    et facteurs d'ajustement pour home_id.
    """
    print(f"  ⚔️  Analyse H2H ({len(h2h_matches)} matchs) :")

    if not h2h_matches:
        print("    ℹ️  Aucune confrontation directe → pas d'ajustement H2H")
        return {
            "nb_matchs":     0,
            "home_wins":     0,
            "draws":         0,
            "away_wins":     0,
            "home_buts_moy": 1.2,
            "away_buts_moy": 1.2,
            "home_adj":      1.0,
            "away_adj":      1.0,
        }

    home_wins = draws = away_wins = 0
    home_buts = away_buts = 0.0
    poids_total = 0.0

    for match in h2h_matches:
        ft        = match.get("score", {}).get("fullTime", {})
        hg        = ft.get("home") or 0
        ag        = ft.get("away") or 0
        date      = match.get("utcDate", "")[:10]
        poids     = poids_temporel(match.get("utcDate", ""), xi=0.001)
        m_home_id = match.get("homeTeam", {}).get("id")
        m_home    = match.get("homeTeam", {}).get("name", "?")
        m_away    = match.get("awayTeam", {}).get("name", "?")

        # Normaliser du point de vue de home_id
        if m_home_id == home_id:
            h_score, a_score = hg, ag
        else:
            h_score, a_score = ag, hg

        home_buts   += h_score * poids
        away_buts   += a_score * poids
        poids_total += poids

        if h_score > a_score:
            resultat = "✅ DOM gagne"
            home_wins += 1
        elif h_score == a_score:
            resultat = "🟡 Nul"
            draws += 1
        else:
            resultat = "❌ EXT gagne"
            away_wins += 1

        print(f"    📅 {date} — {m_home} {hg}-{ag} {m_away}"
              f"  (vue H2H: {h_score}-{a_score}) {resultat}")

    nb = len(h2h_matches)
    home_buts_moy = round(home_buts / poids_total, 2) if poids_total else 1.2
    away_buts_moy = round(away_buts / poids_total, 2) if poids_total else 1.2

    home_win_rate = home_wins / nb
    away_win_rate = away_wins / nb

    home_adj = round(max(0.85, min(1.15, 1.0 + (home_win_rate - 0.333) * 0.45)), 3)
    away_adj = round(max(0.85, min(1.15, 1.0 + (away_win_rate - 0.333) * 0.45)), 3)

    print(f"    📈 Résumé H2H : DOM {home_wins}V / {draws}N / {away_wins}D EXT")
    print(f"    🎯 Buts moy H2H : DOM={home_buts_moy} | EXT={away_buts_moy}")
    print(f"    ⚙️  Facteurs adj : home_adj={home_adj} | away_adj={away_adj}")

    return {
        "nb_matchs":     nb,
        "home_wins":     home_wins,
        "draws":         draws,
        "away_wins":     away_wins,
        "home_buts_moy": home_buts_moy,
        "away_buts_moy": away_buts_moy,
        "home_adj":      home_adj,
        "away_adj":      away_adj,
    }


def predire_match(home_stats, away_stats, home_advantage=1.05, h2h_stats=None):
    """
    Prédit toutes les probabilités via Dixon-Coles + ajustement H2H.

    home_advantage = 1.05 (réduit car les stats domicile/extérieur
    capturent déjà naturellement l'avantage du terrain).

    Les lambdas sont plafonnés à 3.5 pour rester réalistes.
    """
    # ── Buts attendus de base — plafonnés à 3.5 ──
    lambda_home_base = min(3.5, max(0.3,
        home_stats["attaque"] * away_stats["defense"] * home_advantage
    ))
    lambda_away_base = min(3.5, max(0.3,
        away_stats["attaque"] * home_stats["defense"]
    ))

    print(f"  🔢 Lambdas de base :"
          f" DOM={round(lambda_home_base, 3)} | EXT={round(lambda_away_base, 3)}")

    lambda_home = lambda_home_base
    lambda_away = lambda_away_base

    # ── Ajustement H2H ──
    if h2h_stats and h2h_stats["nb_matchs"] >= 2:
        h2h_weight = min(0.30, h2h_stats["nb_matchs"] * 0.05)

        lambda_home = (
            lambda_home * (1 - h2h_weight)
            + h2h_stats["home_buts_moy"] * h2h_weight
        ) * h2h_stats["home_adj"]

        lambda_away = (
            lambda_away * (1 - h2h_weight)
            + h2h_stats["away_buts_moy"] * h2h_weight
        ) * h2h_stats["away_adj"]

        # Plafond post-H2H
        lambda_home = min(3.5, max(0.3, round(lambda_home, 3)))
        lambda_away = min(3.5, max(0.3, round(lambda_away, 3)))

        print(f"  ✅ Lambdas après H2H (poids={h2h_weight}) :"
              f" DOM={lambda_home} | EXT={lambda_away}")
    else:
        print(f"  ℹ️  Pas d'ajustement H2H (moins de 2 confrontations)")

    # ── Matrice Dixon-Coles 8×8 ──
    MAX     = 8
    matrice = np.zeros((MAX, MAX))

    for i in range(MAX):
        for j in range(MAX):
            rho        = -0.13
            correction = tau(i, j, lambda_home, lambda_away, rho)
            matrice[i][j] = (
                correction
                * poisson.pmf(i, lambda_home)
                * poisson.pmf(j, lambda_away)
            )

    # Normalisation
    total = matrice.sum()
    if total > 0:
        matrice /= total

    # ── Probabilités principales ──
    p_home = float(np.tril(matrice, -1).sum())
    p_draw = float(np.diag(matrice).sum())
    p_away = float(np.triu(matrice, 1).sum())

    # Over / Under 2.5
    p_over25 = sum(
        matrice[i][j]
        for i in range(MAX)
        for j in range(MAX)
        if i + j > 2
    )

    # BTTS
    p_btts = sum(
        matrice[i][j]
        for i in range(1, MAX)
        for j in range(1, MAX)
    )

    # Score le plus probable
    idx            = np.unravel_index(np.argmax(matrice), matrice.shape)
    score_probable = f"{idx[0]}-{idx[1]}"
    prob_score     = round(float(matrice[idx]) * 100, 1)

    # Mi-temps (approximation 45 min)
    lh_ht     = lambda_home * 0.45
    la_ht     = lambda_away * 0.45
    p_ht_home = 1 - poisson.cdf(0, lh_ht) * (1 - poisson.cdf(0, la_ht) + poisson.pmf(0, la_ht))
    p_ht_away = 1 - poisson.cdf(0, la_ht) * (1 - poisson.cdf(0, lh_ht) + poisson.pmf(0, lh_ht))
    p_ht_draw = max(0.0, 1 - p_ht_home - p_ht_away)

    print(f"  🏆 Résultat final :"
          f" V1={round(p_home*100,1)}%"
          f" | Nul={round(p_draw*100,1)}%"
          f" | V2={round(p_away*100,1)}%"
          f" | Score={score_probable} ({prob_score}%)"
          f" | +2.5={round(p_over25*100,1)}%"
          f" | BTTS={round(p_btts*100,1)}%")

    return {
        # Résultat final
        "p_home":         round(p_home * 100, 1),
        "p_draw":         round(p_draw * 100, 1),
        "p_away":         round(p_away * 100, 1),
        # Double chance
        "p_1x":           round((p_home + p_draw) * 100, 1),
        "p_x2":           round((p_draw + p_away) * 100, 1),
        "p_12":           round((p_home + p_away) * 100, 1),
        # Buts
        "p_over25":       round(p_over25 * 100, 1),
        "p_under25":      round((1 - p_over25) * 100, 1),
        "buts_dom":       round(lambda_home, 2),
        "buts_ext":       round(lambda_away, 2),
        "buts_total":     round(lambda_home + lambda_away, 2),
        # BTTS
        "p_btts":         round(p_btts * 100, 1),
        "p_no_btts":      round((1 - p_btts) * 100, 1),
        # Score exact
        "score_probable": score_probable,
        "prob_score":     prob_score,
        # Mi-temps
        "p_ht_home":      round(p_ht_home * 100, 1),
        "p_ht_draw":      round(p_ht_draw * 100, 1),
        "p_ht_away":      round(p_ht_away * 100, 1),
    }


def tau(x, y, lh, la, rho):
    """
    Correction Dixon-Coles pour les scores faibles.
    Ajuste les probabilités de 0-0, 1-0, 0-1 et 1-1.
    """
    if x == 0 and y == 0: return max(0.01, 1 - lh * la * rho)
    if x == 0 and y == 1: return max(0.01, 1 + lh * rho)
    if x == 1 and y == 0: return max(0.01, 1 + la * rho)
    if x == 1 and y == 1: return max(0.01, 1 - rho)
    return 1.0