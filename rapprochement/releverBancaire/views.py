import logging

from django.db.models import Count, Q
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import StatutValidation
from importation.pdf import ExtractionError
from releverBancaire.importers import (
    enregistrer_import,
    extraire_releve_pdf,
    marquer_doublons_lignes,
)
from releverBancaire.models import Banque, CompteBancaire, TransactionBancaire
from releverBancaire.serializers import (
    BanqueSerializer,
    CompteBancaireSerializer,
    ImportBanqueSerializer,
    TransactionBancaireSerializer,
)

logger = logging.getLogger(__name__)


class BanqueViewSet(viewsets.ModelViewSet):
    pagination_class = None
    queryset = Banque.objects.order_by("nom")
    serializer_class = BanqueSerializer


class CompteBancaireViewSet(viewsets.ModelViewSet):
    pagination_class = None
    queryset = CompteBancaire.objects.select_related("banque").order_by(
        "banque__nom", "numero_compte"
    )
    serializer_class = CompteBancaireSerializer


class TransactionBancaireViewSet(viewsets.ModelViewSet):
    """
    Liste / détail / modification / suppression des transactions bancaires.

    Filtres : compte, type_transaction, lot, q, date_min, date_max,
    rapprochee (true/false), sens (DEBIT/CREDIT).
    """

    serializer_class = TransactionBancaireSerializer

    def get_queryset(self):
        qs = (
            TransactionBancaire.objects
            .select_related("compte", "compte__banque")
            .annotate(
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
        )
        p = self.request.query_params

        if p.get("compte"):
            qs = qs.filter(compte_id=p["compte"])
        if p.get("type_transaction"):
            qs = qs.filter(type_transaction=p["type_transaction"])
        if p.get("lot"):
            qs = qs.filter(lot_id=p["lot"])
        if p.get("date_min"):
            qs = qs.filter(date_transaction__gte=p["date_min"])
        if p.get("date_max"):
            qs = qs.filter(date_transaction__lte=p["date_max"])
        if p.get("sens") == "DEBIT":
            qs = qs.filter(debit__gt=0)
        elif p.get("sens") == "CREDIT":
            qs = qs.filter(credit__gt=0)
        if p.get("q"):
            qs = qs.filter(
                Q(libelle_brut__icontains=p["q"])
                | Q(reference_rapprochement__icontains=p["q"])
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

        if est_rapprochee("banque", serializer.instance.pk):
            raise ValidationError(
                "Cette transaction est déjà rapprochée : annulez d'abord "
                "le rapprochement avant de la modifier."
            )
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        from core.services.transactions import supprimer_transactions

        instance = self.get_object()
        resultat = supprimer_transactions("banque", [instance.pk])
        if resultat["refusees"]:
            return Response(
                {"detail": resultat["refusees"][0]["raison"]},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExtraireRelevePDFView(APIView):
    """
    Étape 1 : PDF -> lignes à vérifier. N'enregistre RIEN.
    L'enregistrement se fait ensuite via ``save/`` après validation.
    """

    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, compte_id, format=None):
        try:
            compte = CompteBancaire.objects.select_related("banque").get(
                id=compte_id
            )
        except CompteBancaire.DoesNotExist:
            return Response(
                {"error": "Compte bancaire introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )

        fichier = request.FILES.get("fichier")
        if not fichier:
            return Response(
                {"error": "Fichier PDF manquant (clé 'fichier')."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            lignes = extraire_releve_pdf(fichier)
        except ExtractionError as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        except Exception:
            logger.exception("Erreur lors de l'extraction du relevé bancaire.")
            return Response(
                {"error": "Erreur inattendue pendant l'extraction du relevé."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        marquer_doublons_lignes(lignes, compte)

        return Response(
            {
                "source": "BANQUE",
                "compte": compte.id,
                "nom_fichier": fichier.name,
                "nombre_lignes": len(lignes),
                "nombre_erreurs": sum(1 for l in lignes if l["erreurs"]),
                "nombre_doublons": sum(1 for l in lignes if l["doublon_probable"]),
                "data": lignes,
            },
            status=status.HTTP_200_OK,
        )


class ImportTransactionsJSONView(APIView):
    """
    Étape 2 (bouton « Valider ») : enregistre les lignes vérifiées dans un
    lot d'import.

    Corps accepté :
        {"compte": 1, "nom_fichier": "...", "ignorer_doublons": false,
         "transactions": [ {...}, ... ]}
    ou, pour compatibilité, une simple liste de lignes contenant ``compte``.
    """

    parser_classes = [JSONParser]

    def post(self, request, *args, **kwargs):
        donnees = request.data
        if isinstance(donnees, list):
            donnees = {"transactions": donnees}

        serializer = ImportBanqueSerializer(data=donnees)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        try:
            lot, creees, ignorees = enregistrer_import(
                lignes=v["transactions"],
                compte_par_defaut=v["compte"],
                nom_fichier=v["nom_fichier"],
                ignorer_doublons=v["ignorer_doublons"],
            )
        except Exception:
            logger.exception("Erreur lors de l'enregistrement du relevé.")
            return Response(
                {"error": "Impossible d'enregistrer les transactions."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "message": "Transactions bien enregistrées.",
                "lot": str(lot.id),
                "nombre_enregistrees": creees,
                "nombre_ignorees_doublons": ignorees,
            },
            status=status.HTTP_201_CREATED,
        )
