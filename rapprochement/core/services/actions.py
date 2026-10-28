"""Rejet et annulation de rapprochements."""

from django.db import transaction
from django.utils import timezone

from core.models import Rapprochement, StatutValidation


class ActionRapprochementError(Exception):
    pass


def _auteur(utilisateur):
    if utilisateur is not None and getattr(utilisateur, "is_authenticated", False):
        return utilisateur.get_username()
    return "anonyme"


def _horodater(rapprochement, verbe, utilisateur, motif):
    ligne = (
        f"[{verbe} le {timezone.localtime():%Y-%m-%d %H:%M} "
        f"par {_auteur(utilisateur)}] {motif.strip()}"
    )
    rapprochement.commentaire = (
        f"{rapprochement.commentaire}\n{ligne}".strip()
    )


@transaction.atomic
def rejeter_rapprochement(rapprochement_id, utilisateur=None, motif=""):
    """Rejette une proposition en attente (elle ne sera plus reproposée)."""
    if not (motif or "").strip():
        raise ActionRapprochementError("Un motif est obligatoire.")

    try:
        rapprochement = Rapprochement.objects.select_for_update().get(pk=rapprochement_id)
    except Rapprochement.DoesNotExist:
        raise ActionRapprochementError("Rapprochement introuvable.")

    if rapprochement.statut_validation != StatutValidation.PROPOSE:
        raise ActionRapprochementError(
            "Seule une proposition en attente peut être rejetée."
        )

    rapprochement.statut_validation = StatutValidation.REJETE
    _horodater(rapprochement, "Rejeté", utilisateur, motif)
    rapprochement.save(update_fields=["statut_validation", "commentaire"])
    return rapprochement


@transaction.atomic
def annuler_rapprochement(rapprochement_id, utilisateur=None, motif=""):
    """
    Annule une proposition en attente OU un rapprochement déjà validé
    (correction d'une erreur : les transactions redeviennent non rapprochées).
    """
    if not (motif or "").strip():
        raise ActionRapprochementError("Un motif est obligatoire.")

    try:
        rapprochement = Rapprochement.objects.select_for_update().get(pk=rapprochement_id)
    except Rapprochement.DoesNotExist:
        raise ActionRapprochementError("Rapprochement introuvable.")

    if rapprochement.statut_validation not in (
        StatutValidation.PROPOSE, StatutValidation.VALIDE
    ):
        raise ActionRapprochementError(
            "Seul un rapprochement proposé ou validé peut être annulé."
        )

    etait_valide = rapprochement.statut_validation == StatutValidation.VALIDE
    rapprochement.statut_validation = StatutValidation.ANNULE
    rapprochement.valide_le = None
    _horodater(
        rapprochement,
        "Validation annulée" if etait_valide else "Annulé",
        utilisateur, motif,
    )
    rapprochement.save(
        update_fields=["statut_validation", "valide_le", "commentaire"]
    )
    return rapprochement
