from django.urls import path

from .views import OrderListCreateView

# Included under /api/v1/ (no trailing slash, like the other APIs).
urlpatterns = [
    path('orders', OrderListCreateView.as_view(), name='order-list'),
]
