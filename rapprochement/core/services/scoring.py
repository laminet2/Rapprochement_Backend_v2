"""
Score de correspondance (0-100), normalisé sur les critères disponibles.

Barème : montant 30, date 15, référence 25, compte 15, nom 10, sens 5.

Un critère est « disponible » seulement si la donnée existe côté Valorix
(ex. un document « autre » n'a ni référence ni nom). Le score est ramené
sur les critères disponibles, pour qu'un candidat parfait fasse 100 même
sans référence ni nom.

Chaque critère est une fraction entre 0 et 1, ce qui permet de scorer
aussi bien un rapprochement 1-1 qu'un groupe (fraction de lignes qui
correspondent).
"""

from decimal import ROUND_HALF_UP, Decimal

BAREME = {
    "montant": 30,
    "date": 15,
    "reference": 25,
    "compte": 15,
    "nom_client": 10,
    "sens": 5,
}


def _arrondi(valeur):
    return int(Decimal(valeur).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _fraction(valeur):
    """bool / int / float / Decimal -> Decimal entre 0 et 1."""
    if isinstance(valeur, bool):
        return Decimal(1) if valeur else Decimal(0)
    valeur = Decimal(str(valeur))
    return max(Decimal(0), min(Decimal(1), valeur))


def fraction_montant(ecart, tolerance):
    ecart, tolerance = Decimal(str(ecart)), Decimal(str(tolerance))
    if ecart < 0 or tolerance < 0:
        raise ValueError("L'écart et la tolérance doivent être positifs ou nuls.")
    if ecart > tolerance:
        return Decimal(0)
    if tolerance == 0:
        return Decimal(1) if ecart == 0 else Decimal(0)
    return (tolerance - ecart) / tolerance


def fraction_date(ecart_jours, regle):
    ecart_jours = int(ecart_jours)
    distance = abs(ecart_jours)
    fenetre = (
        regle.fenetre_jours_avant if ecart_jours < 0 else regle.fenetre_jours_apres
    )
    if distance == 0:
        return Decimal(1)
    if fenetre <= 0 or distance > fenetre:
        return Decimal(0)
    return Decimal(fenetre - distance) / Decimal(fenetre)


def _sens_inverse_correct(candidat):
    sens_valorix = candidat.get("sens_valorix")
    sens_banque = candidat.get("sens_banque")
    return (sens_valorix == "DEBIT" and sens_banque == "CREDIT") or (
        sens_valorix == "CREDIT" and sens_banque == "DEBIT"
    )


def _texte_montant(candidat, ecart, tolerance):
    if ecart == 0:
        return "Montants identiques."
    return f"Écart de {ecart} pour une tolérance de {tolerance}."


def _texte_date(jours):
    jours = sorted(set(abs(int(j)) for j in jours))
    if not jours or jours == [0]:
        return "Même jour."
    if len(jours) == 1:
        return f"{jours[0]} jour(s) d'écart."
    return f"Écarts de {jours[0]} à {jours[-1]} jour(s)."


def calculer_score(candidat, regle):
    """
    ``candidat`` (clés lues) :
      ecart_montant, tolerance                         (obligatoires)
      ecart_jours (int)  ou  ecarts_jours (liste)      (obligatoire)
      reference_correspond, numero_cheque_correspond   (bool ou fraction)
      reference_disponible / compte_disponible / nom_disponible  (défaut True)
      compte_correspond, nom_correspond                (bool ou fraction)
      sens_correct (bool/fraction)  ou  sens_valorix + sens_banque
                                    ou  sens_verifie (ancien format)
    """
    ecart = Decimal(str(candidat["ecart_montant"]))
    tolerance = Decimal(str(candidat["tolerance"]))

    ecarts_jours = candidat.get("ecarts_jours")
    if ecarts_jours is None:
        ecarts_jours = [candidat["ecart_jours"]]
    fractions_jours = [fraction_date(j, regle) for j in ecarts_jours]
    f_date = sum(fractions_jours) / len(fractions_jours)

    f_reference = max(
        _fraction(candidat.get("reference_correspond", False)),
        _fraction(candidat.get("numero_cheque_correspond", False)),
    )

    if "sens_correct" in candidat:
        f_sens = _fraction(candidat["sens_correct"])
    elif "sens_valorix" in candidat or "sens_banque" in candidat:
        f_sens = _fraction(_sens_inverse_correct(candidat))
    else:
        f_sens = _fraction(candidat.get("sens_verifie", False))

    criteres = {
        "montant": (fraction_montant(ecart, tolerance), True),
        "date": (f_date, True),
        "reference": (f_reference, bool(candidat.get("reference_disponible", True))),
        "compte": (
            _fraction(candidat.get("compte_correspond", False)),
            bool(candidat.get("compte_disponible", True)),
        ),
        "nom_client": (
            _fraction(candidat.get("nom_correspond", False)),
            bool(candidat.get("nom_disponible", True)),
        ),
        "sens": (f_sens, True),
    }

    obtenu = Decimal(0)
    disponible = 0
    details = {}
    for cle, (fraction, dispo) in criteres.items():
        maximum = BAREME[cle]
        points = fraction * maximum
        if dispo:
            obtenu += points
            disponible += maximum
        details[cle] = {
            "score": _arrondi(points) if dispo else 0,
            "maximum": maximum,
            "disponible": dispo,
            "correspondance": float(round(fraction, 4)),
        }

    score = _arrondi(Decimal(100) * obtenu / disponible) if disponible else 0

    details["montant"].update(
        ecart=str(ecart),
        tolerance=str(tolerance),
        explication=_texte_montant(candidat, ecart, tolerance),
    )
    details["date"].update(
        ecart_jours=candidat.get("ecart_jours"),
        ecarts_jours=list(ecarts_jours),
        explication=_texte_date(ecarts_jours),
    )

    def phrase(cle, ok_txt, ko_txt, absent_txt):
        d = details[cle]
        if not d["disponible"]:
            d["explication"] = absent_txt
        elif d["correspondance"] >= 1:
            d["explication"] = ok_txt
        elif d["correspondance"] > 0:
            d["explication"] = f"{round(d['correspondance'] * 100)} % des lignes concordent."
        else:
            d["explication"] = ko_txt

    phrase(
        "reference",
        "Référence retrouvée dans le libellé ou la référence bancaire.",
        "Aucune référence Valorix retrouvée côté banque.",
        "Non évalué : aucune référence côté Valorix.",
    )
    phrase(
        "compte",
        "Numéro de compte retrouvé dans le libellé bancaire.",
        "Numéro de compte absent du libellé bancaire.",
        "Non évalué : aucun numéro de compte côté Valorix.",
    )
    phrase(
        "nom_client",
        "Nom du client retrouvé dans le libellé bancaire.",
        "Nom du client absent du libellé bancaire.",
        "Non évalué : nom du client indisponible.",
    )
    phrase(
        "sens",
        "Sens bancaire inverse du sens Valorix, comme attendu.",
        "Sens incohérent entre banque et Valorix.",
        "",
    )

    details["reference"]["correspond"] = bool(f_reference >= 1)
    details["reference"]["numero_cheque_correspond"] = bool(
        _fraction(candidat.get("numero_cheque_correspond", False)) >= 1
    )
    details["compte"]["correspond"] = details["compte"]["correspondance"] >= 1
    details["nom_client"]["correspond"] = details["nom_client"]["correspondance"] >= 1
    details["sens"]["verifie"] = bool(f_sens >= 1)
    details["sens"]["sens_valorix"] = candidat.get("sens_valorix")
    details["sens"]["sens_banque"] = candidat.get("sens_banque")
    details["total"] = {"obtenu": _arrondi(obtenu), "disponible": disponible}

    return {"score": score, "details": details}


def classer_candidats(candidats, regle):
    """Ajoute un score à chaque candidat et classe du meilleur au moins bon."""
    resultats = []
    for candidat in candidats:
        evaluation = calculer_score(candidat, regle)
        resultats.append(
            {
                **candidat,
                "score_correspondance": evaluation["score"],
                "score_details": evaluation["details"],
            }
        )

    def jours(item):
        liste = item.get("ecarts_jours") or [item.get("ecart_jours", 0)]
        return sum(abs(int(j)) for j in liste)

    resultats.sort(
        key=lambda i: (-i["score_correspondance"], i["ecart_montant"], jours(i))
    )
    return resultats
