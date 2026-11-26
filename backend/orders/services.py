"""Order business logic (PDF section 29: OrderService, atomic transactions)."""
import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounts.models import User
from analytics.services import ScoreService
from menus.models import MenuItem
from tables.models import Table

from .models import Order, OrderItem

# No 0/O/1/I, so ids stay readable when a waiter reads them out.
_ID_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
_ID_ATTEMPTS = 10


def generate_order_id():
    """`ORD-` plus 8 random characters, e.g. `ORD-7K3F9Q2X`."""
    return 'ORD-' + ''.join(secrets.choice(_ID_ALPHABET) for _ in range(8))


class OrderService:
    @staticmethod
    def create_order(user, order_type, table_id, items):
        """Create an order and its items atomically.

        `items` is a list of `{'menu_item': <id>, 'quantity': <int>}` with no duplicate
        menu items (the serializer guarantees it). Everything is checked against the
        user's own restaurant. The table status is deliberately not changed."""
        restaurant_id = user.restaurant_id
        table = None
        if order_type == Order.Type.DINE_IN:
            table = Table.objects.filter(pk=table_id, restaurant_id=restaurant_id).first()
            if table is None:
                raise ValidationError({'table': 'Table not found in your restaurant.'})

        wanted = {line['menu_item']: line['quantity'] for line in items}
        with transaction.atomic():
            # Lock the dishes so availability and price cannot change under us.
            menu_items = {
                m.pk: m for m in MenuItem.objects
                .select_for_update(of=('self',))
                .filter(pk__in=wanted, category__restaurant_id=restaurant_id)
            }
            missing = sorted(set(wanted) - set(menu_items))
            if missing:
                raise ValidationError({'items': [
                    f'Menu item {pk} does not exist in your restaurant.' for pk in missing]})
            unavailable = sorted(m.name for m in menu_items.values() if not m.is_orderable)
            if unavailable:
                raise ValidationError({'items': [
                    f'"{name}" is currently unavailable.' for name in unavailable]})

            total = sum(
                (menu_items[pk].price * qty for pk, qty in wanted.items()), Decimal('0.00')
            )
            order = OrderService._create_with_unique_id(
                restaurant_id=restaurant_id, table=table, waiter=user,
                order_type=order_type, total_price=total,
            )
            OrderItem.objects.bulk_create([
                OrderItem(order=order, menu_item=menu_items[pk], quantity=qty,
                          price=menu_items[pk].price)
                for pk, qty in wanted.items()
            ])
            # Waiter score: +10 for a Dine-In order, credited once, in this transaction.
            ScoreService.credit_dine_in_order(order)
        return order

    @staticmethod
    def _create_with_unique_id(**fields):
        for _ in range(_ID_ATTEMPTS):
            order_id = generate_order_id()
            try:
                with transaction.atomic():  # savepoint: a collision must not poison the outer one
                    return Order.objects.create(id=order_id, **fields)
            except IntegrityError:
                if Order.objects.filter(pk=order_id).exists():
                    continue  # id collision: try another one
                raise
        raise RuntimeError('Could not generate a unique order id.')  # pragma: no cover

    # ----------------------------------------------------------------------
    # Listing
    # ----------------------------------------------------------------------

    @staticmethod
    def visible_orders(user):
        """Role-based visibility (PDF section 6) plus tenant isolation."""
        qs = Order.objects.all()
        if user.role == User.Role.PLATFORM:
            return qs                                    # all tenants, read only
        # Admin, Staff and Kitchen all see their own restaurant's orders, never another's.
        # (Staff are not limited to the orders they created themselves.)
        return qs.filter(restaurant_id=user.restaurant_id)

    @staticmethod
    def list_orders(user, status=None, active='true', table=None, date_from=None,
                    date_to=None, search=None):
        """Filtered, annotated queryset. Arguments are already validated by the view."""
        qs = OrderService.visible_orders(user)
        open_items = OrderItem.objects.filter(
            order=OuterRef('pk')).exclude(kitchen_status=OrderItem.KitchenStatus.COMPLETE)
        qs = qs.annotate(active_flag=Exists(open_items))

        if user.role in (User.Role.KITCHEN, User.Role.STAFF):
            # The kitchen only sees its queue and Staff only see active orders (PDF
            # section 6); neither can widen the view to finished orders.
            active = 'true'
        if active == 'true':
            qs = qs.filter(active_flag=True)
        elif active == 'false':
            qs = qs.filter(active_flag=False)

        if status:
            qs = qs.filter(Exists(
                OrderItem.objects.filter(order=OuterRef('pk'), kitchen_status=status)))
        if table is not None:
            qs = qs.filter(table_id=table)
        if date_from is not None:
            qs = qs.filter(created_at__gte=_day_start(date_from))
        if date_to is not None:
            qs = qs.filter(created_at__lt=_day_start(date_to + timedelta(days=1)))
        if search:
            qs = qs.filter(Q(id__icontains=search) | Q(table__table_number__icontains=search))

        qs = qs.select_related('table').prefetch_related('items__menu_item')
        # Kitchen queue: oldest first (longest open timer on top); everyone else newest first.
        return qs.order_by('created_at' if user.role == User.Role.KITCHEN else '-created_at', 'id')


def _day_start(day):
    """Start of `day` in the project time zone (timezone-aware)."""
    return timezone.make_aware(datetime.combine(day, time.min))
