import re
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, transaction
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from menus.models import MenuItem
from orders.models import Order, OrderItem
from orders.services import OrderService, generate_order_id
from tables.models import Table

from .testutils import ORDERS_URL, OrderFixtureMixin, client_for, utc

ID_PATTERN = re.compile(r'^ORD-[A-Z2-9]{8}$')


def dine_in(table, *lines):
    return {'order_type': 'Dine-In', 'table': table.pk,
            'items': [{'menu_item': item.pk, 'quantity': qty} for item, qty in lines]}


def parcel(*lines):
    return {'order_type': 'Parcel',
            'items': [{'menu_item': item.pk, 'quantity': qty} for item, qty in lines]}


# --------------------------------------------------------------------------
# POST /api/v1/orders
# --------------------------------------------------------------------------

class OrderCreateTests(OrderFixtureMixin, APITestCase):
    def post(self, user, payload):
        return client_for(user).post(ORDERS_URL, payload, format='json')

    def test_dine_in_order_with_multiple_items_and_total(self):
        r = self.post(self.staff1, dine_in(self.t1, (self.pasta, 2), (self.tea, 1)))
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual((body['error'], body['status']), (None, 'success'))
        data = body['data']
        self.assertRegex(data['id'], ID_PATTERN)
        self.assertEqual(data['order_type'], 'Dine-In')
        self.assertEqual(data['restaurant_id'], self.r1.pk)
        self.assertEqual(data['waiter_id'], 'staff1')
        self.assertEqual(data['table'], {'id': self.t1.pk, 'table_number': 'T1'})
        self.assertEqual(data['total_price'], '28.00')      # 2 x 12.50 + 1 x 3.00
        self.assertTrue(data['is_active'])
        self.assertEqual(data['status_summary'], {'Pickup': 2, 'Processed': 0, 'Complete': 0})
        self.assertEqual(len(data['items']), 2)
        self.assertEqual({i['kitchen_status'] for i in data['items']}, {'Pickup'})
        by_name = {i['name']: i for i in data['items']}
        self.assertEqual((by_name['Pasta']['quantity'], by_name['Pasta']['price']), (2, '12.50'))
        self.assertEqual((by_name['Tea']['quantity'], by_name['Tea']['price']), (1, '3.00'))
        # persisted
        order = Order.objects.get(pk=data['id'])
        self.assertEqual((order.restaurant_id, order.waiter_id, order.table_id),
                         (self.r1.pk, 'staff1', self.t1.pk))
        self.assertEqual(order.total_price, Decimal('28.00'))
        self.assertEqual(order.items.count(), 2)

    def test_parcel_order_has_no_table(self):
        r = self.post(self.admin1, parcel((self.tea, 3)))
        self.assertEqual(r.status_code, 201)
        data = r.json()['data']
        self.assertEqual(data['order_type'], 'Parcel')
        self.assertIsNone(data['table'])
        self.assertEqual(data['total_price'], '9.00')
        self.assertEqual(data['waiter_id'], 'admin1')
        self.assertIsNone(Order.objects.get(pk=data['id']).table_id)

    def test_parcel_with_explicit_null_table_is_fine(self):
        payload = parcel((self.tea, 1))
        payload['table'] = None
        self.assertEqual(self.post(self.staff1, payload).status_code, 201)

    def test_creating_an_order_does_not_change_table_status(self):
        self.post(self.staff1, dine_in(self.t1, (self.tea, 1)))
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, 'Available')

    def test_response_is_idempotent_with_the_database(self):
        r = self.post(self.staff1, dine_in(self.t1, (self.pasta, 1)))
        listed = client_for(self.staff1).get(ORDERS_URL).json()['data']
        self.assertEqual(listed[0], r.json()['data'])

    # -- roles ---------------------------------------------------------------

    def test_admin_and_staff_may_create(self):
        for user in (self.admin1, self.staff1):
            self.assertEqual(self.post(user, parcel((self.tea, 1))).status_code, 201, user.id)

    def test_staff_may_create_orders_for_any_table_of_their_restaurant(self):
        # No table-assignment rule: two different Staff members, same and different tables.
        for user, table in ((self.staff1, self.t1), (self.staff1b, self.t1),
                            (self.staff1b, self.t2), (self.staff1, self.t2)):
            with self.subTest(user=user.id, table=table.table_number):
                r = self.post(user, dine_in(table, (self.tea, 1)))
                self.assertEqual(r.status_code, 201)
                self.assertEqual(r.json()['data']['table']['id'], table.pk)
                self.assertEqual(r.json()['data']['waiter_id'], user.id)
        # ...but never a table of another restaurant
        self.assertEqual(self.post(self.staff1b, dine_in(self.t3, (self.tea, 1))).status_code, 400)
        self.assertEqual(Order.objects.count(), 4)

    def test_kitchen_and_platform_may_not_create(self):
        for user in (self.kitchen1, self.platform):
            r = self.post(user, parcel((self.tea, 1)))
            self.assertEqual(r.status_code, 403, user.id)
            self.assertEqual(r.json()['error']['code'], 'permission_denied')
        self.assertEqual(Order.objects.count(), 0)

    # -- validation ----------------------------------------------------------

    def test_dine_in_requires_a_table(self):
        payload = dine_in(self.t1, (self.tea, 1))
        del payload['table']
        r = self.post(self.staff1, payload)
        self.assertEqual(r.status_code, 400)
        self.assertIn('table', r.json()['error']['details'])
        payload['table'] = None
        self.assertEqual(self.post(self.staff1, payload).status_code, 400)

    def test_parcel_must_not_have_a_table(self):
        payload = parcel((self.tea, 1))
        payload['table'] = self.t1.pk
        r = self.post(self.staff1, payload)
        self.assertEqual(r.status_code, 400)
        self.assertIn('table', r.json()['error']['details'])

    def test_table_of_another_restaurant_is_rejected(self):
        r = self.post(self.staff1, dine_in(self.t3, (self.tea, 1)))
        self.assertEqual(r.status_code, 400)
        self.assertIn('table', r.json()['error']['details'])
        self.assertEqual(Order.objects.count(), 0)

    def test_unknown_table_is_rejected(self):
        payload = dine_in(self.t1, (self.tea, 1))
        payload['table'] = 999999
        self.assertEqual(self.post(self.staff1, payload).status_code, 400)

    def test_invalid_order_type_and_missing_fields(self):
        self.assertEqual(self.post(self.staff1, {'order_type': 'Delivery', 'items': [
            {'menu_item': self.tea.pk, 'quantity': 1}]}).status_code, 400)
        self.assertEqual(self.post(self.staff1, {'items': [
            {'menu_item': self.tea.pk, 'quantity': 1}]}).status_code, 400)
        self.assertEqual(self.post(self.staff1, {'order_type': 'Parcel'}).status_code, 400)
        self.assertEqual(self.post(self.staff1, {}).status_code, 400)

    def test_empty_items_rejected(self):
        self.assertEqual(self.post(self.staff1, parcel()).status_code, 400)

    def test_quantity_validation(self):
        for bad in (0, -1, 100, 'abc', 1.5, None):
            payload = parcel((self.tea, 1))
            payload['items'][0]['quantity'] = bad
            r = self.post(self.staff1, payload)
            self.assertEqual(r.status_code, 400, f'quantity={bad!r}')
        for good in (1, 99):
            payload = parcel((self.tea, 1))
            payload['items'][0]['quantity'] = good
            self.assertEqual(self.post(self.staff1, payload).status_code, 201, good)

    def test_missing_quantity_or_menu_item_rejected(self):
        self.assertEqual(self.post(self.staff1, {'order_type': 'Parcel', 'items': [
            {'menu_item': self.tea.pk}]}).status_code, 400)
        self.assertEqual(self.post(self.staff1, {'order_type': 'Parcel', 'items': [
            {'quantity': 1}]}).status_code, 400)

    def test_duplicate_menu_item_lines_rejected(self):
        r = self.post(self.staff1, parcel((self.tea, 1), (self.tea, 2)))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_unavailable_menu_item_rejected_and_nothing_saved(self):
        r = self.post(self.staff1, parcel((self.tea, 1), (self.soup, 1)))
        self.assertEqual(r.status_code, 400)
        self.assertIn('Soup', str(r.json()['error']['details']))
        self.assertEqual((Order.objects.count(), OrderItem.objects.count()), (0, 0))

    def test_unavailable_item_becomes_orderable_again(self):
        self.soup.is_available = True
        self.soup.save()
        self.assertEqual(self.post(self.staff1, parcel((self.soup, 1))).status_code, 201)

    def test_nonexistent_menu_item_rejected(self):
        payload = parcel((self.tea, 1))
        payload['items'].append({'menu_item': 999999, 'quantity': 1})
        r = self.post(self.staff1, payload)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_menu_item_of_another_restaurant_rejected(self):
        r = self.post(self.staff1, parcel((self.other_dish, 1)))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_restaurant_and_waiter_cannot_be_set_from_the_body(self):
        payload = parcel((self.tea, 1))
        payload.update({'restaurant': self.r2.pk, 'restaurant_id': self.r2.pk,
                        'waiter': 'admin2', 'waiter_id': 'admin2', 'total_price': '0.01',
                        'id': 'ORD-HACKED00'})
        r = self.post(self.staff1, payload)
        self.assertEqual(r.status_code, 201)
        order = Order.objects.get(pk=r.json()['data']['id'])
        self.assertEqual((order.restaurant_id, order.waiter_id), (self.r1.pk, 'staff1'))
        self.assertEqual(order.total_price, Decimal('3.00'))
        self.assertNotEqual(order.pk, 'ORD-HACKED00')

    # -- prices & atomicity --------------------------------------------------

    def test_unit_price_is_stored_at_order_time(self):
        first = self.post(self.staff1, parcel((self.pasta, 1))).json()['data']
        self.pasta.price = Decimal('20.00')
        self.pasta.save()
        second = self.post(self.staff1, parcel((self.pasta, 1))).json()['data']
        self.assertEqual(first['items'][0]['price'], '12.50')
        self.assertEqual(second['items'][0]['price'], '20.00')
        self.assertEqual(Order.objects.get(pk=first['id']).total_price, Decimal('12.50'))
        self.assertEqual(
            OrderItem.objects.get(order_id=first['id']).price, Decimal('12.50'))

    def test_total_uses_exact_decimal_arithmetic(self):
        dish = MenuItem.objects.create(
            category=self.pasta.category, name='Odd', price=Decimal('0.10'), dish_type='Veg')
        r = self.post(self.staff1, parcel((dish, 3)))
        self.assertEqual(r.json()['data']['total_price'], '0.30')

    def test_zero_priced_item_is_allowed(self):
        free = MenuItem.objects.create(
            category=self.pasta.category, name='Water', price=Decimal('0.00'), dish_type='Veg')
        r = self.post(self.staff1, parcel((free, 2)))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['data']['total_price'], '0.00')

    def test_order_and_items_are_created_atomically(self):
        with mock.patch('orders.services.OrderItem.objects.bulk_create',
                        side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.place(self.staff1, 'Parcel', None, [(self.tea, 1)])
        self.assertEqual((Order.objects.count(), OrderItem.objects.count()), (0, 0))

    def test_order_id_collision_is_retried(self):
        existing = self.place(self.staff1, 'Parcel', None, [(self.tea, 1)])
        with mock.patch('orders.services.generate_order_id',
                        side_effect=[existing.pk, 'ORD-ABCDEFGH']):
            new = self.place(self.staff1, 'Parcel', None, [(self.tea, 1)])
        self.assertEqual(new.pk, 'ORD-ABCDEFGH')
        self.assertEqual(Order.objects.count(), 2)

    def test_generated_ids_have_the_documented_format_and_are_unique(self):
        ids = {generate_order_id() for _ in range(500)}
        self.assertEqual(len(ids), 500)
        self.assertTrue(all(ID_PATTERN.match(i) for i in ids))

    # -- authentication ------------------------------------------------------

    def test_post_requires_authentication(self):
        r = client_for(None).post(ORDERS_URL, parcel((self.tea, 1)), format='json')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error']['code'], 'not_authenticated')


# --------------------------------------------------------------------------
# GET /api/v1/orders
# --------------------------------------------------------------------------

class OrderListTests(OrderFixtureMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # o1: Staff 1, T1, two items          o2: Staff 1b, T2, one item
        # o3: Admin 1, Parcel, finished       o4: restaurant two
        cls.o1 = cls.place(cls.staff1, 'Dine-In', cls.t1, [(cls.pasta, 1), (cls.tea, 2)])
        cls.o2 = cls.place(cls.staff1b, 'Dine-In', cls.t2, [(cls.pasta, 1)])
        cls.o3 = cls.place(cls.admin1, 'Parcel', None, [(cls.tea, 1)])
        cls.o3.items.update(kitchen_status='Complete')
        cls.o4 = cls.place(cls.staff2, 'Dine-In', cls.t3, [(cls.other_dish, 1)])
        cls.set_created(cls.o1, utc(2026, 11, 10, 12))
        cls.set_created(cls.o2, utc(2026, 11, 12, 23, 59, 59))
        cls.set_created(cls.o3, utc(2026, 11, 13, 0, 0, 0))
        cls.set_created(cls.o4, utc(2026, 11, 11, 9))

    def get(self, user, **params):
        return client_for(user).get(ORDERS_URL, params)

    # -- visibility per role ---------------------------------------------------

    def test_admin_sees_active_orders_of_own_restaurant_newest_first(self):
        r = self.get(self.admin1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.ids(r), [self.o2.pk, self.o1.pk])

    def test_active_parameter(self):
        self.assertEqual(self.ids(self.get(self.admin1, active='all')),
                         [self.o3.pk, self.o2.pk, self.o1.pk])
        self.assertEqual(self.ids(self.get(self.admin1, active='false')), [self.o3.pk])
        self.assertEqual(self.ids(self.get(self.admin1, active='true')),
                         [self.o2.pk, self.o1.pk])

    def test_staff_sees_all_active_orders_of_their_restaurant(self):
        # o1 was created by staff1 and o2 by staff1b: both Staff see both, newest first.
        for user in (self.staff1, self.staff1b):
            with self.subTest(user=user.id):
                r = self.get(user)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(self.ids(r), [self.o2.pk, self.o1.pk])
        # the Admin's own order o3 is finished, so it is not in the default (active) list
        self.assertNotIn(self.o3.pk, self.ids(self.get(self.staff1)))
        # an order the Admin creates is visible to Staff while it is active
        o5 = self.place(self.admin1, 'Dine-In', self.t2, [(self.tea, 1)])
        self.assertIn(o5.pk, self.ids(self.get(self.staff1)))

    def test_staff_never_sees_orders_of_another_restaurant(self):
        for params in ({}, {'active': 'all'}, {'active': 'false'}, {'search': 'ORD'},
                       {'table': self.t3.pk}):
            with self.subTest(params=params):
                for user in (self.staff1, self.staff1b):
                    self.assertNotIn(self.o4.pk, self.ids(self.get(user, **params)), user.id)
        # and the other restaurant's Staff see only their own restaurant's orders
        self.assertEqual(self.ids(self.get(self.staff2, active='all')), [self.o4.pk])
        # a table id of another restaurant simply matches nothing
        self.assertEqual(self.ids(self.get(self.staff1, table=self.t3.pk, active='all')), [])

    def test_completed_orders_are_not_in_the_default_staff_list(self):
        self.assertNotIn(self.o3.pk, self.ids(self.get(self.staff1)))
        self.assertNotIn(self.o3.pk, self.ids(self.get(self.staff1, active='true')))
        # Staff cannot widen the view to finished orders, whatever they ask for
        for active in ('false', 'all', 'true'):
            with self.subTest(active=active):
                for user in (self.staff1, self.staff1b):
                    r = self.get(user, active=active)
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(self.ids(r), [self.o2.pk, self.o1.pk], user.id)
        # the status filter cannot be used to reach finished orders either
        self.assertEqual(self.ids(self.get(self.staff1, status='Complete', active='all')), [])
        # Admin keeps the full history, unchanged
        self.assertEqual(self.ids(self.get(self.admin1, active='false')), [self.o3.pk])
        # finishing o1 removes it from the other waiter's list too
        self.o1.items.update(kitchen_status='Complete')
        self.assertEqual(self.ids(self.get(self.staff1b)), [self.o2.pk])

    def test_kitchen_sees_the_active_queue_oldest_first(self):
        r = self.get(self.kitchen1)
        self.assertEqual(self.ids(r), [self.o1.pk, self.o2.pk])
        # the kitchen cannot widen its view to finished orders
        self.assertEqual(self.ids(self.get(self.kitchen1, active='all')),
                         [self.o1.pk, self.o2.pk])
        self.assertEqual(self.ids(self.get(self.kitchen1, active='false')),
                         [self.o1.pk, self.o2.pk])

    def test_platform_sees_all_tenants(self):
        r = self.get(self.platform, active='all')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(self.ids(r)), {self.o1.pk, self.o2.pk, self.o3.pk, self.o4.pk})
        self.assertEqual({o['restaurant_id'] for o in r.json()['data']},
                         {self.r1.pk, self.r2.pk})

    def test_tenant_isolation(self):
        for user in (self.admin2, self.staff2, self.kitchen2):
            self.assertEqual(self.ids(self.get(user, active='all')), [self.o4.pk], user.id)
        for user in (self.admin1, self.staff1, self.kitchen1):
            self.assertNotIn(self.o4.pk, self.ids(self.get(user, active='all')), user.id)

    # -- filters ----------------------------------------------------------------

    def test_status_filter_uses_item_states(self):
        client_items = list(self.o1.items.order_by('id'))
        client_items[0].kitchen_status = 'Processed'
        client_items[0].save()
        # o1: Processed + Pickup, o2: Pickup, o3: Complete (inactive)
        self.assertEqual(self.ids(self.get(self.admin1, status='Pickup')),
                         [self.o2.pk, self.o1.pk])
        self.assertEqual(self.ids(self.get(self.admin1, status='Processed')), [self.o1.pk])
        self.assertEqual(self.ids(self.get(self.admin1, status='Complete')), [])
        self.assertEqual(self.ids(self.get(self.admin1, status='Complete', active='all')),
                         [self.o3.pk])

    def test_order_with_mixed_item_states_is_represented_by_a_summary(self):
        items = list(self.o1.items.order_by('id'))
        items[0].kitchen_status = 'Complete'
        items[0].save()
        data = self.get(self.admin1).json()['data']
        order = next(o for o in data if o['id'] == self.o1.pk)
        self.assertTrue(order['is_active'])
        self.assertEqual(order['status_summary'], {'Pickup': 1, 'Processed': 0, 'Complete': 1})
        # it matches both documented states it contains
        self.assertIn(self.o1.pk, self.ids(self.get(self.admin1, status='Complete')))
        self.assertIn(self.o1.pk, self.ids(self.get(self.admin1, status='Pickup')))

    def test_order_becomes_inactive_when_all_items_complete(self):
        self.o2.items.update(kitchen_status='Complete')
        self.assertNotIn(self.o2.pk, self.ids(self.get(self.admin1)))
        self.assertIn(self.o2.pk, self.ids(self.get(self.admin1, active='false')))

    def test_table_filter(self):
        self.assertEqual(self.ids(self.get(self.admin1, table=self.t1.pk)), [self.o1.pk])
        self.assertEqual(self.ids(self.get(self.admin1, table=self.t2.pk)), [self.o2.pk])
        # a table of another restaurant never leaks anything
        self.assertEqual(self.ids(self.get(self.admin1, table=self.t3.pk, active='all')), [])
        self.assertEqual(self.ids(self.get(self.platform, table=self.t3.pk)), [self.o4.pk])

    def test_date_from_is_inclusive(self):
        r = self.get(self.admin1, date_from='2026-11-12', active='all')
        self.assertEqual(self.ids(r), [self.o3.pk, self.o2.pk])

    def test_date_to_is_inclusive_up_to_the_end_of_the_day(self):
        r = self.get(self.admin1, date_to='2026-11-12', active='all')
        self.assertEqual(self.ids(r), [self.o2.pk, self.o1.pk])   # o2 at 23:59:59 is in

    def test_single_day_and_range(self):
        self.assertEqual(self.ids(self.get(
            self.admin1, date_from='2026-11-12', date_to='2026-11-12', active='all')),
            [self.o2.pk])
        self.assertEqual(self.ids(self.get(
            self.admin1, date_from='2026-11-10', date_to='2026-11-13', active='all')),
            [self.o3.pk, self.o2.pk, self.o1.pk])
        self.assertEqual(self.ids(self.get(
            self.admin1, date_from='2026-12-01', active='all')), [])

    def test_search_by_order_id(self):
        self.assertEqual(self.ids(self.get(self.admin1, search=self.o1.pk)), [self.o1.pk])
        self.assertEqual(self.ids(self.get(self.admin1, search=self.o1.pk.lower())), [self.o1.pk])
        self.assertEqual(self.ids(self.get(self.admin1, search=self.o1.pk[4:9])), [self.o1.pk])

    def test_search_by_table_number(self):
        self.assertEqual(self.ids(self.get(self.admin1, search='T1')), [self.o1.pk])
        self.assertEqual(self.ids(self.get(self.admin1, search='t2')), [self.o2.pk])

    def test_search_without_match_and_no_cross_tenant_leak(self):
        self.assertEqual(self.ids(self.get(self.admin1, search='NOPE-NOPE')), [])
        self.assertEqual(self.ids(self.get(self.admin1, search=self.o4.pk, active='all')), [])
        self.assertEqual(self.ids(self.get(self.admin1, search='T3', active='all')), [])

    def test_filters_combine(self):
        r = self.get(self.admin1, status='Pickup', table=self.t1.pk,
                     date_from='2026-11-10', date_to='2026-11-10', search='T1')
        self.assertEqual(self.ids(r), [self.o1.pk])
        r = self.get(self.admin1, status='Pickup', table=self.t2.pk, search='T1')
        self.assertEqual(self.ids(r), [])

    def test_invalid_filters_are_rejected(self):
        for params in ({'status': 'Cooking'}, {'status': 'pickup'}, {'active': 'maybe'},
                       {'table': 'abc'}, {'table': '0'}, {'date_from': '12-11-2026'},
                       {'date_to': 'yesterday'}, {'date_from': '2026-13-01'},
                       {'date_from': '2026-11-13', 'date_to': '2026-11-12'},
                       {'search': 'x' * 51}):
            r = self.get(self.admin1, **params)
            self.assertEqual(r.status_code, 400, params)
            self.assertEqual(r.json()['error']['code'], 'validation_error')

    # -- response shape ------------------------------------------------------

    def test_response_shape(self):
        body = self.get(self.admin1, active='all').json()
        self.assertEqual((body['error'], body['status']), (None, 'success'))
        by_id = {o['id']: o for o in body['data']}
        o1 = by_id[self.o1.pk]
        self.assertEqual(set(o1), {'id', 'order_type', 'restaurant_id', 'table', 'waiter_id',
                                   'total_price', 'created_at', 'is_active', 'status_summary',
                                   'items'})
        self.assertEqual(o1['table'], {'id': self.t1.pk, 'table_number': 'T1'})
        self.assertEqual(o1['waiter_id'], 'staff1')
        self.assertEqual(o1['total_price'], '18.50')
        self.assertEqual(o1['status_summary'], {'Pickup': 2, 'Processed': 0, 'Complete': 0})
        self.assertEqual(set(o1['items'][0]), {'id', 'menu_item_id', 'name', 'quantity',
                                               'price', 'kitchen_status'})
        o3 = by_id[self.o3.pk]
        self.assertIsNone(o3['table'])
        self.assertFalse(o3['is_active'])
        self.assertEqual(o3['status_summary'], {'Pickup': 0, 'Processed': 0, 'Complete': 1})

    def test_empty_list(self):
        Order.objects.all().delete()
        r = self.get(self.admin1)
        self.assertEqual((r.status_code, r.json()['data']), (200, []))

    def test_number_of_queries_does_not_grow_with_the_number_of_orders(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.get(self.admin1, active='all').status_code, 200)
            return len(ctx)
        before = count()
        for _ in range(5):
            self.place(self.admin1, 'Dine-In', self.t1, [(self.pasta, 1), (self.tea, 1)])
        self.assertEqual(count(), before)

    # -- authentication --------------------------------------------------------

    def test_requires_authentication(self):
        r = client_for(None).get(ORDERS_URL)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error']['code'], 'not_authenticated')

    def test_invalid_tokens_are_rejected(self):
        client = client_for(None)
        client.credentials(HTTP_AUTHORIZATION='Bearer not.a.jwt')
        self.assertEqual(client.get(ORDERS_URL).status_code, 401)

        expired = AccessToken.for_user(self.admin1)
        expired.set_exp(from_time=timezone.now() - timedelta(hours=2),
                        lifetime=timedelta(minutes=1))
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {expired}')
        self.assertEqual(client.get(ORDERS_URL).status_code, 401)

        refresh = RefreshToken.for_user(self.admin1)          # wrong token type
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh}')
        self.assertEqual(client.get(ORDERS_URL).status_code, 401)

        client.credentials(HTTP_AUTHORIZATION='Token abc')    # wrong scheme
        self.assertEqual(client.get(ORDERS_URL).status_code, 401)

    def test_deactivated_user_is_rejected(self):
        client = client_for(self.admin1)
        self.admin1.is_active = False
        self.admin1.save()
        self.assertEqual(client.get(ORDERS_URL).status_code, 401)

    def test_method_not_allowed(self):
        client = client_for(self.admin1)
        self.assertEqual(client.delete(ORDERS_URL).status_code, 405)
        self.assertEqual(client.put(ORDERS_URL, {}, format='json').status_code, 405)


# --------------------------------------------------------------------------
# Database constraints (checked directly, independent of the API)
# --------------------------------------------------------------------------

class OrderConstraintTests(OrderFixtureMixin, APITestCase):
    def make(self, **overrides):
        fields = dict(id='ORD-TESTTEST', restaurant=self.r1, waiter=self.admin1,
                      order_type='Parcel', total_price=Decimal('1.00'))
        fields.update(overrides)
        return Order.objects.create(**fields)

    def assertRejected(self, func):
        with self.assertRaises(IntegrityError), transaction.atomic():
            func()

    def test_dine_in_needs_a_table_and_parcel_must_not_have_one(self):
        self.assertRejected(lambda: self.make(order_type='Dine-In', table=None))
        self.assertRejected(lambda: self.make(order_type='Parcel', table=self.t1))
        self.make(order_type='Dine-In', table=self.t1)

    def test_total_cannot_be_negative(self):
        self.assertRejected(lambda: self.make(total_price=Decimal('-0.01')))

    def test_item_quantity_price_and_uniqueness(self):
        order = self.make()
        self.assertRejected(lambda: OrderItem.objects.create(
            order=order, menu_item=self.tea, quantity=0, price=Decimal('1')))
        self.assertRejected(lambda: OrderItem.objects.create(
            order=order, menu_item=self.tea, quantity=1, price=Decimal('-1')))
        OrderItem.objects.create(order=order, menu_item=self.tea, quantity=1, price=Decimal('1'))
        self.assertRejected(lambda: OrderItem.objects.create(
            order=order, menu_item=self.tea, quantity=2, price=Decimal('1')))

    def test_items_default_to_pickup(self):
        order = self.make()
        item = OrderItem.objects.create(
            order=order, menu_item=self.tea, quantity=1, price=Decimal('1'))
        self.assertEqual(item.kitchen_status, 'Pickup')

    def test_ordered_tables_and_dishes_are_protected_from_deletion(self):
        self.place(self.staff1, 'Dine-In', self.t1, [(self.tea, 1)])
        with self.assertRaises(Exception), transaction.atomic():
            self.t1.delete()
        with self.assertRaises(Exception), transaction.atomic():
            self.tea.delete()

    def test_table_api_reports_conflict_for_a_table_with_orders(self):
        self.place(self.staff1, 'Dine-In', self.t1, [(self.tea, 1)])
        r = client_for(self.admin1).delete(f'/api/v1/tables/{self.t1.pk}')
        self.assertEqual(r.status_code, 409)
        self.assertTrue(Table.objects.filter(pk=self.t1.pk).exists())

    def test_menu_api_reports_conflict_for_a_dish_with_orders(self):
        self.place(self.staff1, 'Dine-In', self.t1, [(self.tea, 1)])
        r = client_for(self.admin1).delete(f'/api/v1/menu/items/{self.tea.pk}')
        self.assertEqual(r.status_code, 409)
        self.assertTrue(MenuItem.objects.filter(pk=self.tea.pk).exists())


class OrderSchemaTests(OrderFixtureMixin, APITestCase):
    def test_openapi_schema_documents_the_new_routes(self):
        r = client_for(None).get('/api/schema/?format=json')
        self.assertEqual(r.status_code, 200)
        paths = r.json()['paths']
        self.assertEqual(set(paths['/api/v1/orders']), {'get', 'post'})
        self.assertIn('patch', paths['/api/v1/orders/{order_id}/items/{item_id}'])
