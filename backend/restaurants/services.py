from django.db.models import Count, Q

from accounts.models import User
from tables.models import Table


class RestaurantService:
    @staticmethod
    def counters(restaurant):
        """Live counters, always counted from the database (nothing is stored or editable).

        Users are counted only while their account is active, so a deactivated waiter no
        longer counts towards the plan's limits."""
        active = Q(is_active=True)
        counts = User.objects.filter(restaurant=restaurant).aggregate(
            users=Count('pk', filter=active),
            staff=Count('pk', filter=active & Q(role=User.Role.STAFF)),
            kitchen_terminals=Count('pk', filter=active & Q(role=User.Role.KITCHEN)),
        )
        counts['tables'] = Table.objects.filter(restaurant=restaurant).count()
        return counts
