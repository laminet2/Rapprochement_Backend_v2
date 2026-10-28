import uuid

from django.db import models


class SourceImport(models.TextChoices):
    BANQUE = "BANQUE", "Relevé bancaire"
    VALORIX = "VALORIX", "Relevé Valorix"
    DOCUMENT_AUTRE = "DOCUMENT_AUTRE", "Document autre"


class LotImport(models.Model):
    """
    Un lot regroupe toutes les transactions enregistrées lors d'un même
    import validé par l'utilisateur.

    Il permet de savoir d'où vient une transaction, de générer les
    propositions de rapprochement sur le dernier import seulement, et
    d'annuler un import entier.

    Aucune clé étrangère vers les autres applications n'est portée ici
    (le compte bancaire est conservé dans ``details``) : ce sont elles qui
    pointent vers le lot, ce qui évite toute dépendance circulaire.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(
        max_length=20, choices=SourceImport.choices, db_index=True
    )
    nom_fichier = models.CharField(max_length=255, blank=True)
    nombre_lignes = models.PositiveIntegerField(default=0)
    nombre_doublons_ignores = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=dict, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True, db_index=True)
    annule_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Lot d'import"
        verbose_name_plural = "Lots d'import"
        ordering = ["-cree_le"]

    def __str__(self):
        return f"{self.get_source_display()} - {self.nom_fichier or self.id}"

    @property
    def est_annule(self):
        return self.annule_le is not None
