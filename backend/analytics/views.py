from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from menus.views import _E401, _E403, _E404, EnvelopeMixin, _envelope

from .permissions import CanViewScorecard, IsAdminRole, IsStaffRole
from .serializers import ScorecardSerializer
from .services import ScoreService

_AUTH_NOTE = ' Requires a JWT bearer token.'
_RULES = (
    ' Scoring: +10 per Dine-In order the waiter creates, +5 per call the waiter picks and '
    'resolves, +5 bonus when the call was picked within 2 minutes of being triggered. '
    '`total_score` is the sum of all three; `bonus_points` is the third part alone.'
)


class MyScorecardView(EnvelopeMixin, APIView):
    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=['Scorecards'], summary='My scorecard (Staff)',
        description='Role: **Staff** (the waiter\'s own personal score).' + _RULES + _AUTH_NOTE,
        responses={200: _envelope('MyScorecardEnvelope', ScorecardSerializer),
                   401: _E401, 403: _E403},
    )
    def get(self, request):
        return Response(ScoreService.scorecard_of(request.user))


class WaiterScorecardView(EnvelopeMixin, APIView):
    permission_classes = [CanViewScorecard]

    @extend_schema(
        tags=['Scorecards'], summary='Scorecard of one waiter',
        description='Roles: **Admin** (any waiter of their restaurant) and **Staff** (only '
                    'their own id; another waiter\'s id gives 403). A waiter of another '
                    'restaurant, or an id that is not a waiter, gives 404 for the Admin.'
                    + _RULES + _AUTH_NOTE,
        responses={200: _envelope('WaiterScorecardEnvelope', ScorecardSerializer),
                   401: _E401, 403: _E403, 404: _E404},
    )
    def get(self, request, pk):
        user = request.user
        if user.role == User.Role.STAFF and pk != user.pk:
            raise PermissionDenied('Staff can only see their own scorecard.')
        waiter = get_object_or_404(ScoreService.waiters_of(user.restaurant_id), pk=pk)
        return Response(ScoreService.scorecard_of(waiter))


class LeaderboardView(EnvelopeMixin, APIView):
    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=['Scorecards'], summary='Scorecards of all waiters (Admin analytics)',
        description='Role: **Admin** only. One scorecard per waiter of the Admin\'s own '
                    'restaurant, best score first: every active waiter (including those with '
                    'no points yet) plus deactivated waiters who earned points '
                    '(`is_active: false`). Deactivated waiters with no points are not listed, '
                    'but their history is kept and `GET /staff/{id}/scorecard/` still works. '
                    'Uses exactly the same calculation as the personal scorecard.'
                    + _RULES + _AUTH_NOTE,
        responses={200: _envelope('LeaderboardEnvelope', ScorecardSerializer, many=True),
                   401: _E401, 403: _E403},
    )
    def get(self, request):
        return Response(ScoreService.leaderboard(request.user.restaurant_id))
