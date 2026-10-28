from django.db.models import Count, Exists, OuterRef, Q
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import (
    Rapprochement,
    RegleMatching,
    StatutValidation,
    TransactionBancaireRapprochee,
    TransactionValorixRapprochee,
)
from core.serializers import (
    DecisionGroupeeSerializer,
    GenerationSerializer,
    MotifSerializer,
    RapprochementManuelSerializer,
    RapprochementSerializer,
    RegleMatchingSerializer,
    SelectionManuelleSerializer,
    SuppressionTransactionsSerializer,
)
from core.services.actions import (
    ActionRapprochementError,
    annuler_rapprochement,
    rejeter_rapprochement,
)
from core.services.decision import annuler_propositions_bloquees, decision_groupee
from core.services.manuel import (
    EcartHorsToleranceError,
    apercu_manuel,
    creer_rapprochement_manuel,
)
from core.services.proposition import generer_toutes_les_propositions
from core.services.regles import creer_regles_par_defaut
from core.services.transactions import queryset_non_rapprochees, supprimer_transactions
from core.services.validation import (
    ConflitRapprochementError,
    ValidationRapprochementError,
    valider_rapprochement,
)
from releverBancaire.models import TransactionBancaire
from releverBancaire.serializers import TransactionBancaireSerializer
from valorix.models import TransactionValorix
from valorix.serializers import TransactionValorixSerializer


def erreur_metier_vers_reponse(exc):
    if isinstance(exc, EcartHorsToleranceError):
        return Response(
            {"detail": str(exc), "apercu": exc.apercu, "forcer_requis": True},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    code = (
        status.HTTP_409_CONFLICT
        if isinstance(exc, ConflitRapprochementError)
        else status.HTTP_400_BAD_REQUEST
    )
    return Response({"detail": str(exc)}, status=code)


def _concurrence(modele_lien, rapprochement_ref="pk"):
    """Exists : une AUTRE proposition en attente partage une transaction."""
    mes_liens = modele_lien.objects.filter(
        rapprochement=OuterRef(OuterRef("pk")),
        transaction=OuterRef("transaction"),
    )
    return Exists(
        modele_lien.objects.filter(
            rapprochement__statut_validation=StatutValidation.PROPOSE
        )
        .exclude(rapprochement=OuterRef("pk"))
        .filter(Exists(mes_liens))
    )


def _bloquee(modele_lien):
    """Exists : une transaction est déjà dans un rapprochement VALIDE."""
    mes_liens = modele_lien.objects.filter(
        rapprochement=OuterRef(OuterRef("pk")),
        transaction=OuterRef("transaction"),
    )
    return Exists(
        modele_lien.objects.filter(
            rapprochement__statut_validation=StatutValidation.VALIDE
        )
        .exclude(rapprochement=OuterRef("pk"))
        .filter(Exists(mes_liens))
    )


class RapprochementQuerySetMixin:
    def get_queryset(self):
        return (
            Rapprochement.objects.annotate(
                conflit_banque=_concurrence(TransactionBancaireRapprochee),
                conflit_valorix=_concurrence(TransactionValorixRapprochee),
                bloquee_banque=_bloquee(TransactionBancaireRapprochee),
                bloquee_valorix=_bloquee(TransactionValorixRapprochee),
                nb_banque=Count("transactions_bancaires", distinct=True),
                nb_valorix=Count("transactions_valorix", distinct=True),
            )
            .prefetch_related(
                "transactions_bancaires__transaction__compte__banque",
                "transactions_valorix__transaction",
            )
        )


class RapprochementListeAPIView(RapprochementQuerySetMixin, generics.ListAPIView):
    """
    Liste paginée (``page``, ``page_size``).

    Filtres : statut_validation, resultat, origine, score_min, score_max,
    type_rapprochement (1-1 | 1-N | N-1), type_valorix, lot_valorix,
    lot_banque, q, ordering (score, -score, cree_le, -cree_le).
    """

    serializer_class = RapprochementSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params

        for champ in ("statut_validation", "resultat", "origine"):
            if p.get(champ):
                qs = qs.filter(**{champ: p[champ]})
        if p.get("score_min"):
            qs = qs.filter(score_correspondance__gte=p["score_min"])
        if p.get("score_max"):
            qs = qs.filter(score_correspondance__lte=p["score_max"])
        if p.get("type_valorix"):
            qs = qs.filter(transactions_valorix__transaction__types=p["type_valorix"])
        if p.get("lot_valorix"):
            qs = qs.filter(transactions_valorix__transaction__lot_id=p["lot_valorix"])
        if p.get("lot_banque"):
            qs = qs.filter(transactions_bancaires__transaction__lot_id=p["lot_banque"])
        if p.get("q"):
            q = p["q"]
            qs = qs.filter(
                Q(transactions_bancaires__transaction__libelle_brut__icontains=q)
                | Q(transactions_bancaires__transaction__reference_rapprochement__icontains=q)
                | Q(transactions_valorix__transaction__numero_compte__icontains=q)
                | Q(transactions_valorix__transaction__nomComplet__icontains=q)
                | Q(transactions_valorix__transaction__references__icontains=q)
            )

        # Les filtres sur des jointures peuvent dupliquer des lignes.
        qs = qs.distinct()

        type_r = p.get("type_rapprochement")
        if type_r == "1-1":
            qs = qs.filter(nb_banque=1, nb_valorix=1)
        elif type_r == "1-N":
            qs = qs.filter(nb_banque=1, nb_valorix__gt=1)
        elif type_r == "N-1":
            qs = qs.filter(nb_banque__gt=1, nb_valorix=1)

        ordre = {
            "score": ("score_correspondance", "-cree_le"),
            "-score": ("-score_correspondance", "-cree_le"),
            "cree_le": ("cree_le",),
            "-cree_le": ("-cree_le",),
        }.get(p.get("ordering"), ("-cree_le",))
        return qs.order_by(*ordre)


class RapprochementDetailAPIView(RapprochementQuerySetMixin, generics.RetrieveAPIView):
    serializer_class = RapprochementSerializer


# ------------------------------------------------------------------
# Génération
# ------------------------------------------------------------------

class GenererRapprochementsAPIView(APIView):
    """
    Génère les propositions en appliquant les règles actives (une par type).

    Corps (tout est optionnel) :
      lot_valorix: [uuid], lot_banque: [uuid], types: ["DEPOT_CHEQUE", ...],
      inclure_regroupements: true
    """

    def post(self, request):
        serializer = GenerationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        bilan = generer_toutes_les_propositions(
            lot_valorix_ids=v["lot_valorix"] or None,
            lot_banque_ids=v["lot_banque"] or None,
            types=v["types"] or None,
            inclure_regroupements=v["inclure_regroupements"],
        )
        return Response(bilan, status=status.HTTP_200_OK)


# ------------------------------------------------------------------
# Actions unitaires et groupées
# ------------------------------------------------------------------

class ValiderRapprochementAPIView(APIView):
    def post(self, request, pk):
        try:
            rapprochement = valider_rapprochement(pk, request.user)
        except ValidationRapprochementError as exc:
            return erreur_metier_vers_reponse(exc)
        return Response(
            {"detail": "Rapprochement validé.", "id": str(rapprochement.pk)}
        )


class ActionMotifRapprochementAPIView(APIView):
    action_service = None
    message = ""

    def post(self, request, pk):
        serializer = MotifSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rapprochement = self.action_service(
                rapprochement_id=pk,
                utilisateur=request.user,
                motif=serializer.validated_data["motif"],
            )
        except ActionRapprochementError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "detail": self.message,
                "id": str(rapprochement.pk),
                "statut_validation": rapprochement.statut_validation,
            }
        )


class RejeterRapprochementAPIView(ActionMotifRapprochementAPIView):
    action_service = staticmethod(rejeter_rapprochement)
    message = "Rapprochement rejeté."


class AnnulerRapprochementAPIView(ActionMotifRapprochementAPIView):
    action_service = staticmethod(annuler_rapprochement)
    message = "Rapprochement annulé."


class DecisionGroupeeAPIView(APIView):
    """
    Revue groupée : rejette ``ids_rejetes`` puis valide TOUT le reste.

    Corps : {"ids_rejetes": [...], "motif_rejet": "...",
             "lot_valorix": [...], "lot_banque": [...], "ids": [...]}
    Les propositions en conflit restent en attente et sont listées dans
    ``conflits``.
    """

    def post(self, request):
        serializer = DecisionGroupeeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        bilan = decision_groupee(
            ids_rejetes=v["ids_rejetes"],
            motif_rejet=v["motif_rejet"],
            ids=v["ids"],
            lot_valorix_ids=v["lot_valorix"] or None,
            lot_banque_ids=v["lot_banque"] or None,
            valider_le_reste=v["valider_le_reste"],
            utilisateur=request.user,
        )
        return Response(bilan)


class ValiderTousRapprochementsAPIView(APIView):
    """Conservé : valide toutes les propositions en attente."""

    def post(self, request):
        return Response(decision_groupee(utilisateur=request.user))


class NettoyerBloqueesAPIView(APIView):
    """Annule les propositions devenues impossibles (transaction déjà rapprochée)."""

    def post(self, request):
        ids = annuler_propositions_bloquees(request.user)
        return Response({"annulees": ids, "nombre": len(ids)})


# ------------------------------------------------------------------
# Rapprochement manuel
# ------------------------------------------------------------------

class ApercuManuelAPIView(APIView):
    """Totaux / écart / tolérance d'une sélection, sans rien enregistrer."""

    def post(self, request):
        serializer = SelectionManuelleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        try:
            return Response(
                apercu_manuel(v["transactions_bancaires"], v["transactions_valorix"])
            )
        except ValidationRapprochementError as exc:
            return erreur_metier_vers_reponse(exc)


class RapprochementManuelAPIView(APIView):
    """Crée directement un rapprochement validé (1-1, 1-N, N-1 ou N-M)."""

    def post(self, request):
        serializer = RapprochementManuelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        try:
            rapprochement = creer_rapprochement_manuel(
                v["transactions_bancaires"], v["transactions_valorix"],
                motif=v["motif"], forcer=v["forcer"],
            )
        except ValidationRapprochementError as exc:
            return erreur_metier_vers_reponse(exc)

        donnees = RapprochementSerializer(
            RapprochementQuerySetMixin.get_queryset(self).get(pk=rapprochement.pk)
        ).data
        return Response(donnees, status=status.HTTP_201_CREATED)


# ------------------------------------------------------------------
# Transactions non rapprochées
# ------------------------------------------------------------------

class NonRapprocheesAPIView(generics.ListAPIView):
    """
    GET ?source=banque|valorix (défaut banque), page, page_size, q,
    date_min, date_max, type, compte (banque), lot, sens.
    """

    def get_serializer_class(self):
        if self.request.query_params.get("source") == "valorix":
            return TransactionValorixSerializer
        return TransactionBancaireSerializer

    def get_queryset(self):
        p = self.request.query_params
        valorix = p.get("source") == "valorix"
        qs = queryset_non_rapprochees("valorix" if valorix else "banque")

        if valorix:
            qs = qs.order_by("-date_transaction", "-id")
            if p.get("type"):
                qs = qs.filter(types=p["type"])
            if p.get("date_min"):
                qs = qs.filter(date_transaction__date__gte=p["date_min"])
            if p.get("date_max"):
                qs = qs.filter(date_transaction__date__lte=p["date_max"])
            if p.get("q"):
                q = p["q"]
                qs = qs.filter(
                    Q(numero_compte__icontains=q) | Q(nomComplet__icontains=q)
                    | Q(references__icontains=q) | Q(numero_transaction__icontains=q)
                )
        else:
            qs = qs.select_related("compte", "compte__banque").order_by(
                "-date_transaction", "-id"
            )
            if p.get("type"):
                qs = qs.filter(type_transaction=p["type"])
            if p.get("compte"):
                qs = qs.filter(compte_id=p["compte"])
            if p.get("date_min"):
                qs = qs.filter(date_transaction__gte=p["date_min"])
            if p.get("date_max"):
                qs = qs.filter(date_transaction__lte=p["date_max"])
            if p.get("q"):
                qs = qs.filter(
                    Q(libelle_brut__icontains=p["q"])
                    | Q(reference_rapprochement__icontains=p["q"])
                )

        if p.get("lot"):
            qs = qs.filter(lot_id=p["lot"])
        if p.get("sens") == "DEBIT":
            qs = qs.filter(debit__gt=0)
        elif p.get("sens") == "CREDIT":
            qs = qs.filter(credit__gt=0)
        return qs


class SupprimerNonRapprocheesAPIView(APIView):
    """
    POST {"source": "banque"|"valorix", "ids": [...]}

    Refuse les transactions déjà rapprochées ; supprime les autres ainsi que
    les propositions (non validées) qui les contiennent.
    """

    def post(self, request):
        serializer = SuppressionTransactionsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        try:
            ids = [int(i) for i in v["ids"]] if v["source"] == "banque" else v["ids"]
        except ValueError:
            return Response(
                {"detail": "Identifiants invalides."}, status=status.HTTP_400_BAD_REQUEST
            )
        resultat = supprimer_transactions(v["source"], ids)
        return Response(resultat)


# ------------------------------------------------------------------
# Statistiques
# ------------------------------------------------------------------

class RapprochementStatistiquesAPIView(APIView):
    def get(self, request):
        par_statut = {
            ligne["statut_validation"]: ligne["total"]
            for ligne in Rapprochement.objects.values("statut_validation")
            .annotate(total=Count("id"))
        }
        par_resultat = {
            ligne["resultat"]: ligne["total"]
            for ligne in Rapprochement.objects.values("resultat")
            .annotate(total=Count("id"))
        }

        total_b = TransactionBancaire.objects.count()
        total_v = TransactionValorix.objects.count()
        non_b = queryset_non_rapprochees("banque").count()
        non_v = queryset_non_rapprochees("valorix").count()

        def taux(total, non):
            return round(100 * (total - non) / total, 1) if total else 0

        return Response(
            {
                "total": sum(par_statut.values()),
                "par_statut": par_statut,
                "par_resultat": par_resultat,
                "propositions_en_attente": par_statut.get(StatutValidation.PROPOSE, 0),
                "rapprochements_valides": par_statut.get(StatutValidation.VALIDE, 0),
                "transactions_banque": {
                    "total": total_b,
                    "non_rapprochees": non_b,
                    "taux_rapprochement": taux(total_b, non_b),
                },
                "transactions_valorix": {
                    "total": total_v,
                    "non_rapprochees": non_v,
                    "taux_rapprochement": taux(total_v, non_v),
                },
            }
        )


# ------------------------------------------------------------------
# Règles (une par type de transaction)
# ------------------------------------------------------------------

class RegleMatchingViewSet(viewsets.ModelViewSet):
    queryset = RegleMatching.objects.all()
    serializer_class = RegleMatchingSerializer
    pagination_class = None

    @action(detail=False, methods=["post"], url_path="par-defaut")
    def par_defaut(self, request):
        """Crée une règle par défaut pour chaque type qui n'en a pas."""
        creees = creer_regles_par_defaut()
        return Response(
            {
                "creees": [r.type_transaction_valorix for r in creees],
                "regles": RegleMatchingSerializer(RegleMatching.objects.all(), many=True).data,
            },
            status=status.HTTP_201_CREATED if creees else status.HTTP_200_OK,
        )
