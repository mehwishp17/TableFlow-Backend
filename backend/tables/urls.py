from django.urls import path

from .views import QRResolveView, TableQRPDFView, TableStatusView, TableViewSet

# Included under /api/v1/. Paths have no trailing slash like the other APIs, except
# the QR resolver, whose route (`qr/resolve/`) was specified by the team leader.
urlpatterns = [
    path('tables', TableViewSet.as_view({'get': 'list', 'post': 'create'}), name='table-list'),
    path('tables/qr-pdf', TableQRPDFView.as_view(), name='table-qr-pdf'),
    path('tables/<int:pk>', TableViewSet.as_view(
        {'get': 'retrieve', 'patch': 'partial_update', 'delete': 'destroy'}), name='table-detail'),
    path('tables/<int:pk>/status', TableStatusView.as_view(), name='table-status'),
    path('qr/resolve/', QRResolveView.as_view(), name='qr-resolve'),
]
