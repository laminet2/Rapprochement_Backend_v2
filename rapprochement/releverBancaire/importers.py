"""Lecture (PDF -> lignes à vérifier) et enregistrement d'un relevé bancaire."""

from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.db import transaction as db_transaction

from importation.models import LotImport, SourceImport
from importation.outils import (
    a_zero,
    marquer_doublons,
    montant_positif,
    montant_vers_texte,
    normaliser_texte,
)
from importation.pdf import (
    extraire_par_blocs,
    lire_pages_pdf,
    regrouper_pages,
)
from releverBancaire.models import TransactionBancaire, TypeTransactionBancaire
from releverBancaire.schemas import ReleveBancaireSchema

PAGES_PAR_BLOC = 2
PROMPT = (Path(__file__).resolve().parent / "prompt.txt").read_text(
    encoding="utf-8"
)
TYPES_VALIDES = {valeur for valeur, _ in TypeTransactionBancaire.choices}


def _parse_date(texte):
    return datetime.strptime(str(texte).strip(), "%Y-%m-%d").date()


def construire_ligne(index, brut):
    """
    Transforme une ligne extraite par le LLM en dictionnaire JSON simple.

    Les lignes douteuses ne sont PAS rejetées : elles reçoivent une liste
    ``erreurs`` pour que l'utilisateur puisse les corriger ou les retirer
    pendant l'étape de vérification.
    """
    erreurs, alertes = [], []

    date_tx, date_valeur = brut.date_transaction, brut.date_valeur
    try:
        date_tx = _parse_date(brut.date_transaction).isoformat()
    except (ValueError, TypeError):
        erreurs.append(f"Date de transaction invalide : {brut.date_transaction!r}.")

    if brut.date_valeur:
        try:
            date_valeur = _parse_date(brut.date_valeur).isoformat()
        except (ValueError, TypeError):
            alertes.append(f"Date de valeur ignorée (invalide) : {brut.date_valeur!r}.")
            date_valeur = None

    def en_decimal(valeur):
        return None if valeur is None else Decimal(str(valeur))

    debit, credit = en_decimal(brut.debit), en_decimal(brut.credit)
    solde = en_decimal(brut.solde_apres_operation)

    if (debit is not None and debit < 0) or (credit is not None and credit < 0):
        erreurs.append("Les montants débit/crédit ne doivent pas être négatifs.")
    debit, credit = montant_positif(debit), montant_positif(credit)

    if debit and credit:
        erreurs.append("Débit et crédit sont renseignés simultanément.")
    elif not debit and not credit:
        erreurs.append("Aucun montant (débit et crédit absents).")

    type_tx = brut.type_transaction.value
    if type_tx not in TYPES_VALIDES:
        type_tx = TypeTransactionBancaire.AUTRE

    return {
        "index": index,
        "date_transaction": date_tx,
        "date_valeur": date_valeur,
        "reference_rapprochement": brut.numero_compte_ou_cheque,
        "libelle_brut": brut.libelle_brut,
        "debit": montant_vers_texte(debit),
        "credit": montant_vers_texte(credit),
        "solde_apres_operation": montant_vers_texte(solde),
        "type_transaction": type_tx,
        "doublon_probable": False,
        "erreurs": erreurs,
        "alertes": alertes,
    }


def extraire_releve_pdf(fichier):
    """PDF -> liste de lignes (dict). Ne touche pas à la base."""
    pages = lire_pages_pdf(fichier)
    blocs = regrouper_pages(pages, PAGES_PAR_BLOC)
    brutes = extraire_par_blocs(
        blocs, ReleveBancaireSchema, PROMPT, "relevé bancaire"
    )
    return [construire_ligne(i, brut) for i, brut in enumerate(brutes, 1)]


# ------------------------------------------------------------------
# Doublons
# ------------------------------------------------------------------

def cle_transaction(date_tx, debit, credit, libelle):
    return (
        date_tx,
        a_zero(debit),
        a_zero(credit),
        normaliser_texte(libelle),
    )


def _compteur_existant(compte_id, dates):
    if not dates:
        return Counter()
    lignes = TransactionBancaire.objects.filter(
        compte_id=compte_id,
        date_transaction__range=(min(dates), max(dates)),
    ).values_list("date_transaction", "debit", "credit", "libelle_brut")
    return Counter(cle_transaction(*ligne) for ligne in lignes)


def marquer_doublons_lignes(lignes, compte):
    """Renseigne ``doublon_probable`` sur des lignes de prévisualisation."""
    valides = []
    for ligne in lignes:
        try:
            date_tx = _parse_date(ligne["date_transaction"])
        except (ValueError, TypeError):
            continue
        valides.append((ligne, date_tx))

    compteur = _compteur_existant(compte.id, [d for _, d in valides])
    cles = [
        cle_transaction(
            d,
            Decimal(ligne["debit"]) if ligne.get("debit") else None,
            Decimal(ligne["credit"]) if ligne.get("credit") else None,
            ligne["libelle_brut"],
        )
        for ligne, d in valides
    ]
    for (ligne, _), doublon in zip(valides, marquer_doublons(cles, compteur)):
        ligne["doublon_probable"] = doublon
    return lignes


# ------------------------------------------------------------------
# Enregistrement
# ------------------------------------------------------------------

@db_transaction.atomic
def enregistrer_import(lignes, compte_par_defaut, nom_fichier, ignorer_doublons):
    """
    Enregistre des lignes déjà validées par le serializer.

    ``lignes``: liste de dicts (``compte`` éventuellement présent par ligne).
    Retourne ``(lot, nombre_creees, nombre_ignorees)``.
    """
    par_compte = {}
    for ligne in lignes:
        compte = ligne.get("compte") or compte_par_defaut
        par_compte.setdefault(compte.id, (compte, []))[1].append(ligne)

    lot = LotImport.objects.create(
        source=SourceImport.BANQUE,
        nom_fichier=nom_fichier or "",
        details={
            "comptes": [
                {
                    "id": compte.id,
                    "numero_compte": compte.numero_compte,
                    "banque": compte.banque.nom,
                }
                for compte, _ in par_compte.values()
            ]
        },
    )

    a_creer, ignorees = [], 0

    for compte, lignes_compte in par_compte.values():
        doublons = [False] * len(lignes_compte)
        if ignorer_doublons:
            compteur = _compteur_existant(
                compte.id, [l["date_transaction"] for l in lignes_compte]
            )
            doublons = marquer_doublons(
                [
                    cle_transaction(
                        l["date_transaction"], l.get("debit"),
                        l.get("credit"), l["libelle_brut"],
                    )
                    for l in lignes_compte
                ],
                compteur,
            )

        for ligne, doublon in zip(lignes_compte, doublons):
            if doublon:
                ignorees += 1
                continue
            a_creer.append(
                TransactionBancaire(
                    compte=compte,
                    lot=lot,
                    date_transaction=ligne["date_transaction"],
                    date_valeur=ligne.get("date_valeur"),
                    reference_rapprochement=ligne.get("reference_rapprochement") or None,
                    libelle_brut=ligne["libelle_brut"],
                    debit=montant_positif(ligne.get("debit")),
                    credit=montant_positif(ligne.get("credit")),
                    solde_apres_operation=ligne.get("solde_apres_operation"),
                    type_transaction=ligne.get(
                        "type_transaction", TypeTransactionBancaire.AUTRE
                    ),
                )
            )

    TransactionBancaire.objects.bulk_create(a_creer, batch_size=500)

    lot.nombre_lignes = len(a_creer)
    lot.nombre_doublons_ignores = ignorees
    lot.save(update_fields=["nombre_lignes", "nombre_doublons_ignores"])

    return lot, len(a_creer), ignorees
