from django.urls import include, path
from rest_framework.routers import DefaultRouter

from releverBancaire.views import (
    BanqueViewSet,
    CompteBancaireViewSet,
    ExtraireRelevePDFView,
    ImportTransactionsJSONView,
    TransactionBancaireViewSet,
)

router = DefaultRouter()
router.register("transactions", TransactionBancaireViewSet, basename="transaction-bancaire")
router.register("banques", BanqueViewSet, basename="banque")
router.register("comptes", CompteBancaireViewSet, basename="compte-bancaire")

urlpatterns = [
    # 1. PDF -> lignes à vérifier (rien n'est enregistré)
    path("compte/<int:compte_id>/", ExtraireRelevePDFView.as_view(), name="rbc-extraire"),
    # 2. lignes vérifiées -> enregistrement dans un lot
    path("save/", ImportTransactionsJSONView.as_view(), name="rbc-save"),
    path("", include(router.urls)),
]
