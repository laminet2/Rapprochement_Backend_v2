import logging

from django.db.models import Count, Q
from django.http import HttpResponse
from rest_framework import generics, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import StatutValidation
from importation.pdf import ExtractionError
from valorix.importers import (
    COLONNES_REQUISES,
    enregistrer_import,
    generer_modele_document_autre,
    lire_document_autre,
    lire_releve_valorix,
    marquer_doublons_lignes,
)
from valorix.models import TransactionValorix
from valorix.serializers import (
    ImportDocumentAutreSerializer,
    ImportValorixSaveSerializer,
    ImportValorixSerializer,
    TransactionValorixSerializer,
)

logger = logging.getLogger(__name__)


def _apercu(source, fichier, lignes):
    return {
        "source": source,
        "nom_fichier": fichier.name,
        "nombre_lignes": len(lignes),
        "nombre_erreurs": sum(1 for l in lignes if l["erreurs"]),
        "nombre_doublons": sum(1 for l in lignes if l["doublon_probable"]),
        "data": lignes,
    }


class _ApercuBase(generics.GenericAPIView):
    """Étape 1 : fichier -> lignes à vérifier. N'enregistre RIEN."""

    parser_classes = [MultiPartParser, FormParser]
    source = None
    document_autre = False

    def lire(self, fichier, validated):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fichier = serializer.validated_data["fichier"]

        try:
            lignes = self.lire(fichier, serializer.validated_data)
        except ExtractionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Erreur lors de la lecture du fichier.")
            return Response(
                {"detail": "Erreur inattendue pendant la lecture du fichier."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        marquer_doublons_lignes(lignes, document_autre=self.document_autre)
        return Response(_apercu(self.source, fichier, lignes))


class ImportValorixView(_ApercuBase):
    """Relevé Valorix (.xlsx ou .pdf) -> lignes à vérifier."""

    serializer_class = ImportValorixSerializer
    source = "VALORIX"

    def lire(self, fichier, validated):
        return lire_releve_valorix(fichier)


class ImportDocumentAutreView(_ApercuBase):
    """
    Document « autre » (.xlsx, .csv ou .pdf) -> lignes à vérifier.

    Le document doit contenir les colonnes : Numéro compte client,
    Date transaction, Montant net. Le paramètre ``sens`` (DEBIT/CREDIT)
    est appliqué à tous les montants.
    """

    serializer_class = ImportDocumentAutreSerializer
    source = "DOCUMENT_AUTRE"
    document_autre = True

    def lire(self, fichier, validated):
        return lire_document_autre(fichier, validated["sens"])


class ModeleDocumentAutreView(APIView):
    """Télécharge le modèle Excel du document « autre »."""

    def get(self, request):
        reponse = HttpResponse(
            generer_modele_document_autre(),
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
        reponse["Content-Disposition"] = (
            'attachment; filename="modele_document_autre.xlsx"'
        )
        return reponse


class ColonnesDocumentAutreView(APIView):
    """Colonnes obligatoires (pour l'avertissement affiché en front)."""

    def get(self, request):
        return Response(
            {
                "colonnes_obligatoires": list(COLONNES_REQUISES.values()),
                "avertissement": (
                    "Assurez-vous que les colonnes suivantes figurent dans "
                    "le document : " + ", ".join(COLONNES_REQUISES.values()) + "."
                ),
                "formats": [".xlsx", ".csv", ".pdf"],
            }
        )


class ImportValorixJSONView(APIView):
    """
    Étape 2 (bouton « Valider ») : enregistre les lignes vérifiées dans un
    lot d'import (relevé Valorix ou document « autre », déduit de ``types``).
    """

    parser_classes = [JSONParser]

    def post(self, request, *args, **kwargs):
        donnees = request.data
        if isinstance(donnees, list):
            donnees = {"transactions": donnees}

        serializer = ImportValorixSaveSerializer(data=donnees)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        try:
            lot, creees, ignorees = enregistrer_import(
                lignes=v["transactions"],
                source=v["source"],
                nom_fichier=v["nom_fichier"],
                ignorer_doublons=v["ignorer_doublons"],
            )
        except Exception:
            logger.exception("Erreur lors de l'enregistrement Valorix.")
            return Response(
                {"detail": "Impossible d'enregistrer les transactions."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "message": "Transactions bien enregistrées.",
                "lot": str(lot.id),
                "source": v["source"],
                "nombre_enregistrees": creees,
                "nombre_ignorees_doublons": ignorees,
            },
            status=status.HTTP_201_CREATED,
        )


class TransactionValorixViewSet(viewsets.ModelViewSet):
    """
    Filtres : types, lot, q, date_min, date_max, rapprochee (true/false),
    sens (DEBIT/CREDIT).
    """

    serializer_class = TransactionValorixSerializer

    def get_queryset(self):
        qs = TransactionValorix.objects.annotate(
            propositions_en_attente=Count(
                "liens_rapprochement",
                filter=Q(
                    liens_rapprochement__rapprochement__statut_validation=(
                        StatutValidation.PROPOSE
                    )
                ),
                distinct=True,
            )
        )
        p = self.request.query_params

        if p.get("types"):
            qs = qs.filter(types=p["types"])
        if p.get("lot"):
            qs = qs.filter(lot_id=p["lot"])
        if p.get("date_min"):
            qs = qs.filter(date_transaction__date__gte=p["date_min"])
        if p.get("date_max"):
            qs = qs.filter(date_transaction__date__lte=p["date_max"])
        if p.get("sens") == "DEBIT":
            qs = qs.filter(debit__gt=0)
        elif p.get("sens") == "CREDIT":
            qs = qs.filter(credit__gt=0)
        if p.get("q"):
            q = p["q"]
            qs = qs.filter(
                Q(numero_compte__icontains=q)
                | Q(nomComplet__icontains=q)
                | Q(references__icontains=q)
                | Q(numero_transaction__icontains=q)
            )

        rapprochee = p.get("rapprochee")
        if rapprochee is not None and rapprochee != "":
            valides = Q(
                liens_rapprochement__rapprochement__statut_validation=(
                    StatutValidation.VALIDE
                )
            )
            if rapprochee.lower() in ("true", "1", "oui"):
                qs = qs.filter(valides).distinct()
            else:
                qs = qs.exclude(valides)

        return qs.order_by("-date_transaction", "-id")

    def perform_update(self, serializer):
        from core.services.transactions import est_rapprochee

        if est_rapprochee("valorix", serializer.instance.pk):
            raise ValidationError(
                "Cette transaction est déjà rapprochée : annulez d'abord "
                "le rapprochement avant de la modifier."
            )
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        from core.services.transactions import supprimer_transactions

        instance = self.get_object()
        resultat = supprimer_transactions("valorix", [instance.pk])
        if resultat["refusees"]:
            return Response(
                {"detail": resultat["refusees"][0]["raison"]},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
