"""Rapprochement manuel : aperçu en direct et création (N lignes contre M lignes)."""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import (
    OrigineRapprochement,
    Rapprochement,
    ResultatMatching,
    StatutValidation,
    TransactionBancaireRapprochee,
    TransactionValorixRapprochee,
)
from core.services.candidats import construire_ligne, type_de_groupe
from core.services.regles import ParametresRegle, parametres_pour_type
from core.services.validation import (
    ValidationRapprochementError,
    evaluer_ensemble,
    verifier_fenetres,
)
from releverBancaire.models import TransactionBancaire
from valorix.models import TransactionValorix


class EcartHorsToleranceError(ValidationRapprochementError):
    """L'écart dépasse la tolérance : ``forcer`` + un motif sont nécessaires."""

    def __init__(self, message, apercu):
        super().__init__(message)
        self.apercu = apercu


def _unique(ids, nom):
    ids = list(ids or [])
    if not ids:
        raise ValidationRapprochementError(
            f"Sélectionnez au moins une transaction {nom}."
        )
    if len(set(ids)) != len(ids):
        raise ValidationRapprochementError(
            f"Une même transaction {nom} est sélectionnée plusieurs fois."
        )
    return ids


def _charger(ids_banque, ids_valorix):
    ids_banque = _unique(ids_banque, "bancaire")
    ids_valorix = _unique(ids_valorix, "Valorix")

    banques = list(TransactionBancaire.objects.filter(pk__in=ids_banque))
    valorix = list(TransactionValorix.objects.filter(pk__in=ids_valorix))
    if len(banques) != len(ids_banque) or len(valorix) != len(ids_valorix):
        raise ValidationRapprochementError("Une ou plusieurs transactions sont introuvables.")

    for tx_liste, lien, nom in (
        (banques, TransactionBancaireRapprochee, "bancaire"),
        (valorix, TransactionValorixRapprochee, "Valorix"),
    ):
        deja = lien.objects.filter(
            transaction_id__in=[t.pk for t in tx_liste],
            rapprochement__statut_validation=StatutValidation.VALIDE,
        ).values_list("transaction_id", flat=True)
        if deja:
            raise ValidationRapprochementError(
                f"{len(set(deja))} transaction(s) {nom} déjà rapprochée(s)."
            )

    lignes_b, lignes_v = [], []
    for tx, cible, nom in (
        *((t, lignes_b, "bancaire") for t in banques),
        *((t, lignes_v, "Valorix") for t in valorix),
    ):
        ligne = construire_ligne(tx)
        if ligne is None:
            raise ValidationRapprochementError(
                f"La transaction {nom} {tx.pk} a un montant ambigu ou nul."
            )
        cible.append(ligne)

    for lignes, nom in ((lignes_b, "bancaires"), (lignes_v, "Valorix")):
        if len({l.sens for l in lignes}) > 1:
            raise ValidationRapprochementError(
                f"Les transactions {nom} sélectionnées mélangent débits et crédits."
            )
    return lignes_b, lignes_v


def _parametres(lignes_b, lignes_v):
    """Règle du type Valorix si unique ; sinon tolérance nulle."""
    types = {l.tx.types for l in lignes_v}
    if len(types) == 1:
        params = parametres_pour_type(next(iter(types)), actif_seulement=False)
        if params:
            return params, True
    return (
        ParametresRegle(
            type_transaction_valorix=next(iter(types)) if len(types) == 1 else "",
            sens_transaction_document_cible=lignes_b[0].sens,
            fenetre_jours_avant=0,
            fenetre_jours_apres=0,
            tolerance_fixe=Decimal(0),
            tolerance_pourcentage=Decimal(0),
        ),
        False,
    )


def apercu_manuel(ids_banque, ids_valorix):
    """Totaux, écart et tolérance d'une sélection (pour l'affichage en direct)."""
    lignes_b, lignes_v = _charger(ids_banque, ids_valorix)
    params, regle_trouvee = _parametres(lignes_b, lignes_v)
    evaluation = evaluer_ensemble(lignes_b, lignes_v, params)

    avertissements = []
    if not regle_trouvee:
        avertissements.append(
            "Aucune règle unique applicable : tolérance nulle appliquée."
        )
    if regle_trouvee and lignes_b[0].sens != params.sens_transaction_document_cible:
        avertissements.append(
            "Le sens bancaire diffère de celui attendu par la règle."
        )
    if regle_trouvee and (len(lignes_b) == 1 or len(lignes_v) == 1) and not verifier_fenetres(
        lignes_b, lignes_v, params
    ):
        avertissements.append("Les dates sortent de la fenêtre de la règle.")
    if lignes_b[0].sens == lignes_v[0].sens:
        avertissements.append("Banque et Valorix ont le même sens (débit/crédit).")

    return {
        "type_rapprochement": type_de_groupe(len(lignes_b), len(lignes_v)),
        "nombre_banque": len(lignes_b),
        "nombre_valorix": len(lignes_v),
        "total_banque": str(evaluation["montant_banque"]),
        "total_valorix": str(evaluation["montant_valorix"]),
        "ecart": str(evaluation["ecart"]),
        "tolerance": str(evaluation["tolerance"]),
        "acceptable": evaluation["acceptable"],
        "regle_appliquee": params.en_snapshot() if regle_trouvee else None,
        "avertissements": avertissements,
    }


@transaction.atomic
def creer_rapprochement_manuel(ids_banque, ids_valorix, motif="", forcer=False):
    """
    Crée directement un rapprochement VALIDE (origine MANUEL).

    Si l'écart dépasse la tolérance, ``forcer=True`` et un motif sont
    obligatoires ; le résultat est alors marqué ECART_MONTANT.
    """
    lignes_b, lignes_v = _charger(ids_banque, ids_valorix)
    params, regle_trouvee = _parametres(lignes_b, lignes_v)
    evaluation = evaluer_ensemble(lignes_b, lignes_v, params)
    apercu = apercu_manuel(ids_banque, ids_valorix)

    resultat = ResultatMatching.CORRESPONDANCE
    if not evaluation["acceptable"]:
        if not forcer or not (motif or "").strip():
            raise EcartHorsToleranceError(
                f"L'écart ({evaluation['ecart']}) dépasse la tolérance "
                f"({evaluation['tolerance']}). Pour valider quand même, "
                "indiquez un motif et confirmez (forcer=true).",
                apercu,
            )
        resultat = ResultatMatching.ECART_MONTANT

    rapprochement = Rapprochement.objects.create(
        statut_validation=StatutValidation.VALIDE,
        resultat=resultat,
        origine=OrigineRapprochement.MANUEL,
        score_correspondance=0,
        ecart_montant=evaluation["ecart"],
        score_details={"origine": "MANUEL", **{
            k: apercu[k] for k in (
                "type_rapprochement", "nombre_banque", "nombre_valorix",
                "total_banque", "total_valorix", "ecart", "tolerance",
                "avertissements",
            )
        }},
        regle_snapshot=params.en_snapshot() if regle_trouvee else {},
        commentaire=(motif or "").strip(),
        valide_le=timezone.now(),
    )
    TransactionBancaireRapprochee.objects.bulk_create(
        TransactionBancaireRapprochee(rapprochement=rapprochement, transaction_id=l.id)
        for l in lignes_b
    )
    TransactionValorixRapprochee.objects.bulk_create(
        TransactionValorixRapprochee(rapprochement=rapprochement, transaction_id=l.id)
        for l in lignes_v
    )
    return rapprochement
