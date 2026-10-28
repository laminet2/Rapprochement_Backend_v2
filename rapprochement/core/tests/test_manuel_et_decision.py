from datetime import date

from django.test import TestCase

from core.models import Rapprochement, StatutValidation
from core.services.decision import annuler_propositions_bloquees, decision_groupee
from core.services.manuel import (
    EcartHorsToleranceError,
    apercu_manuel,
    creer_rapprochement_manuel,
)
from core.services.proposition import generer_toutes_les_propositions
from core.services.transactions import queryset_non_rapprochees, supprimer_transactions
from core.services.validation import ValidationRapprochementError

from .factories import creer_regle, tx_banque, tx_valorix


class ManuelTests(TestCase):
    def setUp(self):
        creer_regle(avant=2, apres=2, fixe=10)

    def test_apercu_somme_et_ecart(self):
        b = tx_banque(date(2026, 10, 5), 600, "CREDIT")
        v1 = tx_valorix(date(2026, 10, 5), 250, "DEBIT")
        v2 = tx_valorix(date(2026, 10, 5), 350, "DEBIT")
        a = apercu_manuel([b.pk], [v1.pk, v2.pk])
        self.assertEqual(a["type_rapprochement"], "1-N")
        self.assertEqual((a["total_banque"], a["total_valorix"], a["ecart"]), ("600.00", "600.00", "0.00"))
        self.assertTrue(a["acceptable"])

    def test_creation_n_contre_m(self):
        bs = [tx_banque(date(2026, 10, 5), 100, "CREDIT") for _ in range(2)]
        vs = [tx_valorix(date(2026, 10, 5), 50, "DEBIT") for _ in range(4)]
        r = creer_rapprochement_manuel([b.pk for b in bs], [v.pk for v in vs])
        self.assertEqual(r.statut_validation, StatutValidation.VALIDE)
        self.assertEqual(r.origine, "MANUEL")
        self.assertEqual(r.score_details["type_rapprochement"], "N-M")

    def test_ecart_hors_tolerance_demande_un_motif(self):
        b = tx_banque(date(2026, 10, 5), 1000, "CREDIT")
        v = tx_valorix(date(2026, 10, 5), 900, "DEBIT")
        with self.assertRaises(EcartHorsToleranceError):
            creer_rapprochement_manuel([b.pk], [v.pk])
        with self.assertRaises(EcartHorsToleranceError):
            creer_rapprochement_manuel([b.pk], [v.pk], forcer=True)  # motif manquant
        r = creer_rapprochement_manuel([b.pk], [v.pk], motif="Frais bancaires", forcer=True)
        self.assertEqual(r.resultat, "ECART_MONTANT")
        self.assertIn("Frais", r.commentaire)

    def test_transaction_deja_rapprochee_refusee(self):
        b = tx_banque(date(2026, 10, 5), 100, "CREDIT")
        v = tx_valorix(date(2026, 10, 5), 100, "DEBIT")
        creer_rapprochement_manuel([b.pk], [v.pk])
        with self.assertRaises(ValidationRapprochementError):
            creer_rapprochement_manuel([b.pk], [v.pk])

    def test_melange_debit_credit_refuse(self):
        b1 = tx_banque(date(2026, 10, 5), 100, "CREDIT")
        b2 = tx_banque(date(2026, 10, 5), 100, "DEBIT")
        v = tx_valorix(date(2026, 10, 5), 200, "DEBIT")
        with self.assertRaises(ValidationRapprochementError):
            creer_rapprochement_manuel([b1.pk, b2.pk], [v.pk])

    def test_manuel_sans_regle_tolerance_nulle(self):
        b = tx_banque(date(2026, 10, 5), 100, "CREDIT")
        v = tx_valorix(date(2026, 10, 5), 105, "DEBIT", types="TRANSFERT")
        self.assertFalse(apercu_manuel([b.pk], [v.pk])["acceptable"])


class DecisionGroupeeTests(TestCase):
    def setUp(self):
        creer_regle(avant=2, apres=2)
        for i, montant in enumerate((1000, 2000, 3000)):
            tx_banque(date(2026, 10, 5), montant, "CREDIT")
            tx_valorix(date(2026, 10, 5), montant, "DEBIT")
        generer_toutes_les_propositions()

    def test_rejeter_certaines_puis_valider_le_reste(self):
        a_rejeter = Rapprochement.objects.order_by("score_correspondance", "cree_le").first()
        bilan = decision_groupee(ids_rejetes=[a_rejeter.pk], motif_rejet="Non conforme")
        self.assertEqual(len(bilan["rejetes"]), 1)
        self.assertEqual(len(bilan["valides"]), 2)
        a_rejeter.refresh_from_db()
        self.assertEqual(a_rejeter.statut_validation, "REJETE")
        self.assertEqual(Rapprochement.objects.filter(statut_validation="PROPOSE").count(), 0)

    def test_conflits_laisses_en_attente(self):
        # Deux Valorix identiques pour une seule ligne banque : 2 propositions concurrentes.
        tx_banque(date(2026, 10, 9), 500, "CREDIT")
        tx_valorix(date(2026, 10, 9), 500, "DEBIT")
        tx_valorix(date(2026, 10, 9), 500, "DEBIT")
        generer_toutes_les_propositions()
        bilan = decision_groupee()
        self.assertEqual(len(bilan["conflits"]), 1)
        self.assertEqual(len(bilan["valides"]), 4)
        self.assertEqual(Rapprochement.objects.filter(statut_validation="PROPOSE").count(), 1)

    def test_nettoyage_des_propositions_bloquees(self):
        tx_banque(date(2026, 10, 9), 500, "CREDIT")
        tx_valorix(date(2026, 10, 9), 500, "DEBIT")
        tx_valorix(date(2026, 10, 9), 500, "DEBIT")
        generer_toutes_les_propositions()
        decision_groupee()
        ids = annuler_propositions_bloquees()
        self.assertEqual(len(ids), 1)
        self.assertEqual(Rapprochement.objects.filter(statut_validation="PROPOSE").count(), 0)


class TransactionsNonRapprocheesTests(TestCase):
    def setUp(self):
        creer_regle()
        self.b1 = tx_banque(date(2026, 10, 5), 1000, "CREDIT")
        self.b2 = tx_banque(date(2026, 10, 5), 777, "CREDIT")
        self.v1 = tx_valorix(date(2026, 10, 5), 1000, "DEBIT")
        generer_toutes_les_propositions()

    def test_liste_non_rapprochees_compte_les_propositions(self):
        qs = {t.pk: t for t in queryset_non_rapprochees("banque")}
        self.assertEqual(len(qs), 2)
        self.assertEqual(qs[self.b1.pk].propositions_en_attente, 1)
        self.assertEqual(qs[self.b2.pk].propositions_en_attente, 0)

    def test_rapprochee_validee_sort_de_la_liste(self):
        decision_groupee()
        self.assertEqual([t.pk for t in queryset_non_rapprochees("banque")], [self.b2.pk])

    def test_suppression_supprime_aussi_les_propositions(self):
        res = supprimer_transactions("banque", [self.b1.pk])
        self.assertEqual(res["supprimees"], 1)
        self.assertEqual(res["propositions_supprimees"], 1)
        self.assertEqual(Rapprochement.objects.count(), 0)

    def test_suppression_refusee_si_rapprochee(self):
        decision_groupee()
        res = supprimer_transactions("banque", [self.b1.pk, self.b2.pk])
        self.assertEqual(res["supprimees"], 1)
        self.assertEqual([r["id"] for r in res["refusees"]], [self.b1.pk])

    def test_simulation_n_ecrit_rien(self):
        supprimer_transactions("banque", [self.b2.pk], simuler=True)
        self.assertEqual(queryset_non_rapprochees("banque").count(), 2)
