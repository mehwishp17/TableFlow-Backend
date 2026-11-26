from django.urls import path

from .views import LeaderboardView, MyScorecardView, WaiterScorecardView

# Included under /api/v1/ BEFORE the accounts staff routes, so that `staff/scorecards/` is
# not read as a staff id.
urlpatterns = [
    path('staff/me/scorecard/', MyScorecardView.as_view(), name='scorecard-me'),
    path('staff/scorecards/', LeaderboardView.as_view(), name='scorecard-leaderboard'),
    path('staff/<str:pk>/scorecard/', WaiterScorecardView.as_view(), name='scorecard-waiter'),
]
