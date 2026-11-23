from django.urls import path

from .views import OrderItemStatusView

# Section 32 lists this Kitchen route under /orders, so it is mounted at that path.
urlpatterns = [
    path('orders/<str:order_id>/items/<int:item_id>', OrderItemStatusView.as_view(),
         name='order-item-status'),
]
