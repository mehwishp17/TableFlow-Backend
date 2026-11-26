from django.urls import path

from .views import RestaurantProfileView

# Included under /api/v1/.
urlpatterns = [
    path('restaurant/profile/', RestaurantProfileView.as_view(), name='restaurant-profile'),
]
