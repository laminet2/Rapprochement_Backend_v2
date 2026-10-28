from decimal import Decimal

from rest_framework import serializers

from core.models import (
    Rapprochement,
    RegleMatching,
    TransactionBancaireRapprochee,
    TransactionValorixRapprochee,
)


def _montant_sens(obj):
    debit, credit = (obj.debit or 0), (obj.credit or 0)
    if debit > 0 and credit > 0:
        return None, "AMBIGU"
    if debit > 0:
        return debit, "DEBIT"
    if credit > 0:
        return credit, "CREDIT"
    return None, None


class RegleMatchingSerializer(serializers.ModelSerializer):
    class Meta:
        model = RegleMatching
        fields = [
            "id", "type_transaction_valorix", "sens_transaction_document_cible",
            "fenetre_jours_avant", "fenetre_jours_apres", "tolerance_fixe",
            "tolerance_pourcentage", "regroupement_max", "actif",
            "date_creation",
        ]
        read_only_fields = ["id", "date_creation"]


class LigneBanqueSerializer(serializers.ModelSerializer):
    transaction_id = serializers.IntegerField(read_only=True)
    date_transaction = serializers.DateField(source="transaction.date_transaction")
    libelle_brut = serializers.CharField(source="transaction.libelle_brut")
    reference_rapprochement = serializers.CharField(source="transaction.reference_rapprochement")
    type_transaction = serializers.CharField(source="transaction.type_transaction")
    compte = serializers.CharField(source="transaction.compte.numero_compte")
    banque = serializers.CharField(source="transaction.compte.banque.nom")
    debit = serializers.DecimalField(source="transaction.debit", max_digits=20, decimal_places=2)
    credit = serializers.DecimalField(source="transaction.credit", max_digits=20, decimal_places=2)
    montant = serializers.SerializerMethodField()
    sens = serializers.SerializerMethodField()

    class Meta:
        model = TransactionBancaireRapprochee
        fields = [
            "id", "transaction_id", "date_transaction", "libelle_brut",
            "reference_rapprochement", "type_transaction", "compte", "banque",
            "debit", "credit", "montant", "sens",
        ]

    def get_montant(self, obj):
        m, _ = _montant_sens(obj.transaction)
        return None if m is None else str(m)

    def get_sens(self, obj):
        return _montant_sens(obj.transaction)[1]


class LigneValorixSerializer(serializers.ModelSerializer):
    transaction_id = serializers.UUIDField(read_only=True)
    numero_transaction = serializers.CharField(source="transaction.numero_transaction")
    date_transaction = serializers.DateTimeField(source="transaction.date_transaction")
    numero_compte = serializers.CharField(source="transaction.numero_compte")
    nom_complet = serializers.CharField(source="transaction.nomComplet")
    references = serializers.CharField(source="transaction.references")
    types = serializers.CharField(source="transaction.types")
    debit = serializers.DecimalField(source="transaction.debit", max_digits=20, decimal_places=2)
    credit = serializers.DecimalField(source="transaction.credit", max_digits=20, decimal_places=2)
    montant = serializers.SerializerMethodField()
    sens = serializers.SerializerMethodField()

    class Meta:
        model = TransactionValorixRapprochee
        fields = [
            "id", "transaction_id", "numero_transaction", "date_transaction",
            "numero_compte", "nom_complet", "references", "types", "debit",
            "credit", "montant", "sens",
        ]

    def get_montant(self, obj):
        m, _ = _montant_sens(obj.transaction)
        return None if m is None else str(m)

    def get_sens(self, obj):
        return _montant_sens(obj.transaction)[1]


def _type_depuis_comptes(nb_b, nb_v):
    if nb_b == 1 and nb_v == 1:
        return "1-1"
    if nb_b == 1:
        return "1-N"
    if nb_v == 1:
        return "N-1"
    return "N-M"


class RapprochementSerializer(serializers.ModelSerializer):
    transactions_bancaires = LigneBanqueSerializer(many=True, read_only=True)
    transactions_valorix = LigneValorixSerializer(many=True, read_only=True)
    nombre_banque = serializers.SerializerMethodField()
    nombre_valorix = serializers.SerializerMethodField()
    type_rapprochement = serializers.SerializerMethodField()
    total_banque = serializers.SerializerMethodField()
    total_valorix = serializers.SerializerMethodField()
    en_conflit = serializers.SerializerMethodField()
    bloquee = serializers.SerializerMethodField()

    class Meta:
        model = Rapprochement
        fields = [
            "id", "statut_validation", "resultat", "origine",
            "type_rapprochement", "score_correspondance", "score_details",
            "ecart_montant", "total_banque", "total_valorix",
            "nombre_banque", "nombre_valorix", "regle_snapshot",
            "en_conflit", "bloquee", "commentaire", "cree_le", "valide_le",
            "transactions_bancaires", "transactions_valorix",
        ]

    def _liens(self, obj):
        return list(obj.transactions_bancaires.all()), list(obj.transactions_valorix.all())

    def get_nombre_banque(self, obj):
        return len(obj.transactions_bancaires.all())

    def get_nombre_valorix(self, obj):
        return len(obj.transactions_valorix.all())

    def get_type_rapprochement(self, obj):
        return _type_depuis_comptes(self.get_nombre_banque(obj), self.get_nombre_valorix(obj))

    def _total(self, liens):
        return str(sum((_montant_sens(l.transaction)[0] or Decimal(0) for l in liens), Decimal(0)))

    def get_total_banque(self, obj):
        return self._total(obj.transactions_bancaires.all())

    def get_total_valorix(self, obj):
        return self._total(obj.transactions_valorix.all())

    def get_en_conflit(self, obj):
        """Une autre proposition en attente utilise l'une de ces transactions."""
        return bool(
            getattr(obj, "conflit_banque", False) or getattr(obj, "conflit_valorix", False)
        ) and obj.statut_validation == "PROPOSE"

    def get_bloquee(self, obj):
        """Une transaction est déjà rapprochée ailleurs : validation impossible."""
        return bool(
            getattr(obj, "bloquee_banque", False) or getattr(obj, "bloquee_valorix", False)
        ) and obj.statut_validation == "PROPOSE"


class MotifSerializer(serializers.Serializer):
    motif = serializers.CharField(allow_blank=False)


class GenerationSerializer(serializers.Serializer):
    lot_valorix = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    lot_banque = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    types = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    inclure_regroupements = serializers.BooleanField(required=False, default=True)


class DecisionGroupeeSerializer(serializers.Serializer):
    ids_rejetes = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    motif_rejet = serializers.CharField(required=False, allow_blank=True, default="")
    ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_null=True, default=None
    )
    lot_valorix = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    lot_banque = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    valider_le_reste = serializers.BooleanField(required=False, default=True)


class SelectionManuelleSerializer(serializers.Serializer):
    transactions_bancaires = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=False
    )
    transactions_valorix = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False
    )


class RapprochementManuelSerializer(SelectionManuelleSerializer):
    motif = serializers.CharField(required=False, allow_blank=True, default="")
    forcer = serializers.BooleanField(required=False, default=False)


class SuppressionTransactionsSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=["banque", "valorix"])
    ids = serializers.ListField(child=serializers.CharField(), allow_empty=False)
