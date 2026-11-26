from django.urls import path

from .call_views import (
    CallCancelView,
    CallListCreateView,
    CallPickView,
    CallResolveView,
    CustomerCallView,
)
from .views import OrderItemStatusView

# Included under /api/v1/.
urlpatterns = [
    # Section 32 lists this Kitchen route under /orders, so it is mounted at that path.
    path('orders/<str:order_id>/items/<int:item_id>', OrderItemStatusView.as_view(),
         name='order-item-status'),

    # Waiter call alerts (confirmed routes: /api/v1/calls/...).
    path('calls/', CallListCreateView.as_view(), name='call-list'),
    path('calls/customer/', CustomerCallView.as_view(), name='call-customer'),
    path('calls/<int:pk>/pick/', CallPickView.as_view(), name='call-pick'),
    path('calls/<int:pk>/resolve/', CallResolveView.as_view(), name='call-resolve'),
    path('calls/<int:pk>/cancel/', CallCancelView.as_view(), name='call-cancel'),
]
