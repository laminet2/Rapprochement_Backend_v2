"""Comparaison de transactions (références, compte, nom) et évaluation d'un groupe."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from core.services.tolerance import evaluer_ecart


def obtenir_montant_et_sens(transaction):
    """
    Retourne (montant positif, sens).

    Le sens est DEBIT ou CREDIT.
    Si les deux champs sont renseignés avec des montants non nuls,
    la transaction est considérée comme ambiguë.
    """
    debit = transaction.debit or Decimal("0")
    credit = transaction.credit or Decimal("0")

    if debit < 0 or credit < 0:
        raise ValidationError(
            "Les montants débit/crédit ne doivent pas être négatifs."
        )

    debit_actif = debit > 0
    credit_actif = credit > 0

    if debit_actif and credit_actif:
        raise ValidationError(
            "La transaction possède à la fois un débit et un crédit non nuls."
        )

    if debit_actif:
        return debit, "DEBIT"

    if credit_actif:
        return credit, "CREDIT"

    raise ValidationError(
        "Impossible de déterminer le montant : débit et crédit sont nuls."
    )


def normaliser_reference(reference):
    """
    Normalisation simple pour comparer des références.
    Ne supprime pas les zéros initiaux, qui peuvent être significatifs.
    """
    if not reference:
        return ""

    return "".join(str(reference).upper().split()).strip()


def normaliser_texte(texte):
    """
    Normalise un texte pour faciliter les comparaisons :
    - majuscules ;
    - suppression des accents ;
    - espaces uniformisés.
    """
    if not texte:
        return ""

    texte = str(texte).upper().strip()
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(
        caractere
        for caractere in texte
        if not unicodedata.combining(caractere)
    )

    return " ".join(texte.split())


def contient_numero_exact(texte, numero):
    """
    Recherche un numéro comme élément distinct.
    Évite les correspondances à l'intérieur d'un autre numéro.
    """
    texte = normaliser_texte(texte)
    numero = str(numero or "").strip()

    if not texte or not numero:
        return False

    motif = rf"(?<!\d){re.escape(numero)}(?!\d)"
    return re.search(motif, texte) is not None


def numero_cheque_correspond(transaction_valorix, transaction_banque):
    """
    Recherche les références Valorix dans les références et le libellé
    bancaire. Une correspondance doit être exacte au niveau du numéro.
    """
    references_valorix = [
        getattr(transaction_valorix, "references", None),
        getattr(transaction_valorix, "references_rapprochement", None),
    ]

    textes_bancaires = [
        getattr(transaction_banque, "reference_rapprochement", None),
        getattr(transaction_banque, "libelle_brut", None),
    ]

    for reference in references_valorix:
        if not reference:
            continue

        # Les références peuvent contenir plusieurs éléments séparés
        # par des espaces, virgules, tirets ou autres signes.
        numeros = re.findall(r"\d+", str(reference))

        for numero in numeros:
            for texte_bancaire in textes_bancaires:
                if contient_numero_exact(texte_bancaire, numero):
                    return True

    return False


def numero_compte_correspond(transaction_valorix, transaction_banque):
    """
    Recherche le numéro de compte Valorix dans le libellé bancaire,
    en respectant les limites numériques.
    """
    numero_compte = getattr(transaction_valorix, "numero_compte", None)
    libelle = getattr(transaction_banque, "libelle_brut", None)

    if not numero_compte or not libelle:
        return False

    return contient_numero_exact(libelle, numero_compte)


def nom_client_correspond(transaction_valorix, transaction_banque):
    """
    Compare les noms indépendamment de l'ordre des mots.
    Exige au moins deux mots significatifs correspondants pour éviter
    qu'un prénom ou nom très courant suffise à lui seul.
    """
    nom_valorix = normaliser_texte(
        getattr(transaction_valorix, "nomComplet", None)
    )
    libelle_banque = normaliser_texte(
        getattr(transaction_banque, "libelle_brut", None)
    )

    if not nom_valorix or not libelle_banque:
        return False

    mots_nom = {
        mot for mot in re.findall(r"[A-Z]+", nom_valorix)
        if len(mot) >= 2
    }

    mots_libelle = set(re.findall(r"[A-Z]+", libelle_banque))

    if len(mots_nom) < 2:
        return False

    mots_communs = mots_nom & mots_libelle

    return len(mots_communs) >= min(2, len(mots_nom))


def references_correspondent(transaction_valorix, transaction_banque):
    """
    Recherche une référence numérique Valorix dans la référence
    ou le libellé bancaire.

    Les séquences numériques sont comparées exactement :
    12345 correspond à 12345, mais pas à 9123456 ni à 123456.
    Les séquences doivent contenir au moins 4 chiffres pour éviter
    les correspondances trop faibles.
    """
    references_valorix = [
        getattr(transaction_valorix, "references", None),
        getattr(transaction_valorix, "references_rapprochement", None),
    ]

    texte_bancaire = " ".join(
        str(valeur)
        for valeur in (
            transaction_banque.reference_rapprochement,
            getattr(transaction_banque, "libelle_brut", None),
        )
        if valeur
    )

    numeros_valorix = set()

    for reference in references_valorix:
        if not reference:
            continue

        numeros_valorix.update(
            re.findall(r"(?<!\d)\d{4,}(?!\d)", str(reference))
        )

    if not numeros_valorix:
        return False

    numeros_bancaires = set(
        re.findall(r"(?<!\d)\d{4,}(?!\d)", texte_bancaire)
    )

    return bool(numeros_valorix & numeros_bancaires)


# ------------------------------------------------------------------
# Lignes normalisées (montant positif + sens + date)
# ------------------------------------------------------------------

@dataclass
class Ligne:
    tx: object
    montant: Decimal
    sens: str
    date: date

    @property
    def id(self):
        return self.tx.pk


def _date_de(valeur):
    if hasattr(valeur, "hour"):  # datetime
        if timezone.is_aware(valeur):
            valeur = timezone.localtime(valeur)
        return valeur.date()
    return valeur


def construire_ligne(tx):
    """Transaction -> Ligne, ou None si montant ambigu / nul."""
    try:
        montant, sens = obtenir_montant_et_sens(tx)
    except ValidationError:
        return None
    return Ligne(tx=tx, montant=montant, sens=sens, date=_date_de(tx.date_transaction))


def _a_une_reference(transaction_valorix):
    texte = " ".join(
        str(v)
        for v in (
            getattr(transaction_valorix, "references", None),
            getattr(transaction_valorix, "references_rapprochement", None),
        )
        if v
    )
    return bool(re.search(r"\d", texte))


def _a_un_nom_exploitable(transaction_valorix):
    nom = normaliser_texte(getattr(transaction_valorix, "nomComplet", None))
    mots = {m for m in re.findall(r"[A-Z]+", nom) if len(m) >= 2}
    return len(mots) >= 2


def _moyenne(valeurs):
    valeurs = list(valeurs)
    return Decimal(sum(1 for v in valeurs if v)) / Decimal(len(valeurs))


def type_de_groupe(nb_banque, nb_valorix):
    if nb_banque == 1 and nb_valorix == 1:
        return "1-1"
    if nb_banque == 1:
        return "1-N"
    if nb_valorix == 1:
        return "N-1"
    return "N-M"


def evaluer_groupe(lignes_banque, lignes_valorix, params):
    """
    Évalue un rapprochement 1-1, 1-N ou N-1.

    Retourne un dictionnaire « candidat » (compatible avec le scoring) ou
    None si le groupe ne respecte pas la règle : sens bancaire, fenêtre de
    dates (pour chaque paire) ou tolérance sur la SOMME des montants.
    """
    nb, nv = len(lignes_banque), len(lignes_valorix)
    if nb == 0 or nv == 0 or (nb > 1 and nv > 1):
        return None

    cible = params.sens_transaction_document_cible
    if any(l.sens != cible for l in lignes_banque):
        return None

    total_banque = sum((l.montant for l in lignes_banque), Decimal(0))
    total_valorix = sum((l.montant for l in lignes_valorix), Decimal(0))

    evaluation = evaluer_ecart(
        montant_banque=total_banque,
        montant_valorix=total_valorix,
        tolerance_fixe=params.tolerance_fixe,
        tolerance_pourcentage=params.tolerance_pourcentage,
    )
    if not evaluation["acceptable"]:
        return None

    ecarts_jours = []
    for b in lignes_banque:
        for v in lignes_valorix:
            ecart = (b.date - v.date).days
            if ecart < -params.fenetre_jours_avant or ecart > params.fenetre_jours_apres:
                return None
            ecarts_jours.append(ecart)

    if nb == 1:
        paires = [(lignes_banque[0], v) for v in lignes_valorix]
    else:
        paires = [(b, lignes_valorix[0]) for b in lignes_banque]

    sens_valorix = {l.sens for l in lignes_valorix}

    return {
        "type_rapprochement": type_de_groupe(nb, nv),
        "lignes_banque": lignes_banque,
        "lignes_valorix": lignes_valorix,
        "montant_banque": total_banque,
        "montant_valorix": total_valorix,
        "sens_banque": cible,
        "sens_valorix": next(iter(sens_valorix)) if len(sens_valorix) == 1 else None,
        "ecart_montant": evaluation["ecart"],
        "tolerance": evaluation["tolerance"],
        "ecarts_jours": ecarts_jours,
        "ecart_jours": ecarts_jours[0] if len(ecarts_jours) == 1
        else round(sum(ecarts_jours) / len(ecarts_jours)),
        "reference_correspond": _moyenne(
            references_correspondent(v.tx, b.tx) for b, v in paires
        ),
        "numero_cheque_correspond": _moyenne(
            numero_cheque_correspond(v.tx, b.tx) for b, v in paires
        ),
        "compte_correspond": _moyenne(
            numero_compte_correspond(v.tx, b.tx) for b, v in paires
        ),
        "nom_correspond": _moyenne(
            nom_client_correspond(v.tx, b.tx) for b, v in paires
        ),
        "sens_correct": _moyenne(l.sens != cible for l in lignes_valorix),
        "reference_disponible": any(_a_une_reference(v.tx) for v in lignes_valorix),
        "compte_disponible": any(
            (getattr(v.tx, "numero_compte", "") or "").strip() for v in lignes_valorix
        ),
        "nom_disponible": any(_a_un_nom_exploitable(v.tx) for v in lignes_valorix),
    }
