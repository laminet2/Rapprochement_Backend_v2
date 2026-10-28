"""Paramètres de règles : lecture, instantané (snapshot) et valeurs par défaut."""

from dataclasses import dataclass
from decimal import Decimal

from core.models import RegleMatching, sens_transactions
from valorix.models import TypeTransactionCaisse


@dataclass(frozen=True)
class ParametresRegle:
    """
    Copie figée d'une règle. Expose les mêmes noms d'attributs que
    ``RegleMatching`` : les services de scoring/tolérance acceptent
    indifféremment l'un ou l'autre.
    """

    type_transaction_valorix: str
    sens_transaction_document_cible: str
    fenetre_jours_avant: int
    fenetre_jours_apres: int
    tolerance_fixe: Decimal
    tolerance_pourcentage: Decimal
    regroupement_max: int = 10

    @classmethod
    def depuis_objet(cls, regle):
        return cls(
            type_transaction_valorix=regle.type_transaction_valorix,
            sens_transaction_document_cible=regle.sens_transaction_document_cible,
            fenetre_jours_avant=int(regle.fenetre_jours_avant),
            fenetre_jours_apres=int(regle.fenetre_jours_apres),
            tolerance_fixe=Decimal(str(regle.tolerance_fixe)),
            tolerance_pourcentage=Decimal(str(regle.tolerance_pourcentage)),
            regroupement_max=int(getattr(regle, "regroupement_max", 10)),
        )

    @classmethod
    def depuis_snapshot(cls, donnees):
        return cls(
            type_transaction_valorix=donnees["type_transaction_valorix"],
            sens_transaction_document_cible=donnees["sens_transaction_document_cible"],
            fenetre_jours_avant=int(donnees["fenetre_jours_avant"]),
            fenetre_jours_apres=int(donnees["fenetre_jours_apres"]),
            tolerance_fixe=Decimal(str(donnees["tolerance_fixe"])),
            tolerance_pourcentage=Decimal(str(donnees["tolerance_pourcentage"])),
            regroupement_max=int(donnees.get("regroupement_max", 10)),
        )

    def en_snapshot(self):
        """Dictionnaire JSON-compatible stocké sur le rapprochement."""
        return {
            "type_transaction_valorix": self.type_transaction_valorix,
            "sens_transaction_document_cible": self.sens_transaction_document_cible,
            "fenetre_jours_avant": self.fenetre_jours_avant,
            "fenetre_jours_apres": self.fenetre_jours_apres,
            "tolerance_fixe": str(self.tolerance_fixe),
            "tolerance_pourcentage": str(self.tolerance_pourcentage),
            "regroupement_max": self.regroupement_max,
        }


def parametres_pour_type(type_transaction, actif_seulement=True):
    """Règle (figée) applicable à un type Valorix, ou None."""
    qs = RegleMatching.objects.filter(type_transaction_valorix=type_transaction)
    if actif_seulement:
        qs = qs.filter(actif=True)
    regle = qs.first()
    return ParametresRegle.depuis_objet(regle) if regle else None


def parametres_du_rapprochement(rapprochement, type_transaction=None):
    """
    Paramètres à utiliser pour (re)valider un rapprochement : son instantané
    s'il existe, sinon la règle actuelle du type (anciennes propositions).
    """
    if rapprochement.regle_snapshot:
        return ParametresRegle.depuis_snapshot(rapprochement.regle_snapshot)
    if type_transaction:
        return parametres_pour_type(type_transaction, actif_seulement=False)
    return None


def creer_regles_par_defaut():
    """
    Crée une règle pour chaque type Valorix qui n'en a pas encore.
    Sens bancaire par défaut : DEBIT pour les retraits, CREDIT sinon
    (à vérifier selon le fonctionnement comptable réel).
    """
    existants = set(
        RegleMatching.objects.values_list("type_transaction_valorix", flat=True)
    )
    creees = []
    for valeur, _ in TypeTransactionCaisse.choices:
        if valeur in existants:
            continue
        sens = (
            sens_transactions.DEBIT if "RETRAIT" in valeur
            else sens_transactions.CREDIT
        )
        creees.append(
            RegleMatching.objects.create(
                type_transaction_valorix=valeur,
                sens_transaction_document_cible=sens,
            )
        )
    return creees
