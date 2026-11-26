from django.db import models


class PrinterSettings(models.Model):
    """Per-restaurant configuration for the local `print.py` bridge (PDF section 19).

    The cloud backend only *stores* the folder path. It never opens, creates or checks
    that folder: the path refers to a directory on the restaurant's own computer, which
    only the local `print.py` script can reach."""

    restaurant = models.OneToOneField(
        'restaurants.Restaurant', on_delete=models.PROTECT, related_name='printer_settings')
    folder_path = models.CharField(
        max_length=260, blank=True,
        help_text='Local folder on the restaurant PC that print.py writes tickets to. '
                  'Empty means "not configured".')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Printer settings of restaurant {self.restaurant_id}'
