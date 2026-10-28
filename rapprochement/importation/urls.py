from rest_framework.routers import DefaultRouter

from importation.views import LotImportViewSet

router = DefaultRouter()
router.register("", LotImportViewSet, basename="lot-import")

urlpatterns = router.urls
