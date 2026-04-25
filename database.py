# database.py — Gestion des codes VIP via Supabase (BradFoot)
import os
import random
import string
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════════════
#  CLIENT SUPABASE
# ══════════════════════════════════════════════════════════════

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")   # service_role key (côté serveur)

if not SUPABASE_URL or not SUPABASE_KEY:
    raise EnvironmentError(
        "❌ SUPABASE_URL et SUPABASE_SERVICE_KEY doivent être définis dans le .env"
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE = "vip_codes"

# Sections valides
SECTIONS_VALIDES = {"top25", "prediction", "all"}


# ══════════════════════════════════════════════════════════════
#  UTILITAIRES
# ══════════════════════════════════════════════════════════════

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Convertit en string ISO8601 avec timezone pour Supabase."""
    return dt.isoformat()


def _generer_code(prefix: str = "VIP", longueur: int = 8) -> str:
    """Génère un code aléatoire style VIP-XXXXXXXX."""
    chars = string.ascii_uppercase + string.digits
    suffix = ''.join(random.choices(chars, k=longueur))
    return f"{prefix}-{suffix}"


def _section_autorise(section_code: str, section_demandee: str) -> bool:
    """
    Vérifie si le code donne accès à la section demandée.
    - 'all'        → accès à tout
    - 'top25'      → accès uniquement top25
    - 'prediction' → accès uniquement prediction
    """
    if section_code == "all":
        return True
    if section_demandee == "":
        return True
    return section_code == section_demandee


# ══════════════════════════════════════════════════════════════
#  VÉRIFICATION
# ══════════════════════════════════════════════════════════════

def verifier_code(code: str, section: str = "") -> tuple[bool, dict | str]:
    """
    Vérifie si un code VIP est valide pour une section donnée.

    Retourne (True, info_dict) si valide, (False, message_erreur) sinon.
    Met à jour last_used_at et usage_count si valide.
    """
    if not code:
        return False, "Code manquant."

    code = code.strip().upper()

    try:
        res = (
            supabase.table(TABLE)
            .select("*")
            .eq("code", code)
            .single()
            .execute()
        )
    except Exception as e:
        print(f"  ⚠️  Supabase error (verifier_code): {e}")
        return False, "Erreur base de données."

    if not res.data:
        return False, "Code VIP invalide."

    row = res.data

    # Actif ?
    if not row["is_active"]:
        return False, "Ce code VIP a été révoqué."

    # Expiré ?
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if _now_utc() > expires_at:
        return False, "Ce code VIP est expiré."

    # Section autorisée ?
    if not _section_autorise(row["section"], section):
        return False, f"Ce code n'autorise pas l'accès à '{section}'."

    # Mise à jour stats d'utilisation (non bloquant)
    try:
        supabase.table(TABLE).update({
            "last_used_at": _iso(_now_utc()),
            "usage_count":  row["usage_count"] + 1,
        }).eq("code", code).execute()
    except Exception as e:
        print(f"  ⚠️  Impossible de mettre à jour usage_count: {e}")

    return True, {
        "code":       row["code"],
        "section":    row["section"],
        "expires_at": row["expires_at"],
    }


# ══════════════════════════════════════════════════════════════
#  CRÉATION
# ══════════════════════════════════════════════════════════════

def creer_code(
    section:     str = "all",
    jours:       int = 7,
    custom_code: str | None = None,
    note:        str = ""
) -> dict | None:
    """
    Crée un code VIP.

    - section     : 'top25' | 'prediction' | 'all'
    - jours       : durée de validité en jours
    - custom_code : code personnalisé (optionnel)
    - note        : note libre de l'admin

    Retourne le dict du code créé, ou None si le code existe déjà.
    """
    if section not in SECTIONS_VALIDES:
        section = "all"

    code = (custom_code.strip().upper() if custom_code else _generer_code())
    expires_at = _now_utc() + timedelta(days=jours)

    payload = {
        "code":       code,
        "section":    section,
        "expires_at": _iso(expires_at),
        "is_active":  True,
        "note":       note,
    }

    try:
        res = supabase.table(TABLE).insert(payload).execute()
        if res.data:
            print(f"  ✅ Code créé : {code} | section={section} | expire={expires_at.date()}")
            return res.data[0]
        return None
    except Exception as e:
        msg = str(e)
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            print(f"  ⚠️  Code déjà existant : {code}")
            return None
        print(f"  ❌ Erreur création code : {e}")
        return None


def creer_plusieurs_codes(
    section: str = "all",
    jours:   int = 7,
    prefix:  str = "VIP",
    nb:      int = 5,
    note:    str = ""
) -> list[dict]:
    """Crée plusieurs codes VIP d'un coup. Retourne la liste des codes créés."""
    codes_crees = []
    tentatives  = 0
    max_tentatives = nb * 3  # évite la boucle infinie en cas de collision

    while len(codes_crees) < nb and tentatives < max_tentatives:
        tentatives += 1
        result = creer_code(section=section, jours=jours, note=note,
                            custom_code=_generer_code(prefix=prefix))
        if result:
            codes_crees.append(result)

    print(f"  📦 {len(codes_crees)}/{nb} codes créés (section={section}, {jours}j)")
    return codes_crees


# ══════════════════════════════════════════════════════════════
#  RÉVOCATION
# ══════════════════════════════════════════════════════════════

def revoquer_code(code: str) -> bool:
    """Désactive un code VIP (soft delete)."""
    code = code.strip().upper()
    try:
        res = (
            supabase.table(TABLE)
            .update({"is_active": False})
            .eq("code", code)
            .execute()
        )
        success = bool(res.data)
        if success:
            print(f"  🚫 Code révoqué : {code}")
        return success
    except Exception as e:
        print(f"  ❌ Erreur révocation {code}: {e}")
        return False


def reactiver_code(code: str) -> bool:
    """Réactive un code VIP précédemment révoqué."""
    code = code.strip().upper()
    try:
        res = (
            supabase.table(TABLE)
            .update({"is_active": True})
            .eq("code", code)
            .execute()
        )
        success = bool(res.data)
        if success:
            print(f"  ✅ Code réactivé : {code}")
        return success
    except Exception as e:
        print(f"  ❌ Erreur réactivation {code}: {e}")
        return False


def supprimer_code(code: str) -> bool:
    """Supprime définitivement un code VIP de la base."""
    code = code.strip().upper()
    try:
        res = (
            supabase.table(TABLE)
            .delete()
            .eq("code", code)
            .execute()
        )
        success = bool(res.data)
        if success:
            print(f"  🗑️  Code supprimé : {code}")
        return success
    except Exception as e:
        print(f"  ❌ Erreur suppression {code}: {e}")
        return False


def prolonger_code(code: str, jours_supplementaires: int = 7) -> bool:
    """Prolonge la validité d'un code VIP."""
    code = code.strip().upper()
    try:
        # Récupérer la date d'expiration actuelle
        res = supabase.table(TABLE).select("expires_at").eq("code", code).single().execute()
        if not res.data:
            return False

        expires_at = datetime.fromisoformat(res.data["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        # Si déjà expiré, on part de maintenant
        base = max(expires_at, _now_utc())
        new_expires = base + timedelta(days=jours_supplementaires)

        supabase.table(TABLE).update({
            "expires_at": _iso(new_expires)
        }).eq("code", code).execute()

        print(f"  ⏳ Code {code} prolongé jusqu'au {new_expires.date()}")
        return True
    except Exception as e:
        print(f"  ❌ Erreur prolongation {code}: {e}")
        return False


# ══════════════════════════════════════════════════════════════
#  LISTING & STATS
# ══════════════════════════════════════════════════════════════

def lister_codes(
    section:    str | None = None,
    actif_only: bool = False,
    limit:      int  = 200
) -> list[dict]:
    """
    Liste tous les codes VIP.

    - section    : filtre par section (optionnel)
    - actif_only : ne retourne que les codes actifs et non expirés
    - limit      : nombre max de résultats
    """
    try:
        query = supabase.table(TABLE).select("*").order("created_at", desc=True).limit(limit)

        if section and section in SECTIONS_VALIDES:
            query = query.eq("section", section)

        if actif_only:
            query = query.eq("is_active", True).gt("expires_at", _iso(_now_utc()))

        res = query.execute()
        return res.data or []
    except Exception as e:
        print(f"  ❌ Erreur listing codes: {e}")
        return []


def stats_codes() -> dict:
    """Retourne des statistiques globales sur les codes VIP."""
    try:
        all_codes = lister_codes(limit=1000)
        now = _now_utc()

        total       = len(all_codes)
        actifs      = 0
        expires     = 0
        revoques    = 0
        par_section = {"top25": 0, "prediction": 0, "all": 0}
        total_usage = 0

        for c in all_codes:
            exp = datetime.fromisoformat(c["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)

            total_usage += c.get("usage_count", 0)
            section = c.get("section", "all")
            if section in par_section:
                par_section[section] += 1

            if not c["is_active"]:
                revoques += 1
            elif now > exp:
                expires += 1
            else:
                actifs += 1

        return {
            "total":        total,
            "actifs":       actifs,
            "expires":      expires,
            "revoques":     revoques,
            "par_section":  par_section,
            "total_usage":  total_usage,
        }
    except Exception as e:
        print(f"  ❌ Erreur stats_codes: {e}")
        return {
            "total": 0, "actifs": 0, "expires": 0,
            "revoques": 0, "par_section": {}, "total_usage": 0
        }


def rechercher_code(code: str) -> dict | None:
    """Recherche un code exact et retourne ses détails complets."""
    code = code.strip().upper()
    try:
        res = supabase.table(TABLE).select("*").eq("code", code).single().execute()
        return res.data
    except Exception:
        return None