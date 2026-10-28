from django.urls import include, path
from rest_framework.routers import SimpleRouter

from core.views import (
    AnnulerRapprochementAPIView,
    ApercuManuelAPIView,
    DecisionGroupeeAPIView,
    GenererRapprochementsAPIView,
    NettoyerBloqueesAPIView,
    NonRapprocheesAPIView,
    RapprochementDetailAPIView,
    RapprochementListeAPIView,
    RapprochementManuelAPIView,
    RapprochementStatistiquesAPIView,
    RegleMatchingViewSet,
    RejeterRapprochementAPIView,
    SupprimerNonRapprocheesAPIView,
    ValiderRapprochementAPIView,
    ValiderTousRapprochementsAPIView,
)

app_name = "core"

router = SimpleRouter()
router.register("regles", RegleMatchingViewSet, basename="regle")

urlpatterns = [
    path("generer/", GenererRapprochementsAPIView.as_view(), name="rapprochement-generer"),
    path("decision-groupee/", DecisionGroupeeAPIView.as_view(), name="rapprochement-decision-groupee"),
    path("valider-tout/", ValiderTousRapprochementsAPIView.as_view(), name="rapprochement-valider-tout"),
    path("nettoyer-bloquees/", NettoyerBloqueesAPIView.as_view(), name="rapprochement-nettoyer"),
    path("manuel/", RapprochementManuelAPIView.as_view(), name="rapprochement-manuel"),
    path("manuel/apercu/", ApercuManuelAPIView.as_view(), name="rapprochement-manuel-apercu"),
    path("non-rapprochees/", NonRapprocheesAPIView.as_view(), name="non-rapprochees"),
    path("non-rapprochees/supprimer/", SupprimerNonRapprocheesAPIView.as_view(), name="non-rapprochees-supprimer"),
    path("statistiques/", RapprochementStatistiquesAPIView.as_view(), name="rapprochement-statistiques"),
    path("", include(router.urls)),
    path("", RapprochementListeAPIView.as_view(), name="rapprochement-liste"),
    path("<uuid:pk>/", RapprochementDetailAPIView.as_view(), name="rapprochement-detail"),
    path("<uuid:pk>/valider/", ValiderRapprochementAPIView.as_view(), name="rapprochement-valider"),
    path("<uuid:pk>/rejeter/", RejeterRapprochementAPIView.as_view(), name="rapprochement-rejeter"),
    path("<uuid:pk>/annuler/", AnnulerRapprochementAPIView.as_view(), name="rapprochement-annuler"),
]
