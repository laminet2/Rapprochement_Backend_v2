from types import SimpleNamespace

from django.test import SimpleTestCase

from core.services.scoring import calculer_score, classer_candidats


class TestScoring(SimpleTestCase):
    def setUp(self):
        self.regle = SimpleNamespace(fenetre_jours_avant=2, fenetre_jours_apres=3)

    def base(self, **extra):
        return {
            "ecart_montant": 0, "tolerance": 1000, "ecart_jours": 0,
            "reference_correspond": True, "compte_correspond": True,
            "nom_correspond": True, "sens_valorix": "DEBIT", "sens_banque": "CREDIT",
            **extra,
        }

    def test_score_maximal(self):
        self.assertEqual(calculer_score(self.base(), self.regle)["score"], 100)

    def test_ancien_format_sens_verifie(self):
        c = {"ecart_montant": 0, "tolerance": 1000, "ecart_jours": 0,
             "reference_correspond": True, "sens_verifie": True,
             "compte_correspond": True, "nom_correspond": True}
        self.assertEqual(calculer_score(c, self.regle)["score"], 100)

    def test_sans_reference_perd_25_points(self):
        r = calculer_score(self.base(reference_correspond=False), self.regle)
        self.assertEqual(r["score"], 75)

    def test_montant_proportionnel_a_la_tolerance(self):
        r = calculer_score(self.base(ecart_montant=500), self.regle)
        self.assertEqual(r["details"]["montant"]["score"], 15)
        self.assertEqual(r["score"], 85)

    def test_montant_hors_tolerance(self):
        r = calculer_score(self.base(ecart_montant=2000), self.regle)
        self.assertEqual(r["details"]["montant"]["score"], 0)

    def test_date_avant_et_apres_utilisent_leur_fenetre(self):
        avant = calculer_score(self.base(ecart_jours=-1), self.regle)
        apres = calculer_score(self.base(ecart_jours=1), self.regle)
        self.assertEqual(avant["details"]["date"]["score"], 8)   # (2-1)/2*15
        self.assertEqual(apres["details"]["date"]["score"], 10)  # (3-1)/3*15

    def test_criteres_indisponibles_sont_normalises(self):
        """Document « autre » : ni référence ni nom -> un candidat parfait fait 100."""
        c = self.base(
            reference_correspond=False, nom_correspond=False,
            reference_disponible=False, nom_disponible=False,
        )
        r = calculer_score(c, self.regle)
        self.assertEqual(r["score"], 100)
        self.assertFalse(r["details"]["reference"]["disponible"])
        self.assertEqual(r["details"]["total"]["disponible"], 65)

    def test_critere_disponible_mais_absent_penalise(self):
        c = self.base(compte_correspond=False)  # compte disponible par défaut
        self.assertEqual(calculer_score(c, self.regle)["score"], 85)

    def test_groupe_fraction_de_lignes(self):
        c = self.base(compte_correspond=0.5)
        r = calculer_score(c, self.regle)
        self.assertEqual(r["details"]["compte"]["score"], 8)  # 7.5 arrondi
        self.assertIn("50 %", r["details"]["compte"]["explication"])

    def test_explications_en_francais(self):
        r = calculer_score(self.base(), self.regle)
        self.assertEqual(r["details"]["montant"]["explication"], "Montants identiques.")
        self.assertEqual(r["details"]["date"]["explication"], "Même jour.")

    def test_classement(self):
        a = self.base(ecart_montant=500)
        b = self.base()
        res = classer_candidats([a, b], self.regle)
        self.assertEqual([r["score_correspondance"] for r in res], [100, 85])
