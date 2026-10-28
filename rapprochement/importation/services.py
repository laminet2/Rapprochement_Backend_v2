from django.db import transaction
from django.utils import timezone

from core.models import StatutValidation
from core.services.transactions import supprimer_transactions
from releverBancaire.models import TransactionBancaire
from valorix.models import TransactionValorix


class AnnulationLotError(Exception):
    pass


@transaction.atomic
def annuler_lot(lot):
    """
    Supprime toutes les transactions d'un lot, si aucune n'est déjà
    rapprochée (rapprochement VALIDE). Sinon rien n'est supprimé.
    """
    if lot.annule_le:
        raise AnnulationLotError("Ce lot est déjà annulé.")

    ids_banque = list(TransactionBancaire.objects.filter(lot=lot).values_list("pk", flat=True))
    ids_valorix = list(TransactionValorix.objects.filter(lot=lot).values_list("pk", flat=True))

    resultat_b = supprimer_transactions("banque", ids_banque, simuler=True)
    resultat_v = supprimer_transactions("valorix", ids_valorix, simuler=True)
    refusees = resultat_b["refusees"] + resultat_v["refusees"]

    if refusees:
        raise AnnulationLotError(
            f"{len(refusees)} transaction(s) de ce lot sont déjà rapprochées. "
            "Annulez d'abord ces rapprochements avant d'annuler l'import."
        )

    supprimer_transactions("banque", ids_banque)
    supprimer_transactions("valorix", ids_valorix)

    lot.annule_le = timezone.now()
    lot.save(update_fields=["annule_le"])
    return {"supprimees": len(ids_banque) + len(ids_valorix)}
