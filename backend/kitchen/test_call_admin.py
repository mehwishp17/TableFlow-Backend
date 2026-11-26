"""Admin call management, and what happens to a waiter's calls when they are deactivated."""
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from analytics.models import ScoreEvent
from kitchen.models import StaffCall
from kitchen.test_calls import CALLS, CallFixtureMixin, pick_url, resolve_url
from orders.testutils import client_for, go_online


def cancel_url(call):
    return f'/api/v1/calls/{call.pk}/cancel/'


def staff_url(user_id):
    return f'/api/v1/staff/{user_id}/'


class AdminCallCancelTests(CallFixtureMixin, APITestCase):
    def test_admin_cancels_a_pending_call(self):
        call = self.customer_call()
        r = self.patch(self.admin1, cancel_url(call))
        self.assertEqual(r.status_code, 200)
        data = r.json()['data']
        self.assertEqual((data['status'], data['closed_by_id']), ('CANCELLED', 'admin1'))
        self.assertTrue(data['cancelled_at'])
        self.assertIsNone(data['resolved_at'])
        call.refresh_from_db()
        self.assertEqual((call.status, call.closed_by_id), ('CANCELLED', 'admin1'))
        self.assertEqual(ScoreEvent.objects.count(), 0)

    def test_admin_cancels_a_call_that_a_waiter_picked_and_never_finished(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        self.assertEqual(self.patch(self.admin1, cancel_url(call)).status_code, 200)
        # the waiter can no longer resolve it, and gets no credit
        r = self.patch(self.staff1, resolve_url(call))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(ScoreEvent.objects.count(), 0)
        # ...but still finds it in their own history
        ids = [c['id'] for c in client_for(self.staff1).get(CALLS, {'status': 'CANCELLED'})
               .json()['data']]
        self.assertEqual(ids, [call.pk])
        self.assertEqual(client_for(self.staff1b).get(CALLS, {'status': 'CANCELLED'})
                         .json()['data'], [])

    def test_a_cancelled_call_frees_the_table_for_a_new_call(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))                   # stuck: the waiter vanished
        again = self.customer_call()
        self.assertEqual(again.pk, call.pk)                       # still the same open call
        self.patch(self.admin1, cancel_url(call))
        fresh = self.customer_call()
        self.assertNotEqual(fresh.pk, call.pk)
        self.assertEqual(fresh.status, 'PENDING')

    def test_a_cancelled_call_leaves_the_pending_queue(self):
        call = self.customer_call()
        self.patch(self.admin1, cancel_url(call))
        self.assertEqual(client_for(self.staff1).get(CALLS).json()['data'], [])
        self.assertEqual(self.patch(self.staff1, pick_url(call)).status_code, 409)

    def test_admin_can_cancel_a_kitchen_call_too(self):
        call = self.kitchen_call(self.staff1)
        self.assertEqual(self.patch(self.admin1, cancel_url(call)).status_code, 200)

    def test_finished_calls_cannot_be_cancelled(self):
        resolved = self.customer_call(self.t1)
        self.patch(self.staff1, pick_url(resolved))
        self.patch(self.staff1, resolve_url(resolved))
        cancelled = self.customer_call(self.t2)
        self.patch(self.admin1, cancel_url(cancelled))
        for call in (resolved, cancelled):
            self.assertEqual(self.patch(self.admin1, cancel_url(call)).status_code, 409)
        resolved.refresh_from_db()
        self.assertEqual(resolved.status, 'RESOLVED')

    def test_cancelling_never_touches_scores(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        self.patch(self.admin1, cancel_url(call))
        self.assertEqual(ScoreEvent.objects.count(), 0)

    # -- isolation and permissions ------------------------------------------------------

    def test_an_admin_cannot_touch_another_restaurants_call(self):
        call = self.customer_call(self.t3)                        # restaurant two
        self.assertEqual(self.patch(self.admin1, cancel_url(call)).status_code, 404)
        self.assertEqual(self.patch(self.admin1, resolve_url(call)).status_code, 404)
        call.refresh_from_db()
        self.assertEqual(call.status, 'PENDING')
        self.assertEqual(self.patch(self.admin2, cancel_url(call)).status_code, 200)

    def test_only_the_admin_may_cancel(self):
        call = self.customer_call()
        for user in (self.staff1, self.kitchen1, self.platform):
            self.assertEqual(self.patch(user, cancel_url(call)).status_code, 403, user.id)
        self.assertEqual(client_for(None).patch(cancel_url(call)).status_code, 401)
        self.assertEqual(self.patch(self.admin1, '/api/v1/calls/987654/cancel/').status_code, 404)
        call.refresh_from_db()
        self.assertEqual(call.status, 'PENDING')

    def test_other_methods_are_not_allowed(self):
        call = self.customer_call()
        self.assertEqual(client_for(self.admin1).post(cancel_url(call)).status_code, 405)

    def test_database_requires_a_timestamp_for_a_cancelled_call(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StaffCall.objects.create(restaurant=self.r1, table=self.t1, source='Customer',
                                     status='CANCELLED')
        StaffCall.objects.create(restaurant=self.r1, table=self.t1, source='Customer',
                                 status='CANCELLED', cancelled_at=timezone.now())


class AdminCallResolveTests(CallFixtureMixin, APITestCase):
    def test_admin_closes_a_picked_call_on_the_waiters_behalf_without_score(self):
        call = self.customer_call()
        self.age(call, 10)                                        # would have earned a bonus
        self.patch(self.staff1, pick_url(call))
        r = self.patch(self.admin1, resolve_url(call))
        self.assertEqual(r.status_code, 200)
        data = r.json()['data']
        self.assertEqual((data['status'], data['closed_by_id'], data['assigned_waiter_id']),
                         ('RESOLVED', 'admin1', 'staff1'))
        self.assertTrue(data['resolved_at'])
        self.assertEqual(ScoreEvent.objects.count(), 0)            # the waiter did not resolve it
        self.assertEqual(self.patch(self.staff1, resolve_url(call)).status_code, 409)
        self.assertEqual(ScoreEvent.objects.count(), 0)
        self.assertEqual(self.customer_call().status, 'PENDING')   # the table can call again

    def test_a_pending_call_cannot_be_resolved_by_the_admin(self):
        call = self.customer_call()
        r = self.patch(self.admin1, resolve_url(call))
        self.assertEqual(r.status_code, 409)
        self.assertIn('cancel', r.json()['error']['message'])
        call.refresh_from_db()
        self.assertEqual(call.status, 'PENDING')

    def test_a_cancelled_or_resolved_call_cannot_be_resolved_again(self):
        cancelled = self.customer_call(self.t1)
        self.patch(self.admin1, cancel_url(cancelled))
        done = self.customer_call(self.t2)
        self.patch(self.staff1, pick_url(done))
        self.patch(self.staff1, resolve_url(done))
        before = ScoreEvent.objects.count()
        for call in (cancelled, done):
            self.assertEqual(self.patch(self.admin1, resolve_url(call)).status_code, 409)
        self.assertEqual(ScoreEvent.objects.count(), before)

    def test_a_waiters_own_resolve_still_scores_and_records_no_admin(self):
        call = self.customer_call()
        self.age(call, 10)
        self.patch(self.staff1, pick_url(call))
        self.patch(self.staff1, resolve_url(call))
        call.refresh_from_db()
        self.assertIsNone(call.closed_by)
        self.assertEqual(ScoreEvent.objects.filter(waiter=self.staff1).count(), 2)

    def test_kitchen_and_platform_still_cannot_resolve(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        for user in (self.kitchen1, self.platform):
            self.assertEqual(self.patch(user, resolve_url(call)).status_code, 403, user.id)

    def test_admin_still_cannot_pick(self):
        call = self.customer_call()
        self.assertEqual(self.patch(self.admin1, pick_url(call)).status_code, 403)

    def test_admin_lists_cancelled_calls_only_on_request(self):
        call = self.customer_call()
        self.patch(self.admin1, cancel_url(call))
        get = lambda **p: [c['id'] for c in client_for(self.admin1).get(CALLS, p).json()['data']]  # noqa: E731
        self.assertEqual(get(), [])
        self.assertEqual(get(status='CANCELLED'), [call.pk])
        self.assertEqual(get(status='all'), [call.pk])


class DeactivatedWaiterCallTests(CallFixtureMixin, APITestCase):
    def deactivate(self, user_id, admin=None):
        return client_for(admin or self.admin1).delete(staff_url(user_id))

    def test_picked_customer_calls_go_back_to_pending_when_the_waiter_is_deactivated(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        r = self.deactivate('staff1')
        self.assertEqual(r.status_code, 200)
        call.refresh_from_db()
        self.assertEqual((call.status, call.assigned_waiter, call.picked_at),
                         ('PENDING', None, None))
        self.assertEqual(ScoreEvent.objects.count(), 0)
        # another waiter sees it again, picks it, resolves it and is the one credited
        self.assertEqual([c['id'] for c in client_for(self.staff1b).get(CALLS).json()['data']],
                         [call.pk])
        self.assertEqual(self.patch(self.staff1b, pick_url(call)).status_code, 200)
        self.age(call, 5)
        self.patch(self.staff1b, resolve_url(call))
        self.assertEqual({e.waiter_id for e in ScoreEvent.objects.all()}, {'staff1b'})

    def test_the_same_happens_through_patch_is_active_false(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        r = client_for(self.admin1).patch(staff_url('staff1'), {'is_active': False},
                                          format='json')
        self.assertEqual(r.status_code, 200)
        call.refresh_from_db()
        self.assertEqual(call.status, 'PENDING')

    def test_the_released_call_can_not_be_resolved_by_the_old_waiter(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        self.deactivate('staff1')
        self.assertEqual(client_for(self.staff1).patch(resolve_url(call)).status_code, 401)

    def test_a_waiter_who_stops_being_a_waiter_also_releases_their_calls(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        r = client_for(self.admin1).patch(staff_url('staff1'), {'role': 'Kitchen'},
                                          format='json')
        self.assertEqual(r.status_code, 200)
        call.refresh_from_db()
        self.assertEqual(call.status, 'PENDING')

    def test_kitchen_calls_addressed_to_the_waiter_are_cancelled(self):
        pending = self.kitchen_call(self.staff1, table=self.t1.pk)
        picked = self.kitchen_call(self.staff1, table=self.t2.pk)
        self.patch(self.staff1, pick_url(picked))
        self.deactivate('staff1')
        for call in (pending, picked):
            call.refresh_from_db()
            self.assertEqual((call.status, call.closed_by_id), ('CANCELLED', 'admin1'))
            self.assertIsNotNone(call.cancelled_at)
        self.assertEqual(ScoreEvent.objects.count(), 0)
        # the kitchen can simply call someone else
        self.assertEqual(client_for(self.kitchen1).post(
            CALLS, {'waiter': 'staff1b', 'table': self.t1.pk}, format='json').status_code, 201)

    def test_other_waiters_and_finished_calls_are_untouched(self):
        mine_picked = self.customer_call(self.t1)
        self.patch(self.staff1, pick_url(mine_picked))
        theirs_picked = self.customer_call(self.t2)
        self.patch(self.staff1b, pick_url(theirs_picked))
        resolved = StaffCall.objects.create(
            restaurant=self.r1, table=self.t1, source='Customer', status='RESOLVED',
            assigned_waiter=self.staff1, picked_at=timezone.now(), resolved_at=timezone.now())
        kitchen_for_other = self.kitchen_call(self.staff1b)
        self.deactivate('staff1')
        for call, status in ((mine_picked, 'PENDING'), (theirs_picked, 'PICKED'),
                             (resolved, 'RESOLVED'), (kitchen_for_other, 'PENDING')):
            call.refresh_from_db()
            self.assertEqual(call.status, status)
        theirs_picked.refresh_from_db()
        self.assertEqual(theirs_picked.assigned_waiter_id, 'staff1b')

    def test_calls_of_another_restaurant_are_never_touched(self):
        other = self.customer_call(self.t3)
        self.patch(self.staff2, pick_url(other))
        self.deactivate('staff1')
        other.refresh_from_db()
        self.assertEqual((other.status, other.assigned_waiter_id), ('PICKED', 'staff2'))

    def test_another_restaurants_admin_cannot_deactivate_or_release_anything(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        self.assertEqual(self.deactivate('staff1', self.admin2).status_code, 404)
        call.refresh_from_db()
        self.assertEqual((call.status, call.assigned_waiter_id), ('PICKED', 'staff1'))
        self.assertTrue(User.objects.get(pk='staff1').is_active)

    def test_deactivating_a_kitchen_user_releases_nothing(self):
        picked = self.customer_call()
        self.patch(self.staff1, pick_url(picked))
        made = self.kitchen_call(self.staff1b)
        self.deactivate('kitchen1')
        picked.refresh_from_db()
        made.refresh_from_db()
        self.assertEqual((picked.status, made.status), ('PICKED', 'PENDING'))

    def test_deactivating_twice_is_harmless_and_reactivation_restores_nothing(self):
        call = self.customer_call()
        self.patch(self.staff1, pick_url(call))
        self.deactivate('staff1')
        self.assertEqual(self.deactivate('staff1').status_code, 200)
        client_for(self.admin1).patch(staff_url('staff1'), {'is_active': True}, format='json')
        call.refresh_from_db()
        self.assertEqual((call.status, call.assigned_waiter), ('PENDING', None))
        go_online(self.staff1)
        self.assertEqual(self.patch(self.staff1, pick_url(call)).status_code, 200)
