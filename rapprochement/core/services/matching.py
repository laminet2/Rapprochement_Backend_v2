"""
Moteur de recherche de correspondances (aucune écriture en base).

Pour une règle (un type Valorix) il produit :
  * des rapprochements 1-1 : une ligne banque <-> une ligne Valorix ;
  * des regroupements 1-N : une ligne banque <-> plusieurs lignes Valorix
    dont la SOMME correspond (ex. un dépôt de 50 chèques) ;
  * des regroupements N-1 : plusieurs lignes banque <-> une ligne Valorix.

Les regroupements ne sont cherchés que pour les lignes sans candidat 1-1,
et une ligne n'apparaît que dans UNE proposition de regroupement par
génération (algorithme glouton) pour éviter des propositions qui se
chevauchent.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from core.models import StatutValidation, TransactionBancaireRapprochee, TransactionValorixRapprochee
from core.services.candidats import Ligne, construire_ligne, evaluer_groupe
from core.services.scoring import classer_candidats
from core.services.tolerance import calculer_tolerance
from releverBancaire.models import TransactionBancaire
from valorix.models import TransactionValorix

TAILLE_MAX_POOL = 40
BUDGET_NOEUDS = 200_000
MAX_SOLUTIONS = 8


def _en_centimes(montant):
    return int((Decimal(montant) * 100).to_integral_value())


def sous_ensembles(valeurs, cible, tolerance, taille_max,
                   budget=BUDGET_NOEUDS, max_solutions=MAX_SOLUTIONS):
    """
    Cherche des sous-ensembles de 2 à ``taille_max`` éléments dont la somme
    est dans [cible - tolerance, cible + tolerance].

    ``valeurs`` : liste d'entiers positifs (centimes).
    Retourne une liste de listes d'indices (dans ``valeurs``). La recherche
    est bornée (``budget`` nœuds) pour rester rapide sur de gros volumes.
    """
    ordre = sorted(range(len(valeurs)), key=lambda i: -valeurs[i])
    vals = [valeurs[i] for i in ordre]
    n = len(vals)
    bas, haut = cible - tolerance, cible + tolerance

    prefixes = [0]
    for v in vals:
        prefixes.append(prefixes[-1] + v)

    solutions, noeuds = [], [0]

    def explorer(debut, somme, choisis):
        for j in range(debut, n):
            noeuds[0] += 1
            if noeuds[0] > budget or len(solutions) >= max_solutions:
                return
            s = somme + vals[j]
            if s > haut:
                continue
            places = taille_max - len(choisis) - 1
            # somme maximale atteignable avec les éléments suivants
            reste = prefixes[min(n, j + 1 + places)] - prefixes[j + 1]
            if s + reste < bas:
                continue
            nouveaux = choisis + [j]
            if len(nouveaux) >= 2 and bas <= s <= haut:
                solutions.append([ordre[k] for k in nouveaux])
            if len(nouveaux) < taille_max:
                explorer(j + 1, s, nouveaux)

    explorer(0, 0, [])
    return solutions


def _meilleur_groupe(ancre, pool, params, ancre_est_banque):
    """Meilleur groupe (1-N ou N-1) autour d'une ligne « ancre », ou None."""
    if len(pool) < 2:
        return None

    pool = sorted(
        pool, key=lambda l: (abs((l.date - ancre.date).days), -l.montant)
    )[:TAILLE_MAX_POOL]

    # Tolérance large pour la recherche ; evaluer_groupe tranche ensuite.
    tolerance = calculer_tolerance(
        ancre.montant * Decimal("1.1"),
        params.tolerance_fixe,
        params.tolerance_pourcentage,
    )
    solutions = sous_ensembles(
        [_en_centimes(l.montant) for l in pool],
        _en_centimes(ancre.montant),
        _en_centimes(tolerance),
        params.regroupement_max,
    )

    candidats = []
    for indices in solutions:
        groupe = [pool[i] for i in indices]
        candidat = (
            evaluer_groupe([ancre], groupe, params)
            if ancre_est_banque
            else evaluer_groupe(groupe, [ancre], params)
        )
        if candidat:
            candidats.append(candidat)

    if not candidats:
        return None
    return classer_candidats(candidats, params)[0]


# ------------------------------------------------------------------
# Chargement des données
# ------------------------------------------------------------------

def _ids_deja_valides(modele_lien):
    return modele_lien.objects.filter(
        rapprochement__statut_validation=StatutValidation.VALIDE
    ).values("transaction_id")


def charger_valorix(params, lot_ids=None):
    qs = TransactionValorix.objects.filter(
        types=params.type_transaction_valorix
    ).exclude(pk__in=_ids_deja_valides(TransactionValorixRapprochee))
    if lot_ids:
        qs = qs.filter(lot_id__in=lot_ids)

    lignes, ignorees = [], 0
    for tx in qs.order_by("date_transaction", "id"):
        ligne = construire_ligne(tx)
        if ligne:
            lignes.append(ligne)
        else:
            ignorees += 1
    return lignes, ignorees


def charger_banque(params, date_min, date_max, lot_ids=None):
    qs = TransactionBancaire.objects.filter(
        date_transaction__range=(date_min, date_max)
    ).exclude(pk__in=_ids_deja_valides(TransactionBancaireRapprochee))
    if params.sens_transaction_document_cible == "DEBIT":
        qs = qs.filter(debit__gt=0)
    else:
        qs = qs.filter(credit__gt=0)
    if lot_ids:
        qs = qs.filter(lot_id__in=lot_ids)

    lignes, ignorees = [], 0
    for tx in qs.select_related("compte", "compte__banque").order_by("date_transaction", "id"):
        ligne = construire_ligne(tx)
        if ligne:
            lignes.append(ligne)
        else:
            ignorees += 1
    return lignes, ignorees


# ------------------------------------------------------------------
# Recherche
# ------------------------------------------------------------------

def rechercher_propositions(params, lot_valorix_ids=None, lot_banque_ids=None,
                            inclure_regroupements=True):
    """
    Retourne ``(candidats, stats)`` : candidats classés du meilleur au moins
    bon, chacun étant un dictionnaire issu de ``evaluer_groupe`` + score.
    """
    valorix, valorix_ignorees = charger_valorix(params, lot_valorix_ids)
    stats = {
        "valorix_examinees": len(valorix),
        "banque_examinees": 0,
        "lignes_ignorees": valorix_ignorees,
    }
    if not valorix:
        return [], stats

    date_min = min(v.date for v in valorix) - timedelta(days=params.fenetre_jours_avant)
    date_max = max(v.date for v in valorix) + timedelta(days=params.fenetre_jours_apres)
    banque, banque_ignorees = charger_banque(params, date_min, date_max, lot_banque_ids)
    stats["banque_examinees"] = len(banque)
    stats["lignes_ignorees"] += banque_ignorees

    par_date = defaultdict(list)
    for b in banque:
        par_date[b.date].append(b)

    un_un, groupes = [], []
    banque_avec_1_1, valorix_avec_1_1 = set(), set()

    for v in valorix:
        for k in range(-params.fenetre_jours_avant, params.fenetre_jours_apres + 1):
            for b in par_date.get(v.date + timedelta(days=k), ()):
                candidat = evaluer_groupe([b], [v], params)
                if candidat:
                    un_un.append(candidat)
                    banque_avec_1_1.add(b.id)
                    valorix_avec_1_1.add(v.id)

    if inclure_regroupements and params.regroupement_max >= 2:
        libres_v = [v for v in valorix if v.id not in valorix_avec_1_1]
        libres_b = [b for b in banque if b.id not in banque_avec_1_1]
        pris_v, pris_b = set(), set()

        # 1-N : une ligne banque <-> plusieurs lignes Valorix
        for b in sorted(libres_b, key=lambda l: (l.date, -l.montant)):
            pool = [
                v for v in libres_v
                if v.id not in pris_v
                and -params.fenetre_jours_avant <= (b.date - v.date).days <= params.fenetre_jours_apres
            ]
            groupe = _meilleur_groupe(b, pool, params, ancre_est_banque=True)
            if groupe:
                groupes.append(groupe)
                pris_b.add(b.id)
                pris_v.update(l.id for l in groupe["lignes_valorix"])

        # N-1 : plusieurs lignes banque <-> une ligne Valorix
        for v in sorted(libres_v, key=lambda l: (l.date, -l.montant)):
            if v.id in pris_v:
                continue
            pool = [
                b for b in libres_b
                if b.id not in pris_b
                and -params.fenetre_jours_avant <= (b.date - v.date).days <= params.fenetre_jours_apres
            ]
            groupe = _meilleur_groupe(v, pool, params, ancre_est_banque=False)
            if groupe:
                groupes.append(groupe)
                pris_v.add(v.id)
                pris_b.update(l.id for l in groupe["lignes_banque"])

    # Les groupes sont déjà scorés ; on score les 1-1 puis on classe l'ensemble.
    resultats = classer_candidats(un_un, params) + groupes
    resultats.sort(key=lambda c: -c["score_correspondance"])
    return resultats, stats
