import uuid

from django.db import models



class TypeTransactionBancaire(models.TextChoices):
    RETRAIT_CHEQUE = "RETRAIT_CHEQUE", "Retrait par chèque"
    COMPENSATION_CHEQUE = "COMPENSATION_CHEQUE", "Compensation de chèque"
    DEPOT_CHEQUE = "DEPOT_CHEQUE", "Dépôt de chèque"

    DEPOT_BANKCOLLECT = "DEPOT_BANKCOLLECT", "Dépôt BankCollect"
    VERSEMENT_ESPECES = "VERSEMENT_ESPECES", "Versement d'espèces"

    TRANSFERT_RECU = "TRANSFERT_RECU", "Transfert reçu"
    TRANSFERT_MOBILE = "TRANSFERT_MOBILE", "Transfert mobile"
    TRANSFERT_INTERNE = "TRANSFERT_INTERNE", "Transfert interne"
    TRANSFERT_EMIS = "TRANSFERT_EMIS", "Transfert émis"

    PAIEMENT_DIVIDENDES = "PAIEMENT_DIVIDENDES", "Paiement de dividendes"
    SOUSCRIPTION_TITRES = "SOUSCRIPTION_TITRES", "Souscription / Achat de titres"

    FRAIS_BANCAIRES = "FRAIS_BANCAIRES", "Frais bancaires"
    AUTRE = "AUTRE", "Autre"


class Banque(models.Model):
   
    nom = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.nom


class CompteBancaire(models.Model):
    
    banque = models.ForeignKey(
        Banque,
        on_delete=models.PROTECT,
        related_name="comptes",
    )
    numero_compte = models.CharField(max_length=50)
    devise = models.CharField(max_length=3, default="XOF")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["banque", "numero_compte"],
                name="unique_compte_par_banque",
            )
        ]

    def __str__(self):
        return f"{self.banque} - {self.numero_compte}"


class TransactionBancaire(models.Model):

    id =  models.BigAutoField(primary_key=True)

    compte = models.ForeignKey(
        CompteBancaire,
        on_delete=models.PROTECT,
        related_name="transactions",
    )

    date_transaction = models.DateField(db_index=True)
    date_valeur = models.DateField(null=True, blank=True)

    reference_rapprochement = models.CharField(
        max_length=100,
        null=True,
        blank=True,
    )

    libelle_brut = models.TextField()

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

    solde_apres_operation = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )

    type_transaction = models.CharField(
            max_length=30,
            choices=TypeTransactionBancaire.choices,
            default=TypeTransactionBancaire.AUTRE,
        )
    created_at = models.DateTimeField(auto_now_add=True)

    lot = models.ForeignKey(
        "importation.LotImport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions_bancaires",
    )

    class Meta:
        ordering = ["-date_transaction", "-id"]

    def __str__(self):
        return f"{self.date_transaction} - {self.libelle_brut[:40]}"
