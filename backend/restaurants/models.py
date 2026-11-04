from django.db import models


class Restaurant(models.Model):
    """A tenant of the SaaS platform (PDF section 34, `restaurants` table).

    Only the columns listed in the spec exist for now. It is here because
    restaurant-level users need something to point their foreign key at.
    """

    name = models.CharField(max_length=150)
    address = models.TextField(blank=True)
    subscription_plan = models.CharField(max_length=50, blank=True)
    logo_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
