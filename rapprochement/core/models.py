
import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from valorix.models import TypeTransactionCaisse

class sens_transactions(models.TextChoices):
    CREDIT="CREDIT","credit"
    DEBIT="DEBIT","debit"

class StatutValidation(models.TextChoices):
    PROPOSE = "PROPOSE", "Proposé"
    VALIDE = "VALIDE", "Validé"
    REJETE = "REJETE", "Rejeté"
    ANNULE = "ANNULE", "Annulé"


class ResultatMatching(models.TextChoices):
    CORRESPONDANCE = "CORRESPONDANCE", "Correspondance possible"
    ECART_MONTANT = "ECART_MONTANT", "Écart de montant"
    ANOMALIE = "ANOMALIE", "Anomalie"
    EXCLUDED = "EXCLUDED", "Exclu"


class RegleMatching(models.Model):
    """
    Règle de rapprochement : UNE règle par type de transaction Valorix.

    Elle est appliquée à la génération des propositions ; ses paramètres
    sont recopiés dans chaque proposition (``Rapprochement.regle_snapshot``)
    pour que la validation reste cohérente même si la règle change ensuite.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    type_transaction_valorix = models.CharField(
        max_length=30,
        choices=TypeTransactionCaisse.choices,
        unique=True,
    )

    # Sens attendu côté banque (le sens Valorix correspondant est l'inverse).
    sens_transaction_document_cible = models.CharField(
        max_length=40,
        choices=sens_transactions.choices,
        default=sens_transactions.CREDIT,
    )

    fenetre_jours_avant = models.PositiveSmallIntegerField(default=2)
    fenetre_jours_apres = models.PositiveSmallIntegerField(default=2)

    tolerance_fixe = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    # Exemple : 1.5 signifie 1,5 %
    tolerance_pourcentage = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=0,
        validators=[MinValueValidator(0)],
    )

    # Nombre maximal de transactions regroupées dans un rapprochement
    # automatique 1-N / N-1. 1 = uniquement des rapprochements 1-1.
    regroupement_max = models.PositiveSmallIntegerField(
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
    )

    actif = models.BooleanField(default=True)

    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Règle de rapprochement"
        verbose_name_plural = "Règles de rapprochement"
        ordering = ["type_transaction_valorix"]

    def __str__(self):
        return f"Règle {self.type_transaction_valorix}"


class OrigineRapprochement(models.TextChoices):
    AUTO = "AUTO", "Automatique"
    MANUEL = "MANUEL", "Manuel"


class Rapprochement(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    statut_validation = models.CharField(
        max_length=10,
        choices=StatutValidation.choices,
        default=StatutValidation.PROPOSE,
        db_index=True,
    )

    # Résultat calculé par le moteur, distinct de la validation humaine.
    resultat = models.CharField(
        max_length=20,
        choices=ResultatMatching.choices,
        blank=True,
    )

    score_correspondance = models.PositiveSmallIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    # Écart calculé à partir des montants des transactions liées.
    ecart_montant = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )

    # Explications du score : montant, date, référence, sens, etc.
    score_details = models.JSONField(default=dict, blank=True)


    commentaire = models.TextField(blank=True)

    origine = models.CharField(
        max_length=10,
        choices=OrigineRapprochement.choices,
        default=OrigineRapprochement.AUTO,
        db_index=True,
    )

    # Paramètres de la règle au moment de la génération (fenêtre, tolérance,
    # sens attendu...) : la validation s'appuie dessus.
    regle_snapshot = models.JSONField(default=dict, blank=True)

    cree_le = models.DateTimeField(auto_now_add=True, db_index=True)
    valide_le = models.DateTimeField(null=True, blank=True)


    class Meta:
        verbose_name = "Rapprochement"
        verbose_name_plural = "Rapprochements"
        ordering = ["-cree_le"]
        indexes = [
            models.Index(fields=["statut_validation", "resultat"]),
        ]

    def __str__(self):
        return f"{self.id} - {self.statut_validation}"


class TransactionBancaireRapprochee(models.Model):
    rapprochement = models.ForeignKey(
        Rapprochement,
        on_delete=models.CASCADE,
        related_name="transactions_bancaires",
    )

    transaction = models.ForeignKey(
        "releverBancaire.TransactionBancaire",
        on_delete=models.PROTECT,
        related_name="liens_rapprochement",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rapprochement", "transaction"],
                name="uniq_banque_par_rapprochement",
            ),
        ]

    def __str__(self):
        return f"{self.transaction_id} -> {self.rapprochement_id}"


class TransactionValorixRapprochee(models.Model):
    rapprochement = models.ForeignKey(
        Rapprochement,
        on_delete=models.CASCADE,
        related_name="transactions_valorix",
    )

    transaction = models.ForeignKey(
        "valorix.TransactionValorix",
        on_delete=models.PROTECT,
        related_name="liens_rapprochement",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rapprochement", "transaction"],
                name="uniq_valorix_par_rapprochement",
            ),
        ]

    def __str__(self):
        return f"{self.transaction_id} -> {self.rapprochement_id}"
