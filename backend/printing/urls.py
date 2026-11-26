from django.urls import path

from .views import PrinterSettingsView

# Included under /api/v1/.
urlpatterns = [
    path('restaurant/printer-settings/', PrinterSettingsView.as_view(),
         name='printer-settings'),
]
