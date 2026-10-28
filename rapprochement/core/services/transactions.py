"""Transactions non rapprochées et suppression sécurisée."""

from django.db import transaction
from django.db.models import Count, Q

from core.models import (
    Rapprochement,
    StatutValidation,
    TransactionBancaireRapprochee,
    TransactionValorixRapprochee,
)
from releverBancaire.models import TransactionBancaire
from valorix.models import TransactionValorix

SOURCES = {
    "banque": (TransactionBancaire, TransactionBancaireRapprochee),
    "valorix": (TransactionValorix, TransactionValorixRapprochee),
}


def _source(source):
    if source not in SOURCES:
        raise ValueError("La source doit être 'banque' ou 'valorix'.")
    return SOURCES[source]


def est_rapprochee(source, pk):
    _, lien = _source(source)
    return lien.objects.filter(
        transaction_id=pk,
        rapprochement__statut_validation=StatutValidation.VALIDE,
    ).exists()


def queryset_non_rapprochees(source):
    """
    Transactions sans rapprochement VALIDE, avec le nombre de propositions
    en attente (``propositions_en_attente``).
    """
    modele, _ = _source(source)
    valide = Q(
        liens_rapprochement__rapprochement__statut_validation=StatutValidation.VALIDE
    )
    return (
        modele.objects.exclude(valide)
        .annotate(
            propositions_en_attente=Count(
                "liens_rapprochement",
                filter=Q(
                    liens_rapprochement__rapprochement__statut_validation=(
                        StatutValidation.PROPOSE
                    )
                ),
                distinct=True,
            )
        )
    )


@transaction.atomic
def supprimer_transactions(source, ids, simuler=False):
    """
    Supprime des transactions non rapprochées.

    Refus pour celles qui ont un rapprochement VALIDE. Les propositions
    (en attente, rejetées ou annulées) qui les contiennent sont supprimées
    avec elles. ``simuler=True`` n'écrit rien et retourne le même bilan.
    """
    modele, lien = _source(source)
    ids = list(dict.fromkeys(ids))

    existantes = set(modele.objects.filter(pk__in=ids).values_list("pk", flat=True))
    introuvables = [i for i in ids if i not in existantes]

    validees = set(
        lien.objects.filter(
            transaction_id__in=existantes,
            rapprochement__statut_validation=StatutValidation.VALIDE,
        ).values_list("transaction_id", flat=True)
    )
    refusees = [
        {
            "id": i,
            "raison": (
                "Transaction déjà rapprochée : annulez d'abord le "
                "rapprochement."
            ),
        }
        for i in existantes
        if i in validees
    ]
    a_supprimer = existantes - validees

    rapprochements = set(
        lien.objects.filter(transaction_id__in=a_supprimer)
        .values_list("rapprochement_id", flat=True)
    )

    if not simuler:
        Rapprochement.objects.filter(pk__in=rapprochements).delete()
        modele.objects.filter(pk__in=a_supprimer).delete()

    return {
        "supprimees": len(a_supprimer),
        "propositions_supprimees": len(rapprochements),
        "refusees": refusees,
        "introuvables": introuvables,
    }
