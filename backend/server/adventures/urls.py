from django.urls import include, path
from rest_framework.routers import DefaultRouter
from adventures.views import *
from adventures.views.pdf_import_view import PdfImportView, PdfImportStatusView, PdfImportCollectionStatusView, PdfImportRegenerateView
from adventures.views.sso_login_view import SsoLoginView

router = DefaultRouter()
router.register(r'locations', LocationViewSet, basename='locations')
router.register(r'collections', CollectionViewSet, basename='collections')
router.register(r'stats', StatsViewSet, basename='stats')
router.register(r'generate', GenerateDescription, basename='generate')
router.register(r'tags', ActivityTypesView, basename='tags')
router.register(r'transportations', TransportationViewSet, basename='transportations')
router.register(r'notes', NoteViewSet, basename='notes')
router.register(r'checklists', ChecklistViewSet, basename='checklists')
router.register(r'images', ContentImageViewSet, basename='images')
router.register(r'reverse-geocode', ReverseGeocodeViewSet, basename='reverse-geocode')
router.register(r'categories', CategoryViewSet, basename='categories')
router.register(r'ics-calendar', IcsCalendarGeneratorViewSet, basename='ics-calendar')
router.register(r'search', GlobalSearchView, basename='search')
router.register(r'attachments', AttachmentViewSet, basename='attachments')
router.register(r'lodging', LodgingViewSet, basename='lodging')
router.register(r'recommendations', RecommendationsViewSet, basename='recommendations'),
router.register(r'backup', BackupViewSet, basename='backup')
router.register(r'trails', TrailViewSet, basename='trails')
router.register(r'activities', ActivityViewSet, basename='activities')
router.register(r'visits', VisitViewSet, basename='visits')
router.register(r'itineraries', ItineraryViewSet, basename='itineraries')
router.register(r'itinerary-days', ItineraryDayViewSet, basename='itinerary-days')

urlpatterns = [
    path('', include(router.urls)),
    path('import-pdf/', PdfImportView.as_view(), name='import-pdf'),
    path('import-pdf/<str:task_id>/', PdfImportStatusView.as_view(), name='import-pdf-status'),
    path('import-pdf/collection/<str:collection_id>/status/', PdfImportCollectionStatusView.as_view(), name='import-pdf-collection-status'),
    path('import-pdf/collection/<str:collection_id>/regenerate/', PdfImportRegenerateView.as_view(), name='import-pdf-regenerate'),
    path('sso-login/', SsoLoginView.as_view(), name='sso-login'),
]
