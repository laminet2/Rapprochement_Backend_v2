from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from importation.models import LotImport
from importation.serializers import LotImportSerializer
from importation.services import AnnulationLotError, annuler_lot


class LotImportViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """
    Historique des imports.
      GET  /imports/                 liste (filtres : source, actifs=true)
      GET  /imports/<id>/            détail
      POST /imports/<id>/annuler/    supprime toutes les lignes du lot
                                     (refusé si certaines sont rapprochées)
    """

    serializer_class = LotImportSerializer

    def get_queryset(self):
        qs = LotImport.objects.all()
        p = self.request.query_params
        if p.get("source"):
            qs = qs.filter(source=p["source"])
        if p.get("actifs") in ("true", "1"):
            qs = qs.filter(annule_le__isnull=True)
        return qs

    @action(detail=True, methods=["post"])
    def annuler(self, request, pk=None):
        lot = self.get_object()
        try:
            resultat = annuler_lot(lot)
        except AnnulationLotError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"detail": "Import annulé.", **resultat})
