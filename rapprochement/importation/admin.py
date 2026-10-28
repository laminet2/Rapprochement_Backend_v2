from django.contrib import admin

from importation.models import LotImport


@admin.register(LotImport)
class LotImportAdmin(admin.ModelAdmin):
    list_display = (
        "id", "source", "nom_fichier", "nombre_lignes",
        "nombre_doublons_ignores", "cree_le", "annule_le",
    )
    list_filter = ("source",)
    search_fields = ("nom_fichier",)
    readonly_fields = ("cree_le",)
