from django.contrib import admin

from core.models import (
    Rapprochement,
    RegleMatching,
    TransactionBancaireRapprochee,
    TransactionValorixRapprochee,
)


class TransactionBancaireInline(admin.TabularInline):
    model = TransactionBancaireRapprochee
    extra = 0
    readonly_fields = ("transaction",)


class TransactionValorixInline(admin.TabularInline):
    model = TransactionValorixRapprochee
    extra = 0
    readonly_fields = ("transaction",)


@admin.register(RegleMatching)
class RegleMatchingAdmin(admin.ModelAdmin):
    list_display = (
        "type_transaction_valorix", "sens_transaction_document_cible",
        "fenetre_jours_avant", "fenetre_jours_apres", "tolerance_fixe",
        "tolerance_pourcentage", "regroupement_max", "actif",
    )
    list_filter = ("actif",)


@admin.register(Rapprochement)
class RapprochementAdmin(admin.ModelAdmin):
    list_display = (
        "id", "statut_validation", "resultat", "origine",
        "score_correspondance", "ecart_montant", "cree_le",
    )
    list_filter = ("statut_validation", "resultat", "origine", "cree_le")
    search_fields = ("id", "commentaire")
    readonly_fields = (
        "cree_le", "valide_le", "ecart_montant", "score_correspondance",
        "score_details", "regle_snapshot",
    )
    inlines = [TransactionBancaireInline, TransactionValorixInline]
