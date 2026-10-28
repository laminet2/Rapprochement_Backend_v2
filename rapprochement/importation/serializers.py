from rest_framework import serializers

from importation.models import LotImport


class LotImportSerializer(serializers.ModelSerializer):
    source_libelle = serializers.CharField(source="get_source_display", read_only=True)
    est_annule = serializers.BooleanField(read_only=True)

    class Meta:
        model = LotImport
        fields = [
            "id", "source", "source_libelle", "nom_fichier", "nombre_lignes",
            "nombre_doublons_ignores", "details", "cree_le", "annule_le",
            "est_annule",
        ]
