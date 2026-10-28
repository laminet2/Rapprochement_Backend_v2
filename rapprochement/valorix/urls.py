from django.urls import include, path
from rest_framework.routers import DefaultRouter

from valorix.views import (
    ColonnesDocumentAutreView,
    ImportDocumentAutreView,
    ImportValorixJSONView,
    ImportValorixView,
    ModeleDocumentAutreView,
    TransactionValorixViewSet,
)

router = DefaultRouter()
router.register("transactions", TransactionValorixViewSet, basename="valorix-transaction")

urlpatterns = [
    # 1. fichier -> lignes à vérifier (rien n'est enregistré)
    path("", ImportValorixView.as_view(), name="valorix-extraire"),
    path("autres/", ImportDocumentAutreView.as_view(), name="valorix-autres"),
    path("autres/modele/", ModeleDocumentAutreView.as_view(), name="valorix-autres-modele"),
    path("autres/colonnes/", ColonnesDocumentAutreView.as_view(), name="valorix-autres-colonnes"),
    # 2. lignes vérifiées -> enregistrement dans un lot
    path("save/", ImportValorixJSONView.as_view(), name="valorix-save"),
    path("", include(router.urls)),
]
