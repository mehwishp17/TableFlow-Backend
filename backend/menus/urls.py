from django.urls import path

from .views import (
    CategoryViewSet,
    MenuCatalogView,
    MenuItemAvailabilityView,
    MenuItemViewSet,
)

# Included under /api/v1/. Paths have no trailing slash, like the auth route.
# `menu` and `menu/<id>/availability` are the routes named in the PDF; the
# categories/items CRUD routes are proposals.
urlpatterns = [
    path('menu', MenuCatalogView.as_view(), name='menu-catalog'),
    path('menu/<int:item_id>/availability', MenuItemAvailabilityView.as_view(),
         name='menu-item-availability'),
    path('menu/categories', CategoryViewSet.as_view({'get': 'list', 'post': 'create'}),
         name='menu-category-list'),
    path('menu/categories/<int:pk>', CategoryViewSet.as_view(
        {'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
         name='menu-category-detail'),
    path('menu/items', MenuItemViewSet.as_view({'get': 'list', 'post': 'create'}),
         name='menu-item-list'),
    path('menu/items/<int:pk>', MenuItemViewSet.as_view(
        {'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
         name='menu-item-detail'),
]
