from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from analytics.models import ScoreEvent
from analytics.services import QUICK_PICK_WINDOW, ScoreService, quick_pick_bonus_applies
from kitchen.models import StaffCall
from orders.testutils import OrderFixtureMixin, client_for, go_online

CALLS = '/api/v1/calls/'
CUSTOMER = '/api/v1/calls/customer/'


def pick_url(call):
    return f'/api/v1/calls/{call.pk}/pick/'


def resolve_url(call):
    return f'/api/v1/calls/{call.pk}/resolve/'


class CallFixtureMixin(OrderFixtureMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # a second kitchen user, to prove kitchens only see their own calls
        from accounts.models import User
        cls.kitchen1b = User.objects.create_user(
            'kitchen1b', None, role='Kitchen', restaurant=cls.r1)

    def setUp(self):
        cache.clear()  # the public route is rate limited per IP; start every test clean
        go_online(self.staff1, self.staff1b, self.staff2)

    # helpers -----------------------------------------------------------------
    def customer_call(self, table=None):
        r = APIClient().post(CUSTOMER, {'token': str((table or self.t1).qr_token)},
                             format='json')
        return StaffCall.objects.get(pk=r.json()['data']['id'])

    def kitchen_call(self, waiter, **body):
        body.setdefault('table', self.t1.pk)
        r = client_for(self.kitchen1).post(CALLS, {'waiter': waiter.pk, **body}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        return StaffCall.objects.get(pk=r.json()['data']['id'])

    def patch(self, user, url, body=None):
        return client_for(user).patch(url, body or {}, format='json')

    def ledger(self, **filters):
        return ScoreEvent.objects.filter(**filters)

    def age(self, call, seconds):
        """Pretend the call was triggered `seconds` ago."""
        StaffCall.objects.filter(pk=call.pk).update(
            triggered_at=timezone.now() - timedelta(seconds=seconds))


# --------------------------------------------------------------------------
# Customer QR calls (public)
# --------------------------------------------------------------------------

class CustomerCallTests(CallFixtureMixin, APITestCase):
    def post(self, body, client=None):
        return (client or APIClient()).post(CUSTOMER, body, format='json')

    def test_guest_creates_a_pending_call_without_logging_in(self):
        r = self.post({'token': str(self.t1.qr_token)})
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual((body['error'], body['status']), (None, 'success'))
        # only public values: no staff or internal ids
        self.assertEqual(set(body['data']), {'id', 'status', 'table_number', 'triggered_at'})
        self.assertEqual((body['data']['status'], body['data']['table_number']), ('PENDING', 'T1'))
        call = StaffCall.objects.get()
        self.assertEqual((call.restaurant_id, call.table_id, call.source, call.status),
                         (self.r1.pk, self.t1.pk, 'Customer', 'PENDING'))
        self.assertIsNone(call.created_by)
        self.assertIsNone(call.target_waiter)
        self.assertIsNone(call.assigned_waiter)

    def test_a_bad_authorization_header_does_not_break_the_public_route(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer not-a-real-token')
        self.assertEqual(self.post({'token': str(self.t1.qr_token)}, client).status_code, 201)

    def test_qr_link_values_may_be_sent_when_they_match(self):
        r = self.post({'token': str(self.t1.qr_token), 'restaurant_id': self.r1.pk,
                       'table': self.t1.pk})
        self.assertEqual(r.status_code, 201)

    def test_pressing_again_returns_the_open_call_instead_of_a_second_one(self):
        first = self.post({'token': str(self.t1.qr_token)})
        again = self.post({'token': str(self.t1.qr_token)})
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(first.json()['data']['id'], again.json()['data']['id'])
        self.assertEqual(StaffCall.objects.count(), 1)
        # still true once a waiter is on the way
        call = StaffCall.objects.get()
        self.patch(self.staff1, pick_url(call))
        self.assertEqual(self.post({'token': str(self.t1.qr_token)}).status_code, 200)
        self.assertEqual(StaffCall.objects.count(), 1)

    def test_a_new_call_is_possible_once_the_previous_one_is_resolved(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        self.patch(self.staff1, resolve_url(call))
        self.assertEqual(self.post({'token': str(self.t1.qr_token)}).status_code, 201)
        self.assertEqual(StaffCall.objects.count(), 2)

    def test_each_table_has_its_own_call(self):
        self.post({'token': str(self.t1.qr_token)})
        self.post({'token': str(self.t2.qr_token)})
        self.assertEqual(StaffCall.objects.count(), 2)

    def test_database_allows_only_one_open_customer_call_per_table(self):
        self.customer_call()
        with self.assertRaises(IntegrityError), transaction.atomic():
            StaffCall.objects.create(restaurant=self.r1, table=self.t1, source='Customer')

    # -- another restaurant's table can never be targeted ---------------------------

    def test_unknown_and_malformed_tokens_are_the_same_404(self):
        replies = [self.post({'token': t}) for t in (
            '00000000-0000-0000-0000-000000000000', 'not-a-uuid', '12345')]
        for r in replies:
            self.assertEqual(r.status_code, 404)
        self.assertEqual(len({r.content for r in replies}), 1)
        self.assertEqual(StaffCall.objects.count(), 0)

    def test_restaurant_or_table_that_do_not_match_the_token_look_like_an_unknown_token(self):
        unknown = self.post({'token': '00000000-0000-0000-0000-000000000000'})
        for extra in ({'restaurant_id': self.r2.pk},      # t1's token, restaurant two
                      {'table': self.t3.pk},              # t1's token, a restaurant-two table
                      {'restaurant_id': self.r2.pk, 'table': self.t3.pk},
                      {'table': self.t2.pk}):             # same restaurant, wrong table
            with self.subTest(extra=extra):
                r = self.post({'token': str(self.t1.qr_token), **extra})
                self.assertEqual(r.status_code, 404)
                self.assertEqual(r.content, unknown.content)
        self.assertEqual(StaffCall.objects.count(), 0)

    def test_a_call_always_belongs_to_the_restaurant_of_the_token(self):
        self.post({'token': str(self.t3.qr_token)})
        call = StaffCall.objects.get()
        self.assertEqual((call.restaurant_id, call.table_id), (self.r2.pk, self.t3.pk))

    def test_token_is_required(self):
        r = self.post({})
        self.assertEqual(r.status_code, 400)
        self.assertIn('token', r.json()['error']['details'])

    def test_only_post_is_allowed(self):
        self.assertEqual(APIClient().get(CUSTOMER).status_code, 405)

    def test_the_public_route_is_rate_limited(self):
        codes = [self.post({'token': str(self.t1.qr_token)}).status_code for _ in range(31)]
        self.assertEqual(codes[:30], [201] + [200] * 29)
        self.assertEqual(codes[30], 429)


# --------------------------------------------------------------------------
# Kitchen calls
# --------------------------------------------------------------------------

class KitchenCallTests(CallFixtureMixin, APITestCase):
    def post(self, user, body):
        return client_for(user).post(CALLS, body, format='json')

    def test_kitchen_calls_an_online_waiter_about_an_order(self):
        order = self.place(self.staff1, 'Dine-In', self.t1, [(self.tea, 1)])
        r = self.post(self.kitchen1, {'waiter': 'staff1', 'order': order.pk})
        self.assertEqual(r.status_code, 201)
        data = r.json()['data']
        self.assertEqual((data['source'], data['status']), ('Kitchen', 'PENDING'))
        self.assertEqual((data['target_waiter_id'], data['created_by_id']), ('staff1', 'kitchen1'))
        self.assertEqual(data['order_id'], order.pk)
        self.assertEqual(data['table'], {'id': self.t1.pk, 'table_number': 'T1'})
        self.assertIsNone(data['assigned_waiter_id'])
        self.assertIsNone(data['picked_at'])

    def test_kitchen_can_call_about_a_table_alone(self):
        r = self.post(self.kitchen1, {'waiter': 'staff1', 'table': self.t2.pk})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['data']['table']['table_number'], 'T2')

    def test_order_or_table_is_required(self):
        r = self.post(self.kitchen1, {'waiter': 'staff1'})
        self.assertEqual(r.status_code, 400)
        r = self.post(self.kitchen1, {})
        self.assertEqual(r.status_code, 400)
        self.assertIn('waiter', r.json()['error']['details'])
        self.assertEqual(StaffCall.objects.count(), 0)

    def test_only_online_waiters_of_the_own_restaurant_can_be_called(self):
        from accounts.models import User
        User.objects.filter(pk='staff1b').update(is_online=False)           # logged out
        stale = timezone.now() - timedelta(hours=1)
        self.kitchen_call(self.staff1)                                       # sanity: works
        User.objects.create_user('staff1c', None, role='Staff', restaurant=self.r1)
        User.objects.filter(pk='staff1c').update(is_online=True, last_seen=stale)  # timed out
        messages = set()
        for waiter in ('staff1b',      # logged out
                       'staff1c',      # timed out
                       'staff2',       # online, but another restaurant
                       'admin1',       # not a waiter
                       'kitchen1b',    # not a waiter
                       'no-such-user'):
            with self.subTest(waiter=waiter):
                r = self.post(self.kitchen1, {'waiter': waiter, 'table': self.t1.pk})
                self.assertEqual(r.status_code, 400)
                messages.add(str(r.json()['error']['details']['waiter']))
        self.assertEqual(len(messages), 1)   # nothing leaks about who exists elsewhere
        self.assertEqual(StaffCall.objects.count(), 1)

    def test_deactivated_waiter_cannot_be_called(self):
        from accounts.models import User
        User.objects.filter(pk='staff1').update(is_active=False)
        self.assertEqual(self.post(self.kitchen1, {'waiter': 'staff1', 'table': self.t1.pk})
                         .status_code, 400)

    def test_order_and_table_must_belong_to_the_own_restaurant(self):
        other_order = self.place(self.staff2, 'Dine-In', self.t3, [(self.other_dish, 1)])
        for body in ({'order': other_order.pk}, {'table': self.t3.pk}, {'order': 'ORD-NOPE'}):
            with self.subTest(body=body):
                r = self.post(self.kitchen1, {'waiter': 'staff1', **body})
                self.assertEqual(r.status_code, 400)
        self.assertEqual(StaffCall.objects.count(), 0)

    def test_table_must_match_the_order(self):
        order = self.place(self.staff1, 'Dine-In', self.t1, [(self.tea, 1)])
        r = self.post(self.kitchen1, {'waiter': 'staff1', 'order': order.pk, 'table': self.t2.pk})
        self.assertEqual(r.status_code, 400)

    def test_only_kitchen_may_create(self):
        body = {'waiter': 'staff1', 'table': self.t1.pk}
        for user in (self.admin1, self.staff1, self.platform):
            self.assertEqual(self.post(user, body).status_code, 403, user.id)
        self.assertEqual(client_for(None).post(CALLS, body, format='json').status_code, 401)
        self.assertEqual(StaffCall.objects.count(), 0)

    def test_the_client_cannot_set_the_status_or_assignee(self):
        r = self.post(self.kitchen1, {'waiter': 'staff1', 'table': self.t1.pk,
                                      'status': 'RESOLVED', 'assigned_waiter': 'staff1b',
                                      'source': 'Customer', 'created_by': 'admin1'})
        self.assertEqual(r.status_code, 201)
        call = StaffCall.objects.get()
        self.assertEqual((call.status, call.source, call.created_by_id, call.assigned_waiter),
                         ('PENDING', 'Kitchen', 'kitchen1', None))


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------

class CallListTests(CallFixtureMixin, APITestCase):
    def get(self, user, **params):
        return client_for(user).get(CALLS, params)

    def ids(self, response):
        return [c['id'] for c in response.json()['data']]

    def make(self):
        self.c_cust = self.customer_call(self.t1)                        # open to any waiter
        self.c_for1 = self.kitchen_call(self.staff1)                     # addressed to staff1
        self.c_for1b = self.kitchen_call(self.staff1b, table=self.t2.pk)  # addressed to staff1b
        self.c_other = self.customer_call(self.t3)                       # restaurant two

    def test_staff_sees_the_alerts_they_can_act_on_oldest_first(self):
        self.make()
        r = self.get(self.staff1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.ids(r), [self.c_cust.pk, self.c_for1.pk])
        self.assertEqual(self.ids(self.get(self.staff1b)), [self.c_cust.pk, self.c_for1b.pk])
        # another restaurant's waiter only sees their own restaurant's call
        self.assertEqual(self.ids(self.get(self.staff2)), [self.c_other.pk])

    def test_response_shape(self):
        self.make()
        item = self.get(self.staff1).json()['data'][0]
        self.assertEqual(set(item), {'id', 'source', 'status', 'table', 'order_id',
                                     'created_by_id', 'target_waiter_id', 'assigned_waiter_id',
                                     'closed_by_id', 'triggered_at', 'picked_at', 'resolved_at',
                                     'cancelled_at'})
        body = self.get(self.staff1).json()
        self.assertEqual((body['error'], body['status']), (None, 'success'))

    def test_a_picked_call_leaves_the_pending_queue_for_everyone(self):
        self.make()
        self.patch(self.staff1, pick_url(self.c_cust))
        self.assertEqual(self.ids(self.get(self.staff1)), [self.c_for1.pk])
        self.assertEqual(self.ids(self.get(self.staff1b)), [self.c_for1b.pk])
        # ...and shows up, for the waiter who took it only, under PICKED
        self.assertEqual(self.ids(self.get(self.staff1, status='PICKED')), [self.c_cust.pk])
        self.assertEqual(self.ids(self.get(self.staff1b, status='PICKED')), [])

    def test_resolved_calls_move_to_history(self):
        self.make()
        self.patch(self.staff1, pick_url(self.c_cust))
        self.patch(self.staff1, resolve_url(self.c_cust))
        self.assertEqual(self.ids(self.get(self.staff1, status='RESOLVED')), [self.c_cust.pk])
        self.assertEqual(self.ids(self.get(self.staff1b, status='RESOLVED')), [])
        self.assertNotIn(self.c_cust.pk, self.ids(self.get(self.staff1)))
        self.assertEqual(set(self.ids(self.get(self.staff1, status='all'))),
                         {self.c_cust.pk, self.c_for1.pk})

    def test_admin_sees_every_call_of_the_restaurant_only(self):
        self.make()
        self.assertEqual(set(self.ids(self.get(self.admin1, status='all'))),
                         {self.c_cust.pk, self.c_for1.pk, self.c_for1b.pk})
        self.assertEqual(self.ids(self.get(self.admin2, status='all')), [self.c_other.pk])

    def test_kitchen_sees_only_the_calls_it_made(self):
        self.make()
        self.assertEqual(set(self.ids(self.get(self.kitchen1))), {self.c_for1.pk, self.c_for1b.pk})
        self.assertEqual(self.ids(self.get(self.kitchen1b)), [])

    def test_platform_has_no_access_and_anonymous_is_rejected(self):
        self.assertEqual(self.get(self.platform).status_code, 403)
        self.assertEqual(client_for(None).get(CALLS).status_code, 401)

    def test_invalid_status_filter_is_rejected(self):
        r = self.get(self.staff1, status='DONE')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error']['code'], 'validation_error')


# --------------------------------------------------------------------------
# Pick and resolve
# --------------------------------------------------------------------------

class CallPickTests(CallFixtureMixin, APITestCase):
    def test_waiter_picks_a_pending_call(self):
        call = self.customer_call()
        r = self.patch(self.staff1, pick_url(call))
        self.assertEqual(r.status_code, 200)
        data = r.json()['data']
        self.assertEqual((data['status'], data['assigned_waiter_id']), ('PICKED', 'staff1'))
        self.assertTrue(data['picked_at'])
        self.assertIsNone(data['resolved_at'])
        call.refresh_from_db()
        self.assertEqual((call.status, call.assigned_waiter_id), ('PICKED', 'staff1'))

    def test_the_waiter_is_always_the_caller_whatever_the_body_says(self):
        call = self.customer_call()
        r = self.patch(self.staff1, pick_url(call), {
            'assigned_waiter': 'staff1b', 'assigned_waiter_id': 'staff1b',
            'status': 'RESOLVED', 'waiter': 'staff1b', 'picked_at': '2020-01-01T00:00:00Z'})
        self.assertEqual(r.status_code, 200)
        call.refresh_from_db()
        self.assertEqual((call.status, call.assigned_waiter_id), ('PICKED', 'staff1'))
        self.assertGreater(call.picked_at.year, 2020)
        self.assertIsNone(call.resolved_at)

    def test_a_second_waiter_cannot_take_a_picked_call(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        r = self.patch(self.staff1b, pick_url(call))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()['error']['code'], 'conflict')
        call.refresh_from_db()
        self.assertEqual(call.assigned_waiter_id, 'staff1')

    def test_picking_twice_is_an_invalid_transition(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        self.assertEqual(self.patch(self.staff1, pick_url(call)).status_code, 409)

    def test_a_resolved_call_cannot_be_picked(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        self.patch(self.staff1, resolve_url(call))
        self.assertEqual(self.patch(self.staff1b, pick_url(call)).status_code, 409)

    def test_a_kitchen_call_can_only_be_picked_by_the_waiter_it_names(self):
        call = self.kitchen_call(self.staff1)
        r = self.patch(self.staff1b, pick_url(call))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error']['code'], 'permission_denied')
        call.refresh_from_db()
        self.assertEqual((call.status, call.assigned_waiter), ('PENDING', None))
        self.assertEqual(self.patch(self.staff1, pick_url(call)).status_code, 200)

    def test_calls_of_another_restaurant_are_invisible(self):
        call = self.customer_call(self.t3)                # restaurant two
        for user in (self.staff1, self.staff1b):
            self.assertEqual(self.patch(user, pick_url(call)).status_code, 404)
        self.assertEqual(self.patch(self.staff2, pick_url(call)).status_code, 200)
        self.assertEqual(self.patch(self.staff1, pick_url(StaffCall(pk=987654))).status_code, 404)

    def test_only_waiters_may_pick(self):
        call = self.customer_call()
        for user in (self.admin1, self.kitchen1, self.platform):
            self.assertEqual(self.patch(user, pick_url(call)).status_code, 403, user.id)
        self.assertEqual(client_for(None).patch(pick_url(call)).status_code, 401)
        call.refresh_from_db()
        self.assertEqual(call.status, 'PENDING')

    def test_other_methods_are_not_allowed(self):
        call = self.customer_call()
        self.assertEqual(client_for(self.staff1).post(pick_url(call)).status_code, 405)
        self.assertEqual(client_for(self.staff1).get(pick_url(call)).status_code, 405)


class CallResolveTests(CallFixtureMixin, APITestCase):
    def picked(self, waiter=None, seconds_ago=30, call=None):
        call = call or self.customer_call()
        self.age(call, seconds_ago)
        self.patch(waiter or self.staff1, pick_url(call))
        call.refresh_from_db()
        return call

    def test_the_picking_waiter_resolves_the_call(self):
        call = self.picked()
        r = self.patch(self.staff1, resolve_url(call))
        self.assertEqual(r.status_code, 200)
        data = r.json()['data']
        self.assertEqual((data['status'], data['assigned_waiter_id']), ('RESOLVED', 'staff1'))
        self.assertTrue(data['picked_at'] and data['resolved_at'])

    def test_a_call_cannot_be_resolved_before_it_is_picked(self):
        call = self.customer_call()
        r = self.patch(self.staff1, resolve_url(call))
        self.assertEqual(r.status_code, 409)
        call.refresh_from_db()
        self.assertEqual(call.status, 'PENDING')
        self.assertEqual(self.ledger().count(), 0)

    def test_another_waiter_cannot_resolve_a_call_assigned_to_someone_else(self):
        call = self.picked(self.staff1)
        r = self.patch(self.staff1b, resolve_url(call))
        self.assertEqual(r.status_code, 403)
        call.refresh_from_db()
        self.assertEqual(call.status, 'PICKED')
        self.assertEqual(self.ledger().count(), 0)

    def test_calls_of_another_restaurant_are_invisible(self):
        call = self.picked()
        self.assertEqual(self.patch(self.staff2, resolve_url(call)).status_code, 404)

    def test_only_waiters_and_the_admin_may_resolve(self):
        # (the Admin's own rules are tested in test_call_admin.py)
        call = self.picked()
        for user in (self.kitchen1, self.platform):
            self.assertEqual(self.patch(user, resolve_url(call)).status_code, 403, user.id)
        self.assertEqual(client_for(None).patch(resolve_url(call)).status_code, 401)

    def test_resolving_twice_is_rejected_and_scores_only_once(self):
        call = self.picked(seconds_ago=30)
        self.assertEqual(self.patch(self.staff1, resolve_url(call)).status_code, 200)
        first = list(self.ledger().values_list('kind', 'points'))
        again = self.patch(self.staff1, resolve_url(call))
        self.assertEqual(again.status_code, 409)
        self.assertEqual(list(self.ledger().values_list('kind', 'points')), first)

    # -- scoring ------------------------------------------------------------------

    def test_a_resolved_call_earns_5_points(self):
        call = self.picked(seconds_ago=600)               # picked 10 minutes after triggering
        self.patch(self.staff1, resolve_url(call))
        rows = list(self.ledger())
        self.assertEqual([(r.kind, r.points, r.waiter_id, r.restaurant_id, r.call_id)
                          for r in rows],
                         [('CALL_RESOLVED', 5, 'staff1', self.r1.pk, call.pk)])

    def test_picking_within_two_minutes_adds_a_5_point_bonus(self):
        call = self.picked(seconds_ago=119)
        self.patch(self.staff1, resolve_url(call))
        self.assertEqual(sorted(self.ledger().values_list('kind', 'points')),
                         [('CALL_RESOLVED', 5), ('QUICK_PICK_BONUS', 5)])
        self.assertEqual(ScoreService.scorecard_of(self.staff1)['total_score'], 10)

    def test_picking_later_than_two_minutes_has_no_bonus(self):
        call = self.picked(seconds_ago=121)
        self.patch(self.staff1, resolve_url(call))
        self.assertEqual(list(self.ledger().values_list('kind', flat=True)), ['CALL_RESOLVED'])

    def test_the_bonus_depends_on_picked_at_not_on_when_it_is_resolved(self):
        call = self.picked(seconds_ago=10)                  # picked quickly
        later = timezone.now() + timedelta(minutes=45)      # ...but resolved much later
        with mock.patch('kitchen.call_services.timezone.now', return_value=later):
            self.assertEqual(self.patch(self.staff1, resolve_url(call)).status_code, 200)
        self.assertEqual(sorted(self.ledger().values_list('kind', flat=True)),
                         ['CALL_RESOLVED', 'QUICK_PICK_BONUS'])

    def test_the_bonus_depends_on_triggered_at(self):
        slow = self.picked(self.staff1, seconds_ago=300, call=self.customer_call(self.t1))
        fast = self.picked(self.staff1, seconds_ago=5, call=self.customer_call(self.t2))
        self.patch(self.staff1, resolve_url(slow))
        self.patch(self.staff1, resolve_url(fast))
        self.assertFalse(self.ledger(kind='QUICK_PICK_BONUS', call=slow).exists())
        self.assertTrue(self.ledger(kind='QUICK_PICK_BONUS', call=fast).exists())

    def test_bonus_window_boundaries(self):
        t0 = timezone.now()
        self.assertTrue(quick_pick_bonus_applies(t0, t0))
        self.assertTrue(quick_pick_bonus_applies(t0, t0 + QUICK_PICK_WINDOW))      # exactly 2:00
        self.assertFalse(quick_pick_bonus_applies(t0, t0 + QUICK_PICK_WINDOW
                                                  + timedelta(microseconds=1)))
        self.assertFalse(quick_pick_bonus_applies(t0, t0 - timedelta(seconds=1)))  # impossible
        self.assertEqual(QUICK_PICK_WINDOW, timedelta(minutes=2))

    def test_kitchen_calls_score_exactly_like_customer_calls(self):
        call = self.picked(call=self.kitchen_call(self.staff1), seconds_ago=20)
        self.patch(self.staff1, resolve_url(call))
        self.assertEqual(ScoreService.scorecard_of(self.staff1)['total_score'], 10)

    def test_score_goes_to_the_waiter_who_picked_the_call(self):
        call = self.picked(self.staff1b, seconds_ago=20)
        self.patch(self.staff1b, resolve_url(call))
        self.assertEqual({r.waiter_id for r in self.ledger()}, {'staff1b'})
        self.assertEqual(ScoreService.scorecard_of(self.staff1)['total_score'], 0)

    def test_a_call_that_is_only_picked_earns_nothing_yet(self):
        self.picked()
        self.assertEqual(self.ledger().count(), 0)

    def test_crediting_the_same_call_again_changes_nothing(self):
        call = self.picked(seconds_ago=10)
        self.patch(self.staff1, resolve_url(call))
        call.refresh_from_db()
        before = self.ledger().count()
        ScoreService.credit_resolved_call(call)
        ScoreService.credit_resolved_call(call)
        self.assertEqual((before, self.ledger().count()), (2, 2))

    def test_the_database_itself_refuses_a_duplicate_credit(self):
        call = self.picked(seconds_ago=10)
        self.patch(self.staff1, resolve_url(call))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScoreEvent.objects.create(restaurant=self.r1, waiter=self.staff1,
                                      kind='CALL_RESOLVED', points=5, call=call)

    def test_status_and_timestamps_must_agree_in_the_database(self):
        for fields in (
            {'status': 'PICKED'},                                         # no assignee / time
            {'status': 'RESOLVED', 'assigned_waiter': self.staff1,
             'picked_at': timezone.now()},                                # no resolved_at
            {'status': 'PENDING', 'picked_at': timezone.now()},           # pending but picked
        ):
            with self.subTest(fields=fields), self.assertRaises(IntegrityError), \
                    transaction.atomic():
                StaffCall.objects.create(restaurant=self.r1, table=self.t1,
                                         source='Customer', **fields)

    def test_a_customer_call_cannot_have_a_sender_or_target(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StaffCall.objects.create(restaurant=self.r1, table=self.t1, source='Customer',
                                     created_by=self.kitchen1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StaffCall.objects.create(restaurant=self.r1, table=self.t1, source='Kitchen')


class UnauthenticatedCallTests(CallFixtureMixin, APITestCase):
    def test_every_authenticated_call_route_needs_a_token(self):
        call = self.customer_call()
        client = APIClient()
        self.assertEqual(client.get(CALLS).status_code, 401)
        self.assertEqual(client.post(CALLS, {}, format='json').status_code, 401)
        self.assertEqual(client.patch(pick_url(call)).status_code, 401)
        self.assertEqual(client.patch(resolve_url(call)).status_code, 401)
