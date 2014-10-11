from django.conf.urls import patterns, url, include
from rest_framework_extensions.routers import ExtendedSimpleRouter

from .views import CompetitionViewSet, TeamViewSet, TeamPerCompetitionViewSet, GameViewSet

router = ExtendedSimpleRouter()

competition_router = router.register(r'competitions', CompetitionViewSet)
competition_router.register(r'teams', TeamPerCompetitionViewSet,
                            base_name='competitions-teams', parents_query_lookups=['competition__teams'])

router.register(r'teams', TeamViewSet)
router.register(r'games', GameViewSet)

urlpatterns = patterns(
    'api.views',
    url(r'^api/', include([
        url(
            r'^register-by-token/(?P<backend>[^/]+)/$',
            'api.views.register_by_access_token',
            name='register_by_access_token',
        ),
        url(r'^', include(router.urls)),
        url(r'^auth/', include('rest_framework.urls', namespace='rest_framework')),
        ])),
)
