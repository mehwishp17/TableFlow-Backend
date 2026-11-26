from datetime import timedelta
from unittest import mock

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APITestCase

from analytics.models import ScoreEvent
from analytics.services import ScoreService
from kitchen.models import StaffCall
from orders.models import Order
from orders.services import OrderService
from orders.testutils import ORDERS_URL, OrderFixtureMixin, client_for, go_online

ME = '/api/v1/staff/me/scorecard/'
BOARD = '/api/v1/staff/scorecards/'


def card_url(user_id):
    return f'/api/v1/staff/{user_id}/scorecard/'


def dine_in(table, *lines):
    return {'order_type': 'Dine-In', 'table': table.pk,
            'items': [{'menu_item': item.pk, 'quantity': qty} for item, qty in lines]}


class DineInScoringTests(OrderFixtureMixin, APITestCase):
    """+10 for each Dine-In order, credited once, at the single moment it is created."""

    def post(self, user, body):
        return client_for(user).post(ORDERS_URL, body, format='json')

    def test_a_staff_dine_in_order_earns_10_points_once(self):
        r = self.post(self.staff1, dine_in(self.t1, (self.pasta, 1)))
        self.assertEqual(r.status_code, 201)
        rows = ScoreEvent.objects.all()
        self.assertEqual([(e.kind, e.points, e.waiter_id, e.restaurant_id, e.order_id)
                          for e in rows],
                         [('DINE_IN_ORDER', 10, 'staff1', self.r1.pk, r.json()['data']['id'])])

    def test_each_order_scores_separately(self):
        for _ in range(3):
            self.post(self.staff1, dine_in(self.t1, (self.tea, 1)))
        self.assertEqual(ScoreService.scorecard_of(self.staff1)['total_score'], 30)

    def test_parcel_orders_earn_nothing(self):
        r = self.post(self.staff1, {'order_type': 'Parcel',
                                    'items': [{'menu_item': self.tea.pk, 'quantity': 1}]})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(ScoreEvent.objects.count(), 0)

    def test_orders_created_by_an_admin_earn_nothing(self):
        self.assertEqual(self.post(self.admin1, dine_in(self.t1, (self.tea, 1))).status_code, 201)
        self.assertEqual(ScoreEvent.objects.count(), 0)

    def test_a_rejected_order_earns_nothing(self):
        r = self.post(self.staff1, dine_in(self.t1, (self.soup, 1)))      # unavailable dish
        self.assertEqual(r.status_code, 400)
        self.assertEqual((Order.objects.count(), ScoreEvent.objects.count()), (0, 0))

    def test_listing_orders_or_updating_items_never_scores(self):
        self.place(self.staff1, 'Dine-In', self.t1, [(self.tea, 1)])
        self.assertEqual(ScoreEvent.objects.count(), 1)
        client_for(self.staff1).get(ORDERS_URL)
        order = Order.objects.get()
        client_for(self.kitchen1).patch(
            f'/api/v1/orders/{order.pk}/items/{order.items.get().pk}',
            {'kitchen_status': 'Complete'}, format='json')
        self.assertEqual(ScoreEvent.objects.count(), 1)

    def test_the_same_order_cannot_be_credited_twice(self):
        order = self.place(self.staff1, 'Dine-In', self.t1, [(self.tea, 1)])
        self.assertFalse(ScoreService.credit_dine_in_order(order))
        self.assertFalse(ScoreService.credit_dine_in_order(order))
        self.assertEqual(ScoreEvent.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScoreEvent.objects.create(restaurant=self.r1, waiter=self.staff1,
                                      kind='DINE_IN_ORDER', points=10, order=order)

    def test_the_order_and_its_credit_are_saved_together(self):
        with mock.patch('orders.services.ScoreService.credit_dine_in_order',
                        side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.place(self.staff1, 'Dine-In', self.t1, [(self.tea, 1)])
        self.assertEqual((Order.objects.count(), ScoreEvent.objects.count()), (0, 0))

    def test_ledger_rows_must_match_their_kind(self):
        order = self.place(self.admin1, 'Parcel', None, [(self.tea, 1)])
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScoreEvent.objects.create(restaurant=self.r1, waiter=self.staff1,
                                      kind='CALL_RESOLVED', points=5, order=order)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScoreEvent.objects.create(restaurant=self.r1, waiter=self.staff1,
                                      kind='DINE_IN_ORDER', points=10)


class ScorecardContentTests(OrderFixtureMixin, APITestCase):
    def setUp(self):
        go_online(self.staff1, self.staff1b, self.staff2)

    def resolved_call(self, waiter, table, picked_after_seconds):
        call = StaffCall.objects.create(restaurant_id=waiter.restaurant_id, table=table,
                                        source='Customer')
        triggered = timezone.now() - timedelta(seconds=picked_after_seconds)
        StaffCall.objects.filter(pk=call.pk).update(triggered_at=triggered)
        client_for(waiter).patch(f'/api/v1/calls/{call.pk}/pick/')
        client_for(waiter).patch(f'/api/v1/calls/{call.pk}/resolve/')
        return call

    def build(self):
        # staff1: 2 Dine-In orders, 1 Parcel (no points), 2 resolved calls (1 quick, 1 slow)
        self.place(self.staff1, 'Dine-In', self.t1, [(self.tea, 1)])
        self.place(self.staff1, 'Dine-In', self.t2, [(self.tea, 1)])
        self.place(self.staff1, 'Parcel', None, [(self.tea, 1)])
        self.resolved_call(self.staff1, self.t1, 30)      # +5 +5
        self.resolved_call(self.staff1, self.t2, 600)     # +5
        # staff1b: one Dine-In order only
        self.place(self.staff1b, 'Dine-In', self.t1, [(self.tea, 1)])

    def test_scorecard_fields_and_numbers(self):
        self.build()
        r = client_for(self.staff1).get(ME)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body['error'], body['status']), (None, 'success'))
        self.assertEqual(body['data'], {
            'waiter_id': 'staff1',
            'is_active': True,
            'total_score': 2 * 10 + 2 * 5 + 5,
            'dine_in_orders_served': 2,
            'calls_resolved': 2,
            'bonus_points': 5,
            'breakdown': {
                'dine_in_orders': {'count': 2, 'points': 20},
                'calls_resolved': {'count': 2, 'points': 10},
                'quick_pick_bonus': {'count': 1, 'points': 5},
            },
        })

    def test_a_new_waiter_has_an_empty_scorecard(self):
        self.assertEqual(client_for(self.staff1).get(ME).json()['data'], {
            'waiter_id': 'staff1', 'is_active': True, 'total_score': 0,
            'dine_in_orders_served': 0,
            'calls_resolved': 0, 'bonus_points': 0,
            'breakdown': {'dine_in_orders': {'count': 0, 'points': 0},
                          'calls_resolved': {'count': 0, 'points': 0},
                          'quick_pick_bonus': {'count': 0, 'points': 0}},
        })

    def test_total_equals_the_sum_of_the_ledger(self):
        self.build()
        from django.db.models import Sum
        ledger = ScoreEvent.objects.filter(waiter=self.staff1).aggregate(t=Sum('points'))['t']
        self.assertEqual(client_for(self.staff1).get(ME).json()['data']['total_score'], ledger)

    def test_staff_personal_and_admin_views_use_the_same_numbers(self):
        self.build()
        mine = client_for(self.staff1).get(ME).json()['data']
        by_id_as_staff = client_for(self.staff1).get(card_url('staff1')).json()['data']
        by_id_as_admin = client_for(self.admin1).get(card_url('staff1')).json()['data']
        board = {c['waiter_id']: c for c in client_for(self.admin1).get(BOARD).json()['data']}
        self.assertEqual(mine, by_id_as_staff)
        self.assertEqual(mine, by_id_as_admin)
        self.assertEqual(mine, board['staff1'])

    # -- who may see what ----------------------------------------------------------

    def test_my_scorecard_is_for_staff_only(self):
        for user in (self.admin1, self.kitchen1, self.platform):
            self.assertEqual(client_for(user).get(ME).status_code, 403, user.id)
        self.assertEqual(client_for(None).get(ME).status_code, 401)

    def test_staff_cannot_read_another_waiters_scorecard(self):
        self.build()
        for user_id in ('staff1b', 'staff2', 'admin1', 'nobody'):
            r = client_for(self.staff1).get(card_url(user_id))
            self.assertEqual(r.status_code, 403, user_id)
            self.assertNotIn('total_score', r.content.decode())

    def test_admin_reads_any_waiter_of_their_restaurant_only(self):
        self.build()
        self.assertEqual(client_for(self.admin1).get(card_url('staff1b')).status_code, 200)
        for user_id in ('staff2',            # another restaurant's waiter
                        'kitchen1',          # not a waiter
                        'admin1', 'platform1', 'nobody', 'online'):
            self.assertEqual(client_for(self.admin1).get(card_url(user_id)).status_code, 404,
                             user_id)

    def test_kitchen_and_platform_have_no_access(self):
        for user in (self.kitchen1, self.platform):
            self.assertEqual(client_for(user).get(card_url('staff1')).status_code, 403)
            self.assertEqual(client_for(user).get(BOARD).status_code, 403)
        self.assertEqual(client_for(None).get(card_url('staff1')).status_code, 401)

    # -- leaderboard / tenant isolation -----------------------------------------------

    def test_admin_leaderboard_lists_every_waiter_best_first(self):
        self.build()
        board = client_for(self.admin1).get(BOARD).json()['data']
        self.assertEqual([(c['waiter_id'], c['total_score']) for c in board],
                         [('staff1', 35), ('staff1b', 10)])

    def test_leaderboard_includes_waiters_without_points_and_breaks_ties_by_id(self):
        from accounts.models import User
        User.objects.create_user('aaa', None, role='Staff', restaurant=self.r1)
        board = client_for(self.admin1).get(BOARD).json()['data']
        self.assertEqual([(c['waiter_id'], c['total_score']) for c in board],
                         [('aaa', 0), ('staff1', 0), ('staff1b', 0)])

    def test_leaderboard_never_mixes_restaurants(self):
        self.build()
        self.place(self.staff2, 'Dine-In', self.t3, [(self.other_dish, 1)])
        one = client_for(self.admin1).get(BOARD).json()['data']
        two = client_for(self.admin2).get(BOARD).json()['data']
        self.assertEqual({c['waiter_id'] for c in one}, {'staff1', 'staff1b'})
        self.assertEqual([(c['waiter_id'], c['total_score']) for c in two], [('staff2', 10)])

    def test_only_the_admin_has_the_leaderboard(self):
        self.assertEqual(client_for(self.staff1).get(BOARD).status_code, 403)

    def test_leaderboard_uses_a_constant_number_of_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from accounts.models import User

        def count():
            with CaptureQueriesContext(connection) as ctx:
                ScoreService.leaderboard(self.r1.pk)
            return len(ctx)
        count()
        before = count()
        for i in range(5):
            User.objects.create_user(f'extra{i}', None, role='Staff', restaurant=self.r1)
        self.assertEqual(count(), before)

    def test_a_deactivated_waiters_history_is_kept(self):
        self.build()
        client_for(self.admin1).delete('/api/v1/staff/staff1/')
        self.assertEqual(client_for(self.admin1).get(card_url('staff1')).json()['data']
                         ['total_score'], 35)
