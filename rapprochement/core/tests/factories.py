"""Fabriques de données pour les tests."""

from datetime import datetime
from decimal import Decimal
from itertools import count

from django.utils import timezone

from core.models import RegleMatching
from releverBancaire.models import Banque, CompteBancaire, TransactionBancaire
from valorix.models import TransactionValorix

_compteur = count(1)


def creer_compte():
    banque, _ = Banque.objects.get_or_create(nom="Banque Test")
    compte, _ = CompteBancaire.objects.get_or_create(
        banque=banque, numero_compte="COMPTE-001", defaults={"devise": "XOF"}
    )
    return compte


def tx_banque(date, montant, sens="CREDIT", libelle="VIREMENT", ref=None, lot=None, compte=None):
    return TransactionBancaire.objects.create(
        compte=compte or creer_compte(),
        date_transaction=date,
        libelle_brut=libelle,
        reference_rapprochement=ref,
        debit=Decimal(str(montant)) if sens == "DEBIT" else None,
        credit=Decimal(str(montant)) if sens == "CREDIT" else None,
        lot=lot,
    )


def tx_valorix(date, montant, sens="DEBIT", types="DEPOT_CHEQUE", compte="", nom="",
               refs="", lot=None):
    jour = datetime(date.year, date.month, date.day, 10, 0)
    return TransactionValorix.objects.create(
        numero_transaction=str(next(_compteur)),
        date_transaction=timezone.make_aware(jour),
        numero_compte=compte,
        nomComplet=nom,
        debit=Decimal(str(montant)) if sens == "DEBIT" else None,
        credit=Decimal(str(montant)) if sens == "CREDIT" else None,
        references=refs,
        types=types,
        lot=lot,
    )


def creer_regle(types="DEPOT_CHEQUE", sens="CREDIT", avant=2, apres=2,
                fixe=0, pct=0, groupe_max=10, actif=True):
    return RegleMatching.objects.create(
        type_transaction_valorix=types,
        sens_transaction_document_cible=sens,
        fenetre_jours_avant=avant,
        fenetre_jours_apres=apres,
        tolerance_fixe=Decimal(str(fixe)),
        tolerance_pourcentage=Decimal(str(pct)),
        regroupement_max=groupe_max,
        actif=actif,
    )
