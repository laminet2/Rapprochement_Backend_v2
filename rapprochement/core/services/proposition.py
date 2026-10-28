"""Création / mise à jour des propositions de rapprochement en base."""

from django.db import transaction

from core.models import (
    OrigineRapprochement,
    Rapprochement,
    RegleMatching,
    ResultatMatching,
    StatutValidation,
    TransactionBancaireRapprochee,
    TransactionValorixRapprochee,
)
from core.services.matching import rechercher_propositions
from core.services.regles import ParametresRegle
from valorix.models import TransactionValorix


def _par_lots(valeurs, taille=500):
    valeurs = list(valeurs)
    for i in range(0, len(valeurs), taille):
        yield valeurs[i:i + taille]


def charger_existants(ids_banque, ids_valorix):
    """
    Rapprochements (tous statuts) qui touchent ces transactions.
    Retourne {(frozenset(ids banque), frozenset(ids valorix)): (id, statut)}.
    """
    rids = set()
    for lot in _par_lots(ids_banque):
        rids.update(
            TransactionBancaireRapprochee.objects.filter(transaction_id__in=lot)
            .values_list("rapprochement_id", flat=True)
        )
    for lot in _par_lots(ids_valorix):
        rids.update(
            TransactionValorixRapprochee.objects.filter(transaction_id__in=lot)
            .values_list("rapprochement_id", flat=True)
        )
    if not rids:
        return {}

    banque, valorix, statuts = {}, {}, {}
    for lot in _par_lots(rids):
        for rid, tid in TransactionBancaireRapprochee.objects.filter(
            rapprochement_id__in=lot
        ).values_list("rapprochement_id", "transaction_id"):
            banque.setdefault(rid, set()).add(tid)
        for rid, tid in TransactionValorixRapprochee.objects.filter(
            rapprochement_id__in=lot
        ).values_list("rapprochement_id", "transaction_id"):
            valorix.setdefault(rid, set()).add(tid)
        statuts.update(
            Rapprochement.objects.filter(id__in=lot)
            .values_list("id", "statut_validation")
        )

    return {
        (frozenset(banque.get(rid, ())), frozenset(valorix.get(rid, ()))): (rid, statut)
        for rid, statut in statuts.items()
    }


def _details(candidat):
    details = dict(candidat["score_details"])
    details["type_rapprochement"] = candidat["type_rapprochement"]
    details["nombre_banque"] = len(candidat["lignes_banque"])
    details["nombre_valorix"] = len(candidat["lignes_valorix"])
    details["montant_banque"] = str(candidat["montant_banque"])
    details["montant_valorix"] = str(candidat["montant_valorix"])
    return details


@transaction.atomic
def generer_pour_regle(params, lot_valorix_ids=None, lot_banque_ids=None,
                       inclure_regroupements=True):
    """Génère les propositions pour UNE règle (un type Valorix)."""
    candidats, stats = rechercher_propositions(
        params, lot_valorix_ids, lot_banque_ids, inclure_regroupements
    )

    ids_b = {l.id for c in candidats for l in c["lignes_banque"]}
    ids_v = {l.id for c in candidats for l in c["lignes_valorix"]}
    existants = charger_existants(ids_b, ids_v)
    snapshot = params.en_snapshot()

    a_creer, liens_b, liens_v, a_maj = [], [], [], []
    bilan = {
        "type_transaction_valorix": params.type_transaction_valorix,
        **stats,
        "propositions_creees": 0,
        "propositions_actualisees": 0,
        "propositions_deja_traitees": 0,
        "propositions_1_1": 0,
        "propositions_regroupees": 0,
        "propositions_ids": [],
    }

    for c in candidats:
        cle = (
            frozenset(l.id for l in c["lignes_banque"]),
            frozenset(l.id for l in c["lignes_valorix"]),
        )
        existant = existants.get(cle)

        if existant:
            rid, statut = existant
            if statut == StatutValidation.PROPOSE:
                a_maj.append((rid, c))
            else:
                bilan["propositions_deja_traitees"] += 1
            continue

        rapprochement = Rapprochement(
            statut_validation=StatutValidation.PROPOSE,
            resultat=ResultatMatching.CORRESPONDANCE,
            origine=OrigineRapprochement.AUTO,
            score_correspondance=c["score_correspondance"],
            ecart_montant=c["ecart_montant"],
            score_details=_details(c),
            regle_snapshot=snapshot,
        )
        a_creer.append(rapprochement)
        liens_b += [
            TransactionBancaireRapprochee(rapprochement=rapprochement, transaction_id=l.id)
            for l in c["lignes_banque"]
        ]
        liens_v += [
            TransactionValorixRapprochee(rapprochement=rapprochement, transaction_id=l.id)
            for l in c["lignes_valorix"]
        ]
        bilan["propositions_creees"] += 1
        bilan["propositions_ids"].append(str(rapprochement.id))
        bilan[
            "propositions_1_1" if c["type_rapprochement"] == "1-1"
            else "propositions_regroupees"
        ] += 1

    Rapprochement.objects.bulk_create(a_creer, batch_size=500)
    TransactionBancaireRapprochee.objects.bulk_create(liens_b, batch_size=500)
    TransactionValorixRapprochee.objects.bulk_create(liens_v, batch_size=500)

    for rid, c in a_maj:
        Rapprochement.objects.filter(pk=rid).update(
            score_correspondance=c["score_correspondance"],
            ecart_montant=c["ecart_montant"],
            score_details=_details(c),
            regle_snapshot=snapshot,
        )
        bilan["propositions_actualisees"] += 1
        bilan["propositions_ids"].append(str(rid))

    return bilan


def generer_toutes_les_propositions(
    lot_valorix_ids=None, lot_banque_ids=None, types=None,
    inclure_regroupements=True,
):
    """Applique toutes les règles actives (une par type) et agrège les bilans."""
    regles = RegleMatching.objects.filter(actif=True)
    if types:
        regles = regles.filter(type_transaction_valorix__in=types)

    par_type = [
        generer_pour_regle(
            ParametresRegle.depuis_objet(regle),
            lot_valorix_ids, lot_banque_ids, inclure_regroupements,
        )
        for regle in regles
    ]

    # Types présents côté Valorix mais sans règle active : à signaler.
    qs = TransactionValorix.objects.all()
    if lot_valorix_ids:
        qs = qs.filter(lot_id__in=lot_valorix_ids)
    types_presents = set(qs.values_list("types", flat=True).distinct())
    types_avec_regle = {b["type_transaction_valorix"] for b in par_type}
    if types:
        types_presents &= set(types)

    total = lambda cle: sum(b[cle] for b in par_type)
    return {
        "propositions_creees": total("propositions_creees"),
        "propositions_actualisees": total("propositions_actualisees"),
        "propositions_deja_traitees": total("propositions_deja_traitees"),
        "propositions_1_1": total("propositions_1_1"),
        "propositions_regroupees": total("propositions_regroupees"),
        "valorix_examinees": total("valorix_examinees"),
        "banque_examinees": total("banque_examinees"),
        "types_sans_regle": sorted(types_presents - types_avec_regle),
        "details_par_type": par_type,
    }
