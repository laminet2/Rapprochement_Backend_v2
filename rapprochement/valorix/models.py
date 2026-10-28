from django.db import models
import uuid

class TypeTransactionCaisse(models.TextChoices):
    DEPOT_CHEQUE = "DEPOT_CHEQUE", "Dépôt chèques"
    RETRAIT_CHEQUE = "RETRAIT_CHEQUE", "Retrait chèques"
    DEPOT_ESPECES = "DEPOT_ESPECES", "Dépôt espèces"
    RETRAIT_ESPECES = "RETRAIT_ESPECES", "Retrait espèces"
    TRANSFERT = "TRANSFERT", "Transferts"
    DEPOT_VIREMENTS = "DEPOT_VIREMENTS", "Dépôt/Virements"
    RETRAIT_VIREMENTS = "RETRAIT_VIREMENTS", "Retrait/Virements"
    AUTRE = "AUTRE", "Autre"
    # Document « autre » importé par l'utilisateur (compte, date, montant net).
    DOCUMENT_AUTRE = "DOCUMENT_AUTRE", "Document autre"


# Create your models here.
class TransactionValorix(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4)
    numero_transaction=models.CharField()
    date_transaction=models.DateTimeField(auto_now=False, auto_now_add=False, db_index=True)
    numero_compte = models.CharField(max_length = 150)
    nomComplet=models.CharField(max_length=255)

    debit = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )
    credit = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )
    references = models.CharField(max_length = 255)
    
    types=models.CharField(
        max_length = 100,
        choices=TypeTransactionCaisse.choices
    )

    #references_rapprochement=models.CharField(max_length=255,null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    lot = models.ForeignKey(
        "importation.LotImport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions_valorix",
    )

    class Meta:
        ordering = ["-date_transaction"]

    def __str__(self):
        return f"{self.numero_transaction} - {self.nomComplet}"
