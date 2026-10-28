from rest_framework import serializers

from releverBancaire.models import (
    Banque,
    CompteBancaire,
    TransactionBancaire,
    TypeTransactionBancaire,
)


class BanqueSerializer(serializers.ModelSerializer):
    class Meta:
        model = Banque
        fields = ["id", "nom"]


class CompteBancaireSerializer(serializers.ModelSerializer):
    banque_nom = serializers.CharField(source="banque.nom", read_only=True)

    class Meta:
        model = CompteBancaire
        fields = ["id", "banque", "banque_nom", "numero_compte", "devise"]


class TransactionBancaireSerializer(serializers.ModelSerializer):
    nom_compte = serializers.CharField(
        source="compte.numero_compte", read_only=True
    )
    banque = serializers.CharField(source="compte.banque.nom", read_only=True)
    montant = serializers.SerializerMethodField()
    sens = serializers.SerializerMethodField()
    propositions_en_attente = serializers.IntegerField(
        read_only=True, default=0
    )

    class Meta:
        model = TransactionBancaire
        fields = [
            "id", "compte", "nom_compte", "banque", "lot",
            "date_transaction", "date_valeur", "reference_rapprochement",
            "libelle_brut", "debit", "credit", "montant", "sens",
            "solde_apres_operation", "type_transaction", "created_at",
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
            raise serializers.ValidationError(
                "Les montants débit/crédit ne peuvent pas être négatifs."
            )
        if (debit or 0) > 0 and (credit or 0) > 0:
            raise serializers.ValidationError(
                "Une transaction ne peut pas avoir simultanément un débit "
                "et un crédit."
            )
        return attrs


class LigneImportBanqueSerializer(serializers.Serializer):
    """Une ligne du JSON validé par l'utilisateur (étape « Valider »)."""

    date_transaction = serializers.DateField(input_formats=["%Y-%m-%d"])
    date_valeur = serializers.DateField(
        input_formats=["%Y-%m-%d"], required=False, allow_null=True, default=None
    )
    reference_rapprochement = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None,
        max_length=100,
    )
    libelle_brut = serializers.CharField()
    debit = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True,
        default=None,
    )
    credit = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True,
        default=None,
    )
    solde_apres_operation = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True,
        default=None,
    )
    compte = serializers.PrimaryKeyRelatedField(
        queryset=CompteBancaire.objects.select_related("banque"),
        required=False, allow_null=True, default=None,
    )
    type_transaction = serializers.ChoiceField(
        choices=TypeTransactionBancaire.choices,
        required=False,
        default=TypeTransactionBancaire.AUTRE,
    )

    def validate(self, attrs):
        debit, credit = attrs.get("debit"), attrs.get("credit")

        if (debit or 0) < 0:
            raise serializers.ValidationError(
                {"debit": "Le débit ne peut pas être négatif."}
            )
        if (credit or 0) < 0:
            raise serializers.ValidationError(
                {"credit": "Le crédit ne peut pas être négatif."}
            )
        if (debit or 0) > 0 and (credit or 0) > 0:
            raise serializers.ValidationError(
                "Une transaction ne peut pas avoir simultanément un débit "
                "et un crédit."
            )
        if not (debit or 0) and not (credit or 0):
            raise serializers.ValidationError(
                "Une transaction doit avoir un débit ou un crédit non nul."
            )
        return attrs


# Ancien nom conservé pour compatibilité.
ImportTransactionsJSONSerializer = LigneImportBanqueSerializer


class ImportBanqueSerializer(serializers.Serializer):
    compte = serializers.PrimaryKeyRelatedField(
        queryset=CompteBancaire.objects.select_related("banque"),
        required=False, allow_null=True, default=None,
    )
    nom_fichier = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=255
    )
    ignorer_doublons = serializers.BooleanField(required=False, default=False)
    transactions = LigneImportBanqueSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        for position, ligne in enumerate(attrs["transactions"], start=1):
            if not (ligne.get("compte") or attrs.get("compte")):
                raise serializers.ValidationError(
                    {
                        "compte": (
                            "Indiquez le compte bancaire (au niveau du JSON "
                            f"ou de chaque ligne, ligne {position})."
                        )
                    }
                )
        return attrs
