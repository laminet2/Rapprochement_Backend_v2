
from decimal import Decimal, InvalidOperation


ZERO = Decimal("0")
CENT = Decimal("0.01")
CENT_POURCENT = Decimal("0.0001")


def convertir_decimal(valeur, nom_champ="valeur"):
    """
    Convertit une valeur en Decimal sans passer par un float.
    """
    if valeur is None:
        raise ValueError(f"{nom_champ} ne peut pas être vide.")

    try:
        resultat = Decimal(str(valeur))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{nom_champ} doit être un nombre valide.")

    if not resultat.is_finite():
        raise ValueError(f"{nom_champ} doit être un nombre fini.")

    return resultat


def calculer_tolerance(
    montant_reference,
    tolerance_fixe,
    tolerance_pourcentage,
):
    """
    Calcule la tolérance maximale autorisée.

    Formule :
        max(tolérance fixe, montant de référence * pourcentage / 100)

    Le montant de référence est exprimé en valeur absolue.
    Le pourcentage est exprimé en points de pourcentage :
        1 signifie 1 %, et non 0,01 %.
    """
    montant = abs(convertir_decimal(
        montant_reference, "montant_reference"
    ))
    fixe = convertir_decimal(tolerance_fixe, "tolerance_fixe")
    pourcentage = convertir_decimal(
        tolerance_pourcentage, "tolerance_pourcentage"
    )

    if fixe < ZERO:
        raise ValueError("La tolérance fixe ne peut pas être négative.")

    if pourcentage < ZERO:
        raise ValueError("La tolérance en pourcentage ne peut pas être négative.")

    seuil_pourcentage = (
        montant * pourcentage / Decimal("100")
    ).quantize(CENT)

    return max(fixe, seuil_pourcentage)


def evaluer_ecart(
    montant_banque,
    montant_valorix,
    tolerance_fixe,
    tolerance_pourcentage,
):
    """
    Compare les montants et renvoie le résultat de l'évaluation.

    Les montants sont comparés en valeur absolue.
    La compatibilité débit/crédit doit être vérifiée séparément.
    Valorix sert ici de montant de référence pour le pourcentage.
    """
    banque = abs(convertir_decimal(
        montant_banque, "montant_banque"
    ))
    valorix = abs(convertir_decimal(
        montant_valorix, "montant_valorix"
    ))

    ecart = abs(banque - valorix).quantize(CENT)

    tolerance = calculer_tolerance(
        montant_reference=valorix,
        tolerance_fixe=tolerance_fixe,
        tolerance_pourcentage=tolerance_pourcentage,
    )

    return {
        "montant_banque": banque,
        "montant_valorix": valorix,
        "ecart": ecart,
        "tolerance": tolerance,
        "acceptable": ecart <= tolerance,
    }
