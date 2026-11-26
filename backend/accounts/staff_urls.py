from django.urls import path

from .staff_views import OnlineStaffView, StaffDetailView, StaffListCreateView

# Included under /api/v1/staff/. The fixed sub-routes (`online/`, and the scorecard
# routes of the analytics app) must be matched before `<pk>/`; the ids "online", "me" and
# "scorecards" are reserved so a real account can never collide with them.
urlpatterns = [
    path('online/', OnlineStaffView.as_view(), name='staff-online'),
    path('', StaffListCreateView.as_view(), name='staff-list'),
    path('<str:pk>/', StaffDetailView.as_view(), name='staff-detail'),
]
