
from decimal import Decimal

from django.test import SimpleTestCase

from core.services.tolerance import (
    calculer_tolerance,
    evaluer_ecart,
)


class TestTolerance(SimpleTestCase):

    def test_tolerance_fixe_plus_grande(self):
        tolerance = calculer_tolerance(
            Decimal("10000"),
            Decimal("500"),
            Decimal("1"),
        )
        self.assertEqual(tolerance, Decimal("500"))

    def test_tolerance_pourcentage_plus_grande(self):
        tolerance = calculer_tolerance(
            Decimal("100000"),
            Decimal("500"),
            Decimal("1"),
        )
        self.assertEqual(tolerance, Decimal("1000.00"))

    def test_ecart_dans_la_tolerance(self):
        resultat = evaluer_ecart(
            Decimal("100800"),
            Decimal("100000"),
            Decimal("500"),
            Decimal("1"),
        )
        self.assertTrue(resultat["acceptable"])

    def test_ecart_exactement_a_la_limite(self):
        resultat = evaluer_ecart(
            Decimal("101000"),
            Decimal("100000"),
            Decimal("500"),
            Decimal("1"),
        )
        self.assertTrue(resultat["acceptable"])

    def test_ecart_superieur_a_la_limite(self):
        resultat = evaluer_ecart(
            Decimal("101001"),
            Decimal("100000"),
            Decimal("500"),
            Decimal("1"),
        )
        self.assertFalse(resultat["acceptable"])

    def test_tolerance_negative_refusee(self):
        with self.assertRaises(ValueError):
            calculer_tolerance(
                Decimal("10000"),
                Decimal("-1"),
                Decimal("1"),
            )
