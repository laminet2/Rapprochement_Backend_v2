"""Décision groupée : rejeter une sélection, valider tout le reste."""

from django.db.models import Q

from core.models import Rapprochement, StatutValidation
from core.services.actions import ActionRapprochementError, rejeter_rapprochement
from core.services.validation import (
    ConflitRapprochementError,
    ValidationRapprochementError,
    valider_rapprochement,
)


def propositions_en_attente(ids=None, lot_valorix_ids=None, lot_banque_ids=None):
    """Propositions PROPOSE, éventuellement limitées à des ids ou à des lots."""
    qs = Rapprochement.objects.filter(statut_validation=StatutValidation.PROPOSE)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    if lot_valorix_ids:
        qs = qs.filter(transactions_valorix__transaction__lot_id__in=lot_valorix_ids)
    if lot_banque_ids:
        qs = qs.filter(transactions_bancaires__transaction__lot_id__in=lot_banque_ids)
    return qs.distinct()


def decision_groupee(
    ids_rejetes=(), motif_rejet="", ids=None, lot_valorix_ids=None,
    lot_banque_ids=None, valider_le_reste=True, utilisateur=None,
):
    """
    1. rejette les propositions de ``ids_rejetes`` ;
    2. valide toutes les autres propositions en attente du périmètre
       (``ids`` et/ou lots ; sans filtre = toutes), de la meilleure à la
       moins bonne.

    Une proposition en conflit (transaction déjà prise par une proposition
    validée juste avant) n'est PAS validée : elle reste en attente et est
    listée dans ``conflits`` pour que l'utilisateur tranche.
    """
    ids_rejetes = [str(i) for i in ids_rejetes]
    bilan = {"rejetes": [], "valides": [], "conflits": [], "echecs": []}

    for rid in ids_rejetes:
        try:
            rejeter_rapprochement(
                rid, utilisateur, motif_rejet or "Rejeté lors de la revue groupée."
            )
            bilan["rejetes"].append(rid)
        except ActionRapprochementError as exc:
            bilan["echecs"].append({"id": rid, "detail": str(exc)})

    if valider_le_reste:
        cibles = (
            propositions_en_attente(ids, lot_valorix_ids, lot_banque_ids)
            .exclude(pk__in=ids_rejetes)
            .order_by("-score_correspondance", "cree_le")
        )
        for rapprochement in list(cibles):
            try:
                valider_rapprochement(rapprochement.pk, utilisateur)
                bilan["valides"].append(str(rapprochement.pk))
            except ConflitRapprochementError as exc:
                bilan["conflits"].append({"id": str(rapprochement.pk), "detail": str(exc)})
            except ValidationRapprochementError as exc:
                bilan["echecs"].append({"id": str(rapprochement.pk), "detail": str(exc)})

    bilan["resume"] = {k: len(v) for k, v in bilan.items() if isinstance(v, list)}
    return bilan


def annuler_propositions_bloquees(utilisateur=None):
    """
    Annule les propositions en attente dont une transaction est déjà
    rapprochée (rapprochement VALIDE) : elles ne pourront plus être validées.
    """
    from core.services.actions import annuler_rapprochement

    bloquees = Rapprochement.objects.filter(
        statut_validation=StatutValidation.PROPOSE
    ).filter(
        Q(
            transactions_bancaires__transaction__liens_rapprochement__rapprochement__statut_validation=(
                StatutValidation.VALIDE
            )
        )
        | Q(
            transactions_valorix__transaction__liens_rapprochement__rapprochement__statut_validation=(
                StatutValidation.VALIDE
            )
        )
    ).distinct()

    ids = [str(r.pk) for r in bloquees]
    for rid in ids:
        annuler_rapprochement(
            rid, utilisateur, "Annulé automatiquement : transaction déjà rapprochée."
        )
    return ids
