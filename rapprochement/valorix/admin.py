from django.contrib import admin

from valorix.models import TransactionValorix


@admin.register(TransactionValorix)
class TransactionValorixAdmin(admin.ModelAdmin):
    list_display = [f.name for f in TransactionValorix._meta.fields]
    list_filter = ("types",)
    search_fields = ("numero_compte", "nomComplet", "references", "numero_transaction")
