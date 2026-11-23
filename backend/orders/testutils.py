"""Shared fixtures for the orders and kitchen tests (not a test module itself)."""
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from menus.models import Category, MenuItem
from orders.models import Order
from orders.services import OrderService
from restaurants.models import Restaurant
from tables.models import Table

ORDERS_URL = '/api/v1/orders'


def item_url(order_id, item_id):
    return f'/api/v1/orders/{order_id}/items/{item_id}'


def client_for(user):
    """A client holding a real JWT for `user` (no password hashing needed)."""
    client = APIClient()
    if user is not None:
        token = RefreshToken.for_user(user)
        token['role'] = user.role
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
    return client


def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=dt_timezone.utc)


class OrderFixtureMixin:
    """Two restaurants with a full set of users, dishes and tables.

    Use as `class MyTests(OrderFixtureMixin, APITestCase)`."""

    @classmethod
    def setUpTestData(cls):
        cls.r1 = Restaurant.objects.create(name='Bistro One')
        cls.r2 = Restaurant.objects.create(name='Bistro Two')

        def user(uid, role, restaurant):
            return User.objects.create_user(uid, None, role=role, restaurant=restaurant)

        cls.admin1 = user('admin1', 'Admin', cls.r1)
        cls.staff1 = user('staff1', 'Staff', cls.r1)
        cls.staff1b = user('staff1b', 'Staff', cls.r1)
        cls.kitchen1 = user('kitchen1', 'Kitchen', cls.r1)
        cls.platform = user('platform1', 'Platform', None)
        cls.admin2 = user('admin2', 'Admin', cls.r2)
        cls.staff2 = user('staff2', 'Staff', cls.r2)
        cls.kitchen2 = user('kitchen2', 'Kitchen', cls.r2)

        cat1 = Category.objects.create(restaurant=cls.r1, name='Mains')
        cat2 = Category.objects.create(restaurant=cls.r2, name='Mains')
        cls.pasta = MenuItem.objects.create(
            category=cat1, name='Pasta', price=Decimal('12.50'), dish_type='Veg')
        cls.tea = MenuItem.objects.create(
            category=cat1, name='Tea', price=Decimal('3.00'), dish_type='Veg')
        cls.soup = MenuItem.objects.create(
            category=cat1, name='Soup', price=Decimal('5.25'), dish_type='Veg',
            is_available=False)
        cls.other_dish = MenuItem.objects.create(
            category=cat2, name='Curry', price=Decimal('9.99'), dish_type='Non-Veg')

        cls.t1 = Table.objects.create(restaurant=cls.r1, table_number='T1', capacity=4)
        cls.t2 = Table.objects.create(restaurant=cls.r1, table_number='T2', capacity=2)
        cls.t3 = Table.objects.create(restaurant=cls.r2, table_number='T3', capacity=4)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def place(user, order_type='Dine-In', table=None, lines=()):
        """Create an order through the service. `lines` = [(menu_item, quantity), ...]."""
        return OrderService.create_order(
            user, order_type, table.pk if table else None,
            [{'menu_item': item.pk, 'quantity': qty} for item, qty in lines])

    @staticmethod
    def set_created(order, when):
        Order.objects.filter(pk=order.pk).update(created_at=when)

    @staticmethod
    def ids(response):
        return [o['id'] for o in response.json()['data']]
