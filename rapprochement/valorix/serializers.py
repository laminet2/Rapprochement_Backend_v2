from rest_framework import serializers

from importation.models import SourceImport
from valorix.models import TransactionValorix, TypeTransactionCaisse


class TransactionValorixSerializer(serializers.ModelSerializer):
    montant = serializers.SerializerMethodField()
    sens = serializers.SerializerMethodField()
    propositions_en_attente = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = TransactionValorix
        fields = [
            "id", "lot", "numero_transaction", "date_transaction",
            "numero_compte", "nomComplet", "debit", "credit", "montant",
            "sens", "references", "types", "created_at",
            "propositions_en_attente",
        ]
        read_only_fields = ["id", "created_at", "lot"]

    def get_montant(self, obj):
        valeur = obj.debit if (obj.debit or 0) > 0 else obj.credit
        return None if valeur is None else str(valeur)

    def get_sens(self, obj):
        if (obj.debit or 0) > 0 and (obj.credit or 0) > 0:
            return "AMBIGU"
        if (obj.debit or 0) > 0:
            return "DEBIT"
        if (obj.credit or 0) > 0:
            return "CREDIT"
        return None

    def validate(self, attrs):
        debit = attrs.get("debit", getattr(self.instance, "debit", None))
        credit = attrs.get("credit", getattr(self.instance, "credit", None))
        if (debit or 0) < 0 or (credit or 0) < 0:
            raise serializers.ValidationError("Les montants ne peuvent pas être négatifs.")
        if (debit or 0) > 0 and (credit or 0) > 0:
            raise serializers.ValidationError(
                "Une transaction ne peut pas avoir simultanément un débit et un crédit."
            )
        return attrs


class ImportValorixSerializer(serializers.Serializer):
    fichier = serializers.FileField()

    def validate_fichier(self, fichier):
        if not fichier.name.lower().endswith((".xlsx", ".pdf")):
            raise serializers.ValidationError(
                "Formats acceptés : .xlsx ou .pdf."
            )
        return fichier


class ImportDocumentAutreSerializer(serializers.Serializer):
    fichier = serializers.FileField()
    sens = serializers.ChoiceField(
        choices=[("DEBIT", "Débit"), ("CREDIT", "Crédit")],
        help_text="Sens à appliquer aux montants du document.",
    )

    def validate_fichier(self, fichier):
        if not fichier.name.lower().endswith((".xlsx", ".csv", ".pdf")):
            raise serializers.ValidationError("Formats acceptés : .xlsx, .csv ou .pdf.")
        return fichier


class LigneValorixJSONSerializer(serializers.Serializer):
    """Une ligne validée par l'utilisateur (étape « Valider »)."""

    numero_transaction = serializers.CharField(
        max_length=255, allow_blank=True, required=False, default=""
    )
    date_transaction = serializers.DateTimeField()
    numero_compte = serializers.CharField(
        max_length=150, allow_blank=True, required=False, default=""
    )
    nomComplet = serializers.CharField(
        max_length=255, allow_blank=True, required=False, default=""
    )
    debit = serializers.DecimalField(
        max_digits=20, decimal_places=4, required=False, allow_null=True, default=None
    )
    credit = serializers.DecimalField(
        max_digits=20, decimal_places=4, required=False, allow_null=True, default=None
    )
    references = serializers.CharField(
        max_length=255, allow_blank=True, required=False, default=""
    )
    types = serializers.ChoiceField(choices=TypeTransactionCaisse.choices)

    def validate(self, attrs):
        debit, credit = attrs.get("debit"), attrs.get("credit")
        if (debit or 0) < 0 or (credit or 0) < 0:
            raise serializers.ValidationError("Les montants ne peuvent pas être négatifs.")
        if (debit or 0) > 0 and (credit or 0) > 0:
            raise serializers.ValidationError(
                "Une transaction ne peut pas avoir simultanément un débit et un crédit."
            )
        if not (debit or 0) and not (credit or 0):
            raise serializers.ValidationError(
                "Une transaction doit avoir un débit ou un crédit non nul."
            )
        if attrs["types"] != TypeTransactionCaisse.DOCUMENT_AUTRE and not attrs.get(
            "numero_transaction"
        ):
            raise serializers.ValidationError(
                {"numero_transaction": "Le numéro de transaction est obligatoire."}
            )
        return attrs


# Ancien nom conservé.
TransactionValorixJSONSerializer = LigneValorixJSONSerializer


class ImportValorixSaveSerializer(serializers.Serializer):
    nom_fichier = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=255
    )
    ignorer_doublons = serializers.BooleanField(required=False, default=False)
    transactions = LigneValorixJSONSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        types = {t["types"] for t in attrs["transactions"]}
        document = TypeTransactionCaisse.DOCUMENT_AUTRE
        if document in types and len(types) > 1:
            raise serializers.ValidationError(
                "Un document « autre » ne peut pas être mélangé avec un "
                "relevé Valorix dans le même import."
            )
        attrs["source"] = (
            SourceImport.DOCUMENT_AUTRE if types == {document} else SourceImport.VALORIX
        )
        return attrs
