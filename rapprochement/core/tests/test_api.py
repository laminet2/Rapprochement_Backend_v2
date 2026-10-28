from datetime import date

from rest_framework.test import APITestCase

from core.models import RegleMatching
from valorix.models import TypeTransactionCaisse

from .factories import creer_compte, creer_regle, tx_banque, tx_valorix


class RegleApiTests(APITestCase):
    def test_regles_par_defaut_une_par_type(self):
        r = self.client.post("/rapprochements/regles/par-defaut/")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(RegleMatching.objects.count(), len(TypeTransactionCaisse.choices))
        retrait = RegleMatching.objects.get(type_transaction_valorix="RETRAIT_ESPECES")
        self.assertEqual(retrait.sens_transaction_document_cible, "DEBIT")
        # Idempotent
        self.assertEqual(self.client.post("/rapprochements/regles/par-defaut/").status_code, 200)

    def test_une_seule_regle_par_type(self):
        corps = {"type_transaction_valorix": "DEPOT_CHEQUE"}
        self.assertEqual(self.client.post("/rapprochements/regles/", corps, format="json").status_code, 201)
        self.assertEqual(self.client.post("/rapprochements/regles/", corps, format="json").status_code, 400)

    def test_modifier_une_regle(self):
        regle = creer_regle()
        r = self.client.patch(
            f"/rapprochements/regles/{regle.id}/",
            {"tolerance_fixe": "25.00", "regroupement_max": 5}, format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["regroupement_max"], 5)


class ParcoursCompletTests(APITestCase):
    """Import -> génération -> revue (rejet + validation groupée) -> manuel -> suppression."""

    def setUp(self):
        creer_regle(avant=2, apres=2)
        self.compte = creer_compte()
        # 3 paires 1-1, 1 dépôt 1-N, 1 ligne banque et 1 ligne Valorix orphelines
        for m in (1000, 2000, 3000):
            tx_banque(date(2026, 10, 5), m, "CREDIT", libelle=f"REMISE {m}")
            tx_valorix(date(2026, 10, 5), m, "DEBIT")
        self.b_groupe = tx_banque(date(2026, 10, 8), 900, "CREDIT", libelle="REMISE CHEQUES")
        self.v_g = [tx_valorix(date(2026, 10, 8), m, "DEBIT") for m in (400, 500)]
        self.b_orpheline = tx_banque(date(2026, 10, 20), 12345, "CREDIT")
        self.v_orpheline = tx_valorix(date(2026, 10, 20), 54321, "DEBIT")

    def generer(self):
        r = self.client.post("/rapprochements/generer/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def test_generation_et_liste(self):
        bilan = self.generer()
        self.assertEqual(bilan["propositions_1_1"], 3)
        self.assertEqual(bilan["propositions_regroupees"], 1)

        liste = self.client.get("/rapprochements/?ordering=-score")
        self.assertEqual(liste.data["count"], 4)
        item = liste.data["results"][0]
        for cle in ("score_details", "type_rapprochement", "total_banque",
                    "total_valorix", "en_conflit", "transactions_bancaires",
                    "transactions_valorix", "regle_snapshot", "origine"):
            self.assertIn(cle, item)
        self.assertIn("explication", item["score_details"]["montant"])

        groupes = self.client.get("/rapprochements/?type_rapprochement=1-N")
        self.assertEqual(groupes.data["count"], 1)
        self.assertEqual(groupes.data["results"][0]["nombre_valorix"], 2)
        self.assertEqual(groupes.data["results"][0]["total_banque"], "900.00")

        un_un = self.client.get("/rapprochements/?type_rapprochement=1-1&page_size=2")
        self.assertEqual(un_un.data["count"], 3)
        self.assertEqual(len(un_un.data["results"]), 2)

    def test_detail(self):
        self.generer()
        rid = self.client.get("/rapprochements/").data["results"][0]["id"]
        d = self.client.get(f"/rapprochements/{rid}/")
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d.data["id"], rid)

    def test_revue_rejeter_puis_valider_le_reste(self):
        self.generer()
        ids = [r["id"] for r in self.client.get("/rapprochements/").data["results"]]
        rejet = ids[0]

        r = self.client.post(
            "/rapprochements/decision-groupee/",
            {"ids_rejetes": [rejet], "motif_rejet": "Montant suspect"}, format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["resume"]["rejetes"], 1)
        self.assertEqual(r.data["resume"]["valides"], 3)
        self.assertEqual(r.data["conflits"], [])

        valides = self.client.get("/rapprochements/?statut_validation=VALIDE")
        self.assertEqual(valides.data["count"], 3)
        rejete = self.client.get(f"/rapprochements/{rejet}/").data
        self.assertEqual(rejete["statut_validation"], "REJETE")
        self.assertIn("Montant suspect", rejete["commentaire"])

        # Les lignes de la proposition rejetée restent à rapprocher
        non = self.client.get("/rapprochements/non-rapprochees/?source=banque")
        self.assertGreaterEqual(non.data["count"], 2)

    def test_actions_unitaires_via_api(self):
        self.generer()
        ids = [r["id"] for r in self.client.get("/rapprochements/").data["results"]]

        ok = self.client.post(f"/rapprochements/{ids[0]}/valider/")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(self.client.post(f"/rapprochements/{ids[0]}/valider/").status_code, 400)

        sans_motif = self.client.post(f"/rapprochements/{ids[1]}/rejeter/", {}, format="json")
        self.assertEqual(sans_motif.status_code, 400)
        rej = self.client.post(f"/rapprochements/{ids[1]}/rejeter/", {"motif": "non"}, format="json")
        self.assertEqual(rej.status_code, 200, rej.content)

        ann = self.client.post(f"/rapprochements/{ids[0]}/annuler/", {"motif": "erreur"}, format="json")
        self.assertEqual(ann.status_code, 200, ann.content)
        self.assertEqual(ann.data["statut_validation"], "ANNULE")

    def test_valider_tout_conserve(self):
        self.generer()
        r = self.client.post("/rapprochements/valider-tout/")
        self.assertEqual(r.data["resume"]["valides"], 4)

    def test_rapprochement_manuel_apercu_puis_creation(self):
        b, v = self.b_orpheline, self.v_orpheline
        corps = {"transactions_bancaires": [b.pk], "transactions_valorix": [str(v.pk)]}

        apercu = self.client.post("/rapprochements/manuel/apercu/", corps, format="json")
        self.assertEqual(apercu.status_code, 200, apercu.content)
        self.assertFalse(apercu.data["acceptable"])
        self.assertEqual(apercu.data["ecart"], "41976.00")

        refus = self.client.post("/rapprochements/manuel/", corps, format="json")
        self.assertEqual(refus.status_code, 422)
        self.assertTrue(refus.data["forcer_requis"])

        ok = self.client.post(
            "/rapprochements/manuel/", {**corps, "forcer": True, "motif": "Avoir client"}, format="json"
        )
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(ok.data["origine"], "MANUEL")
        self.assertEqual(ok.data["statut_validation"], "VALIDE")
        self.assertEqual(ok.data["resultat"], "ECART_MONTANT")

    def test_non_rapprochees_et_suppression(self):
        self.generer()
        self.client.post("/rapprochements/decision-groupee/", {}, format="json")

        non = self.client.get("/rapprochements/non-rapprochees/?source=banque")
        self.assertEqual(non.status_code, 200)
        self.assertEqual(non.data["count"], 1)  # seule l'orpheline
        self.assertEqual(non.data["results"][0]["id"], self.b_orpheline.pk)

        nv = self.client.get("/rapprochements/non-rapprochees/?source=valorix")
        self.assertEqual(nv.data["count"], 1)

        refus = self.client.post(
            "/rapprochements/non-rapprochees/supprimer/",
            {"source": "banque", "ids": [self.b_groupe.pk, self.b_orpheline.pk]}, format="json",
        )
        self.assertEqual(refus.data["supprimees"], 1)
        self.assertEqual(refus.data["refusees"][0]["id"], self.b_groupe.pk)

        sup_v = self.client.post(
            "/rapprochements/non-rapprochees/supprimer/",
            {"source": "valorix", "ids": [str(self.v_orpheline.pk)]}, format="json",
        )
        self.assertEqual(sup_v.data["supprimees"], 1)

    def test_suppression_via_les_viewsets(self):
        r = self.client.delete(f"/rbc/transactions/{self.b_orpheline.pk}/")
        self.assertEqual(r.status_code, 204)
        r = self.client.delete(f"/valorix/transactions/{self.v_orpheline.pk}/")
        self.assertEqual(r.status_code, 204)

    def test_suppression_refusee_si_rapprochee(self):
        self.generer()
        self.client.post("/rapprochements/decision-groupee/", {}, format="json")
        r = self.client.delete(f"/rbc/transactions/{self.b_groupe.pk}/")
        self.assertEqual(r.status_code, 409)

    def test_filtre_rapprochee_sur_les_transactions(self):
        self.generer()
        self.client.post("/rapprochements/decision-groupee/", {}, format="json")
        non = self.client.get("/rbc/transactions/?rapprochee=false")
        oui = self.client.get("/rbc/transactions/?rapprochee=true")
        self.assertEqual(non.data["count"], 1)
        self.assertEqual(oui.data["count"], 4)
        self.assertEqual(self.client.get("/valorix/transactions/?rapprochee=false").data["count"], 1)

    def test_statistiques(self):
        self.generer()
        self.client.post("/rapprochements/decision-groupee/", {}, format="json")
        s = self.client.get("/rapprochements/statistiques/")
        self.assertEqual(s.status_code, 200)
        self.assertEqual(s.data["rapprochements_valides"], 4)
        self.assertEqual(s.data["transactions_banque"]["non_rapprochees"], 1)
        self.assertEqual(s.data["transactions_banque"]["taux_rapprochement"], 80.0)

    def test_conflit_visible_dans_la_liste(self):
        tx_banque(date(2026, 10, 12), 500, "CREDIT")
        tx_valorix(date(2026, 10, 12), 500, "DEBIT")
        tx_valorix(date(2026, 10, 12), 500, "DEBIT")
        self.generer()
        res = self.client.get("/rapprochements/?type_rapprochement=1-1").data["results"]
        conflits = [r for r in res if r["en_conflit"]]
        self.assertEqual(len(conflits), 2)

    def test_proposition_bloquee_apres_rapprochement_manuel(self):
        self.generer()
        prop = [r for r in self.client.get("/rapprochements/").data["results"]
                if r["type_rapprochement"] == "1-1"][0]
        b_id = prop["transactions_bancaires"][0]["transaction_id"]
        v_id = prop["transactions_valorix"][0]["transaction_id"]
        # Rapprochement manuel de la même ligne banque avec une autre ligne Valorix
        autre = tx_valorix(date(2026, 10, 5), 1, "DEBIT", types="TRANSFERT")
        self.client.post(
            "/rapprochements/manuel/",
            {"transactions_bancaires": [b_id], "transactions_valorix": [str(autre.pk)],
             "forcer": True, "motif": "test"}, format="json",
        )
        detail = self.client.get(f"/rapprochements/{prop['id']}/").data
        self.assertTrue(detail["bloquee"])
        res = self.client.post(f"/rapprochements/{prop['id']}/valider/")
        self.assertEqual(res.status_code, 409)
        n = self.client.post("/rapprochements/nettoyer-bloquees/")
        self.assertEqual(n.data["nombre"], 1)
