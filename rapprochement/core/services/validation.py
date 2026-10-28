"""Validation d'un rapprochement (1-1, 1-N, N-1) avec revérification complète."""

from django.db import transaction
from django.utils import timezone

from core.models import (
    Rapprochement,
    ResultatMatching,
    StatutValidation,
    TransactionBancaireRapprochee,
    TransactionValorixRapprochee,
)
from core.services.candidats import construire_ligne
from core.services.regles import parametres_du_rapprochement
from core.services.tolerance import evaluer_ecart
from releverBancaire.models import TransactionBancaire
from valorix.models import TransactionValorix


class ValidationRapprochementError(Exception):
    """Le rapprochement ne peut pas être validé."""


class ConflitRapprochementError(ValidationRapprochementError):
    """Une transaction est déjà utilisée par un rapprochement validé."""


def verifier_fenetres(lignes_banque, lignes_valorix, params):
    """True si chaque paire (banque, Valorix) respecte la fenêtre de dates."""
    for b in lignes_banque:
        for v in lignes_valorix:
            ecart = (b.date - v.date).days
            if ecart < -params.fenetre_jours_avant or ecart > params.fenetre_jours_apres:
                return False
    return True


def evaluer_ensemble(lignes_banque, lignes_valorix, params):
    """Évalue les sommes des montants selon la règle (tolérance)."""
    total_b = sum((l.montant for l in lignes_banque), 0)
    total_v = sum((l.montant for l in lignes_valorix), 0)
    return evaluer_ecart(
        montant_banque=total_b,
        montant_valorix=total_v,
        tolerance_fixe=params.tolerance_fixe,
        tolerance_pourcentage=params.tolerance_pourcentage,
    )


def _lignes(transactions, nom):
    lignes = []
    for tx in transactions:
        ligne = construire_ligne(tx)
        if ligne is None:
            raise ValidationRapprochementError(
                f"La transaction {nom} {tx.pk} a un montant ambigu ou nul."
            )
        lignes.append(ligne)
    return lignes


@transaction.atomic
def valider_rapprochement(rapprochement_id, utilisateur=None):
    """
    Valide une proposition après avoir revérifié, sur les données actuelles :
    statut, existence des liens, absence de conflit, règle, sens bancaire,
    fenêtres de dates et tolérance sur la somme des montants.
    """
    try:
        rapprochement = Rapprochement.objects.select_for_update().get(
            pk=rapprochement_id
        )
    except Rapprochement.DoesNotExist:
        raise ValidationRapprochementError("Rapprochement introuvable.")

    if rapprochement.statut_validation != StatutValidation.PROPOSE:
        raise ValidationRapprochementError(
            "Seule une proposition en attente peut être validée."
        )
    if rapprochement.resultat != ResultatMatching.CORRESPONDANCE:
        raise ValidationRapprochementError(
            "Ce rapprochement n'est pas une correspondance valide."
        )

    ids_b = list(
        TransactionBancaireRapprochee.objects.filter(rapprochement=rapprochement)
        .order_by("transaction_id").values_list("transaction_id", flat=True)
    )
    ids_v = list(
        TransactionValorixRapprochee.objects.filter(rapprochement=rapprochement)
        .order_by("transaction_id").values_list("transaction_id", flat=True)
    )
    if not ids_b or not ids_v:
        raise ValidationRapprochementError(
            "Le rapprochement doit contenir au moins une transaction "
            "bancaire et une transaction Valorix."
        )

    # Verrouillage dans un ordre stable (évite les inter-blocages).
    banques = list(TransactionBancaire.objects.select_for_update().filter(pk__in=ids_b).order_by("pk"))
    valorix = list(TransactionValorix.objects.select_for_update().filter(pk__in=ids_v).order_by("pk"))

    if (
        TransactionBancaireRapprochee.objects.filter(
            transaction_id__in=ids_b,
            rapprochement__statut_validation=StatutValidation.VALIDE,
        ).exclude(rapprochement=rapprochement).exists()
    ):
        raise ConflitRapprochementError(
            "Une transaction bancaire de ce rapprochement est déjà rapprochée."
        )
    if (
        TransactionValorixRapprochee.objects.filter(
            transaction_id__in=ids_v,
            rapprochement__statut_validation=StatutValidation.VALIDE,
        ).exclude(rapprochement=rapprochement).exists()
    ):
        raise ConflitRapprochementError(
            "Une transaction Valorix de ce rapprochement est déjà rapprochée."
        )

    lignes_b = _lignes(banques, "bancaire")
    lignes_v = _lignes(valorix, "Valorix")

    types = {l.tx.types for l in lignes_v}
    if len(types) != 1:
        raise ValidationRapprochementError(
            "Les transactions Valorix doivent toutes avoir le même type."
        )
    type_tx = next(iter(types))

    params = parametres_du_rapprochement(rapprochement, type_tx)
    if params is None:
        raise ValidationRapprochementError(
            f"Aucune règle de rapprochement pour le type {type_tx}."
        )
    if params.type_transaction_valorix != type_tx:
        raise ValidationRapprochementError(
            "Le type des transactions Valorix ne correspond pas à la règle."
        )

    if any(l.sens != params.sens_transaction_document_cible for l in lignes_b):
        raise ValidationRapprochementError(
            "Le sens des transactions bancaires ne correspond pas à celui "
            "attendu par la règle."
        )

    # Les fenêtres ne sont vérifiées que pour les groupes « 1 contre N ».
    if (len(lignes_b) == 1 or len(lignes_v) == 1) and not verifier_fenetres(
        lignes_b, lignes_v, params
    ):
        raise ValidationRapprochementError(
            "Les dates ne respectent plus la fenêtre de la règle."
        )

    evaluation = evaluer_ensemble(lignes_b, lignes_v, params)
    if not evaluation["acceptable"]:
        raise ValidationRapprochementError(
            f"L'écart de montant ({evaluation['ecart']}) dépasse la "
            f"tolérance ({evaluation['tolerance']})."
        )

    rapprochement.statut_validation = StatutValidation.VALIDE
    rapprochement.ecart_montant = evaluation["ecart"]
    rapprochement.valide_le = timezone.now()
    if not rapprochement.regle_snapshot:
        rapprochement.regle_snapshot = params.en_snapshot()
    rapprochement.save(
        update_fields=["statut_validation", "ecart_montant", "valide_le", "regle_snapshot"]
    )
    return rapprochement
