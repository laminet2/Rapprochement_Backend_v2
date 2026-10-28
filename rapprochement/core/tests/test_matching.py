from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from core.models import Rapprochement, StatutValidation
from core.services.matching import rechercher_propositions, sous_ensembles
from core.services.proposition import generer_pour_regle, generer_toutes_les_propositions
from core.services.regles import ParametresRegle

from .factories import creer_regle, tx_banque, tx_valorix


class TestSousEnsembles(SimpleTestCase):
    def test_trouve_une_combinaison_exacte(self):
        sols = sous_ensembles([100, 200, 300, 450], cible=500, tolerance=0, taille_max=5)
        sommes = [sum([100, 200, 300, 450][i] for i in s) for s in sols]
        self.assertTrue(sols)
        self.assertTrue(all(s == 500 for s in sommes))

    def test_respecte_la_taille_max(self):
        sols = sous_ensembles([100, 100, 100, 100, 100], cible=500, tolerance=0, taille_max=4)
        self.assertEqual(sols, [])

    def test_tolerance(self):
        sols = sous_ensembles([100, 200], cible=305, tolerance=5, taille_max=3)
        self.assertEqual(len(sols), 1)

    def test_aucune_solution(self):
        self.assertEqual(sous_ensembles([100, 200], cible=1000, tolerance=0, taille_max=5), [])

    def test_budget_borne_la_recherche(self):
        valeurs = [7 + i for i in range(60)]
        # Ne doit pas exploser même sans solution proche.
        sols = sous_ensembles(valeurs, cible=1, tolerance=0, taille_max=10, budget=5000)
        self.assertEqual(sols, [])


class TestMatching1a1(TestCase):
    def setUp(self):
        self.regle = creer_regle(avant=2, apres=2)
        self.params = ParametresRegle.depuis_objet(self.regle)

    def test_paire_simple(self):
        tx_banque(date(2026, 10, 5), 1000, "CREDIT", libelle="REMISE CHQ 1234567")
        tx_valorix(date(2026, 10, 5), 1000, "DEBIT", refs="1234567")
        candidats, stats = rechercher_propositions(self.params)
        self.assertEqual(len(candidats), 1)
        self.assertEqual(candidats[0]["type_rapprochement"], "1-1")
        self.assertEqual(stats["valorix_examinees"], 1)

    def test_hors_fenetre_de_dates(self):
        tx_banque(date(2026, 10, 10), 1000, "CREDIT")
        tx_valorix(date(2026, 10, 5), 1000, "DEBIT")
        self.assertEqual(rechercher_propositions(self.params)[0], [])

    def test_mauvais_sens_bancaire(self):
        tx_banque(date(2026, 10, 5), 1000, "DEBIT")
        tx_valorix(date(2026, 10, 5), 1000, "DEBIT")
        self.assertEqual(rechercher_propositions(self.params)[0], [])

    def test_autre_type_ignore(self):
        tx_banque(date(2026, 10, 5), 1000, "CREDIT")
        tx_valorix(date(2026, 10, 5), 1000, "DEBIT", types="DEPOT_ESPECES")
        self.assertEqual(rechercher_propositions(self.params)[0], [])

    def test_montant_hors_tolerance(self):
        tx_banque(date(2026, 10, 5), 1001, "CREDIT")
        tx_valorix(date(2026, 10, 5), 1000, "DEBIT")
        self.assertEqual(rechercher_propositions(self.params)[0], [])


class TestRegroupements(TestCase):
    def setUp(self):
        self.regle = creer_regle(avant=3, apres=3)
        self.params = ParametresRegle.depuis_objet(self.regle)

    def test_un_depot_banque_pour_plusieurs_cheques(self):
        b = tx_banque(date(2026, 10, 5), 600, "CREDIT", libelle="REMISE CHEQUES")
        v1 = tx_valorix(date(2026, 10, 4), 100, "DEBIT")
        v2 = tx_valorix(date(2026, 10, 4), 200, "DEBIT")
        v3 = tx_valorix(date(2026, 10, 5), 300, "DEBIT")
        tx_valorix(date(2026, 10, 5), 77, "DEBIT")  # intrus

        candidats, _ = rechercher_propositions(self.params)
        self.assertEqual(len(candidats), 1)
        c = candidats[0]
        self.assertEqual(c["type_rapprochement"], "1-N")
        self.assertEqual({l.id for l in c["lignes_valorix"]}, {v1.pk, v2.pk, v3.pk})
        self.assertEqual(c["lignes_banque"][0].id, b.pk)
        self.assertEqual(c["ecart_montant"], Decimal("0.00"))

    def test_plusieurs_lignes_banque_pour_un_valorix(self):
        v = tx_valorix(date(2026, 10, 5), 500, "DEBIT")
        b1 = tx_banque(date(2026, 10, 5), 200, "CREDIT")
        b2 = tx_banque(date(2026, 10, 6), 300, "CREDIT")
        candidats, _ = rechercher_propositions(self.params)
        self.assertEqual(len(candidats), 1)
        self.assertEqual(candidats[0]["type_rapprochement"], "N-1")
        self.assertEqual({l.id for l in candidats[0]["lignes_banque"]}, {b1.pk, b2.pk})
        self.assertEqual(candidats[0]["lignes_valorix"][0].id, v.pk)

    def test_regroupement_desactive(self):
        self.regle.regroupement_max = 1
        self.regle.save()
        params = ParametresRegle.depuis_objet(self.regle)
        tx_banque(date(2026, 10, 5), 300, "CREDIT")
        tx_valorix(date(2026, 10, 5), 100, "DEBIT")
        tx_valorix(date(2026, 10, 5), 200, "DEBIT")
        self.assertEqual(rechercher_propositions(params)[0], [])

    def test_pas_de_regroupement_si_un_1_1_existe(self):
        tx_banque(date(2026, 10, 5), 100, "CREDIT")
        tx_valorix(date(2026, 10, 5), 100, "DEBIT")
        tx_valorix(date(2026, 10, 5), 40, "DEBIT")
        tx_valorix(date(2026, 10, 5), 60, "DEBIT")
        candidats, _ = rechercher_propositions(self.params)
        self.assertEqual([c["type_rapprochement"] for c in candidats], ["1-1"])

    def test_ligne_valorix_non_reutilisee_entre_regroupements(self):
        tx_banque(date(2026, 10, 5), 300, "CREDIT")
        tx_banque(date(2026, 10, 5), 300, "CREDIT")
        for m in (100, 200):
            tx_valorix(date(2026, 10, 5), m, "DEBIT")
        candidats, _ = rechercher_propositions(self.params)
        self.assertEqual(len(candidats), 1)

    def test_tolerance_sur_la_somme(self):
        regle = self.regle
        regle.tolerance_fixe = Decimal("5")
        regle.save()
        params = ParametresRegle.depuis_objet(regle)
        tx_banque(date(2026, 10, 5), 303, "CREDIT")
        tx_valorix(date(2026, 10, 5), 100, "DEBIT")
        tx_valorix(date(2026, 10, 5), 200, "DEBIT")
        candidats, _ = rechercher_propositions(params)
        self.assertEqual(candidats[0]["ecart_montant"], Decimal("3.00"))


class TestGeneration(TestCase):
    def setUp(self):
        creer_regle(avant=2, apres=2)
        tx_banque(date(2026, 10, 5), 1000, "CREDIT")
        tx_valorix(date(2026, 10, 5), 1000, "DEBIT")

    def test_creation_puis_idempotence(self):
        bilan = generer_toutes_les_propositions()
        self.assertEqual(bilan["propositions_creees"], 1)
        self.assertEqual(bilan["propositions_1_1"], 1)

        bilan2 = generer_toutes_les_propositions()
        self.assertEqual(bilan2["propositions_creees"], 0)
        self.assertEqual(bilan2["propositions_actualisees"], 1)
        self.assertEqual(Rapprochement.objects.count(), 1)

    def test_snapshot_enregistre(self):
        generer_toutes_les_propositions()
        r = Rapprochement.objects.get()
        self.assertEqual(r.regle_snapshot["type_transaction_valorix"], "DEPOT_CHEQUE")
        self.assertEqual(r.regle_snapshot["fenetre_jours_avant"], 2)

    def test_rejet_non_repropose(self):
        generer_toutes_les_propositions()
        Rapprochement.objects.update(statut_validation=StatutValidation.REJETE)
        bilan = generer_toutes_les_propositions()
        self.assertEqual(bilan["propositions_creees"], 0)
        self.assertEqual(bilan["propositions_deja_traitees"], 1)

    def test_type_sans_regle_signale(self):
        tx_valorix(date(2026, 10, 5), 10, "DEBIT", types="TRANSFERT")
        bilan = generer_toutes_les_propositions()
        self.assertEqual(bilan["types_sans_regle"], ["TRANSFERT"])

    def test_perimetre_par_lot(self):
        from importation.models import LotImport
        lot = LotImport.objects.create(source="VALORIX")
        tx_valorix(date(2026, 10, 7), 500, "DEBIT", lot=lot)
        tx_banque(date(2026, 10, 7), 500, "CREDIT")
        bilan = generer_toutes_les_propositions(lot_valorix_ids=[lot.id])
        self.assertEqual(bilan["valorix_examinees"], 1)
        self.assertEqual(bilan["propositions_creees"], 1)

    def test_document_autre_score_normalise(self):
        creer_regle(types="DOCUMENT_AUTRE", sens="CREDIT")
        tx_banque(date(2026, 11, 2), 750, "CREDIT", libelle="VIR CLIENT 0012345678")
        tx_valorix(date(2026, 11, 2), 750, "DEBIT", types="DOCUMENT_AUTRE", compte="0012345678")
        generer_toutes_les_propositions(types=["DOCUMENT_AUTRE"])
        r = Rapprochement.objects.get(score_details__nombre_valorix=1, ecart_montant=0,
                                      transactions_valorix__transaction__types="DOCUMENT_AUTRE")
        self.assertEqual(r.score_correspondance, 100)
        self.assertFalse(r.score_details["reference"]["disponible"])
