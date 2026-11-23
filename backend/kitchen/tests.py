from decimal import Decimal

from rest_framework.test import APITestCase

from orders.models import OrderItem
from orders.testutils import ORDERS_URL, OrderFixtureMixin, client_for, item_url


class KitchenItemStatusTests(OrderFixtureMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.order = cls.place(cls.staff1, 'Dine-In', cls.t1, [(cls.pasta, 2), (cls.tea, 1)])
        cls.pasta_line, cls.tea_line = list(cls.order.items.order_by('id'))
        cls.other_order = cls.place(cls.staff1b, 'Dine-In', cls.t2, [(cls.pasta, 1)])
        cls.other_line = cls.other_order.items.get()
        cls.foreign_order = cls.place(cls.staff2, 'Dine-In', cls.t3, [(cls.other_dish, 1)])
        cls.foreign_line = cls.foreign_order.items.get()

    def patch(self, user, order, line, payload):
        order_id = getattr(order, 'pk', order)
        line_id = getattr(line, 'pk', line)
        return client_for(user).patch(item_url(order_id, line_id), payload, format='json')

    def status_of(self, line):
        return OrderItem.objects.get(pk=line.pk).kitchen_status

    # -- happy path ------------------------------------------------------------

    def test_kitchen_moves_an_item_through_the_documented_states(self):
        for new in ('Processed', 'Complete'):
            r = self.patch(self.kitchen1, self.order, self.pasta_line, {'kitchen_status': new})
            self.assertEqual(r.status_code, 200, new)
            self.assertEqual(self.status_of(self.pasta_line), new)
        body = r.json()
        self.assertEqual((body['error'], body['status']), (None, 'success'))
        order = body['data']
        self.assertEqual(order['id'], self.order.pk)
        states = {i['id']: i['kitchen_status'] for i in order['items']}
        self.assertEqual(states, {self.pasta_line.pk: 'Complete', self.tea_line.pk: 'Pickup'})
        self.assertEqual(order['status_summary'], {'Pickup': 1, 'Processed': 0, 'Complete': 1})
        self.assertTrue(order['is_active'])

    def test_any_of_the_three_values_may_be_selected(self):
        # the KDS shows a dropdown, so going back is allowed too
        for new in ('Complete', 'Pickup', 'Processed', 'Pickup', 'Complete'):
            r = self.patch(self.kitchen1, self.order, self.pasta_line, {'kitchen_status': new})
            self.assertEqual(r.status_code, 200, new)
            self.assertEqual(self.status_of(self.pasta_line), new)

    def test_same_status_again_is_accepted(self):
        r = self.patch(self.kitchen1, self.order, self.pasta_line, {'kitchen_status': 'Pickup'})
        self.assertEqual(r.status_code, 200)

    def test_only_the_chosen_item_changes(self):
        self.patch(self.kitchen1, self.order, self.pasta_line, {'kitchen_status': 'Processed'})
        self.assertEqual(self.status_of(self.tea_line), 'Pickup')
        self.assertEqual(self.status_of(self.other_line), 'Pickup')

    def test_order_leaves_the_queue_when_every_item_is_complete(self):
        for line in (self.pasta_line, self.tea_line):
            self.patch(self.kitchen1, self.order, line, {'kitchen_status': 'Complete'})
        listed = client_for(self.kitchen1).get(ORDERS_URL).json()['data']
        self.assertNotIn(self.order.pk, [o['id'] for o in listed])
        admin_view = client_for(self.admin1).get(ORDERS_URL, {'active': 'false'}).json()['data']
        done = next(o for o in admin_view if o['id'] == self.order.pk)
        self.assertFalse(done['is_active'])

    def test_other_fields_in_the_body_are_ignored(self):
        r = self.patch(self.kitchen1, self.order, self.pasta_line, {
            'kitchen_status': 'Processed', 'quantity': 99, 'price': '0.01',
            'menu_item': self.tea.pk, 'order': self.other_order.pk})
        self.assertEqual(r.status_code, 200)
        line = OrderItem.objects.get(pk=self.pasta_line.pk)
        self.assertEqual((line.quantity, line.price, line.menu_item_id, line.order_id),
                         (2, Decimal('12.50'), self.pasta.pk, self.order.pk))
        self.order.refresh_from_db()
        self.assertEqual(self.order.total_price, Decimal('28.00'))

    # -- validation ------------------------------------------------------------

    def test_invalid_status_values_are_rejected(self):
        for bad in ('Cooking', 'complete', 'PICKUP', '', None, 1, ['Pickup']):
            r = self.patch(self.kitchen1, self.order, self.pasta_line, {'kitchen_status': bad})
            self.assertEqual(r.status_code, 400, repr(bad))
            self.assertEqual(r.json()['error']['code'], 'validation_error')
        self.assertEqual(self.status_of(self.pasta_line), 'Pickup')

    def test_missing_status_is_rejected(self):
        for payload in ({}, {'status': 'Complete'}):
            r = self.patch(self.kitchen1, self.order, self.pasta_line, payload)
            self.assertEqual(r.status_code, 400)
            self.assertIn('kitchen_status', r.json()['error']['details'])

    # -- order / item ownership ---------------------------------------------------

    def test_item_must_belong_to_the_order_in_the_url(self):
        r = self.patch(self.kitchen1, self.order, self.other_line, {'kitchen_status': 'Complete'})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.status_of(self.other_line), 'Pickup')

    def test_unknown_order_or_item_is_404(self):
        self.assertEqual(self.patch(self.kitchen1, 'ORD-NOPENOPE', self.pasta_line,
                                    {'kitchen_status': 'Complete'}).status_code, 404)
        self.assertEqual(self.patch(self.kitchen1, self.order, 999999,
                                    {'kitchen_status': 'Complete'}).status_code, 404)

    def test_non_numeric_item_id_does_not_match_a_route(self):
        r = client_for(self.kitchen1).patch(
            f'/api/v1/orders/{self.order.pk}/items/abc', {'kitchen_status': 'Complete'},
            format='json')
        self.assertEqual(r.status_code, 404)

    def test_other_restaurants_cannot_touch_the_order(self):
        r = self.patch(self.kitchen2, self.order, self.pasta_line, {'kitchen_status': 'Complete'})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.status_of(self.pasta_line), 'Pickup')
        r = self.patch(self.kitchen1, self.foreign_order, self.foreign_line,
                       {'kitchen_status': 'Complete'})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.status_of(self.foreign_line), 'Pickup')

    def test_ids_of_two_restaurants_cannot_be_mixed(self):
        r = self.patch(self.kitchen1, self.order, self.foreign_line, {'kitchen_status': 'Complete'})
        self.assertEqual(r.status_code, 404)
        r = self.patch(self.kitchen2, self.foreign_order, self.pasta_line,
                       {'kitchen_status': 'Complete'})
        self.assertEqual(r.status_code, 404)

    # -- roles ---------------------------------------------------------------------

    def test_only_kitchen_may_update_item_status(self):
        for user in (self.admin1, self.staff1, self.platform):
            r = self.patch(user, self.order, self.pasta_line, {'kitchen_status': 'Complete'})
            self.assertEqual(r.status_code, 403, user.id)
            self.assertEqual(r.json()['error']['code'], 'permission_denied')
        self.assertEqual(self.status_of(self.pasta_line), 'Pickup')

    def test_admin_has_view_only_kds_access(self):
        listed = client_for(self.admin1).get(ORDERS_URL)
        self.assertEqual(listed.status_code, 200)
        r = self.patch(self.admin1, self.order, self.pasta_line, {'kitchen_status': 'Processed'})
        self.assertEqual(r.status_code, 403)

    def test_platform_has_no_kds_access(self):
        r = self.patch(self.platform, self.foreign_order, self.foreign_line,
                       {'kitchen_status': 'Processed'})
        self.assertEqual(r.status_code, 403)

    # -- authentication --------------------------------------------------------------

    def test_requires_authentication(self):
        r = client_for(None).patch(item_url(self.order.pk, self.pasta_line.pk),
                                   {'kitchen_status': 'Complete'}, format='json')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error']['code'], 'not_authenticated')

    def test_invalid_token_is_rejected(self):
        client = client_for(None)
        client.credentials(HTTP_AUTHORIZATION='Bearer not.a.jwt')
        r = client.patch(item_url(self.order.pk, self.pasta_line.pk),
                         {'kitchen_status': 'Complete'}, format='json')
        self.assertEqual(r.status_code, 401)

    def test_other_methods_are_not_allowed(self):
        url = item_url(self.order.pk, self.pasta_line.pk)
        client = client_for(self.kitchen1)
        for method in ('get', 'put', 'post', 'delete'):
            self.assertEqual(getattr(client, method)(url).status_code, 405, method)
        self.assertEqual(OrderItem.objects.filter(pk=self.pasta_line.pk).count(), 1)

    # -- KDS content -------------------------------------------------------------------

    def test_kds_queue_shows_what_the_documentation_requires(self):
        data = client_for(self.kitchen1).get(ORDERS_URL).json()['data']
        order = next(o for o in data if o['id'] == self.order.pk)
        self.assertTrue(order['created_at'])                      # open-timer start
        self.assertEqual(order['table'], {'id': self.t1.pk, 'table_number': 'T1'})
        self.assertEqual(order['waiter_id'], 'staff1')
        names = {i['name']: (i['quantity'], i['kitchen_status']) for i in order['items']}
        self.assertEqual(names, {'Pasta': (2, 'Pickup'), 'Tea': (1, 'Pickup')})
