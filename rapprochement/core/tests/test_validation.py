from datetime import date

from django.test import TestCase

from core.models import Rapprochement, StatutValidation
from core.services.actions import (
    ActionRapprochementError,
    annuler_rapprochement,
    rejeter_rapprochement,
)
from core.services.proposition import generer_toutes_les_propositions
from core.services.validation import (
    ConflitRapprochementError,
    ValidationRapprochementError,
    valider_rapprochement,
)

from .factories import creer_regle, tx_banque, tx_valorix


class ValidationTests(TestCase):
    def setUp(self):
        self.regle = creer_regle(avant=2, apres=2)
        self.b = tx_banque(date(2026, 10, 5), 1000, "CREDIT")
        self.v = tx_valorix(date(2026, 10, 5), 1000, "DEBIT")
        generer_toutes_les_propositions()
        self.r = Rapprochement.objects.get()

    def test_validation_ok(self):
        r = valider_rapprochement(self.r.pk)
        self.assertEqual(r.statut_validation, StatutValidation.VALIDE)
        self.assertIsNotNone(r.valide_le)

    def test_validation_sans_utilisateur_authentifie(self):
        """Les permissions sont désactivées volontairement pendant le développement."""
        self.assertEqual(
            valider_rapprochement(self.r.pk, None).statut_validation, "VALIDE"
        )

    def test_deja_valide_refuse(self):
        valider_rapprochement(self.r.pk)
        with self.assertRaises(ValidationRapprochementError):
            valider_rapprochement(self.r.pk)

    def test_revalidation_detecte_montant_modifie(self):
        self.b.credit = 1500
        self.b.save()
        with self.assertRaises(ValidationRapprochementError) as ctx:
            valider_rapprochement(self.r.pk)
        self.assertIn("tolérance", str(ctx.exception))

    def test_revalidation_detecte_date_modifiee(self):
        self.b.date_transaction = date(2026, 10, 20)
        self.b.save()
        with self.assertRaises(ValidationRapprochementError):
            valider_rapprochement(self.r.pk)

    def test_snapshot_fait_foi_meme_si_la_regle_change(self):
        self.regle.tolerance_fixe = 0
        self.regle.fenetre_jours_avant = 0
        self.regle.fenetre_jours_apres = 0
        self.regle.save()
        self.b.date_transaction = date(2026, 10, 6)  # dans la fenêtre du snapshot (2)
        self.b.save()
        valider_rapprochement(self.r.pk)  # ne doit pas échouer

    def test_proposition_ancienne_sans_snapshot_utilise_la_regle_du_type(self):
        Rapprochement.objects.filter(pk=self.r.pk).update(regle_snapshot={})
        r = valider_rapprochement(self.r.pk)
        self.assertEqual(r.regle_snapshot["type_transaction_valorix"], "DEPOT_CHEQUE")

    def test_sans_regle_ni_snapshot_refuse(self):
        Rapprochement.objects.filter(pk=self.r.pk).update(regle_snapshot={})
        self.regle.delete()
        with self.assertRaises(ValidationRapprochementError):
            valider_rapprochement(self.r.pk)

    def test_conflit_avec_un_rapprochement_deja_valide(self):
        v2 = tx_valorix(date(2026, 10, 5), 1000, "DEBIT")
        generer_toutes_les_propositions()
        autre = Rapprochement.objects.exclude(pk=self.r.pk).get()
        valider_rapprochement(self.r.pk)
        with self.assertRaises(ConflitRapprochementError):
            valider_rapprochement(autre.pk)

    def test_validation_groupe_1_N(self):
        regle = self.regle
        b = tx_banque(date(2026, 10, 9), 300, "CREDIT")
        tx_valorix(date(2026, 10, 9), 100, "DEBIT")
        tx_valorix(date(2026, 10, 9), 200, "DEBIT")
        generer_toutes_les_propositions()
        groupe = Rapprochement.objects.get(score_details__type_rapprochement="1-N")
        valider_rapprochement(groupe.pk)
        groupe.refresh_from_db()
        self.assertEqual(groupe.statut_validation, "VALIDE")
        self.assertEqual(groupe.transactions_valorix.count(), 2)


class ActionTests(TestCase):
    def setUp(self):
        creer_regle()
        tx_banque(date(2026, 10, 5), 1000, "CREDIT")
        tx_valorix(date(2026, 10, 5), 1000, "DEBIT")
        generer_toutes_les_propositions()
        self.r = Rapprochement.objects.get()

    def test_rejet_sans_utilisateur_authentifie(self):
        r = rejeter_rapprochement(self.r.pk, None, "doublon")
        self.assertEqual(r.statut_validation, "REJETE")
        self.assertIn("anonyme", r.commentaire)

    def test_motif_obligatoire(self):
        with self.assertRaises(ActionRapprochementError):
            rejeter_rapprochement(self.r.pk, None, "  ")

    def test_rejeter_une_validation_est_interdit(self):
        valider_rapprochement(self.r.pk)
        with self.assertRaises(ActionRapprochementError):
            rejeter_rapprochement(self.r.pk, None, "erreur")

    def test_annuler_un_rapprochement_valide_libere_les_transactions(self):
        valider_rapprochement(self.r.pk)
        r = annuler_rapprochement(self.r.pk, None, "saisie erronée")
        self.assertEqual(r.statut_validation, "ANNULE")
        self.assertIsNone(r.valide_le)
        from core.services.transactions import queryset_non_rapprochees
        self.assertEqual(queryset_non_rapprochees("banque").count(), 1)

    def test_annuler_un_rejet_est_interdit(self):
        rejeter_rapprochement(self.r.pk, None, "x")
        with self.assertRaises(ActionRapprochementError):
            annuler_rapprochement(self.r.pk, None, "y")
