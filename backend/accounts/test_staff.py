import os
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from accounts.models import User
from accounts.services import PresenceService, StaffService
from orders.models import Order
from orders.testutils import OrderFixtureMixin, client_for, go_online

STAFF = '/api/v1/staff/'
ONLINE = '/api/v1/staff/online/'
LOGIN = '/api/v1/auth/login'
LOGOUT = '/api/v1/auth/logout'
GOOD_PASSWORD = 'Tr4nsparent-Pasta!'


def detail(user_id):
    return f'/api/v1/staff/{user_id}/'


class StaffCrudTests(OrderFixtureMixin, APITestCase):
    def post(self, user, body):
        return client_for(user).post(STAFF, body, format='json')

    def new(self, **overrides):
        body = {'id': 'waiter01', 'password': GOOD_PASSWORD, 'role': 'Staff'}
        body.update(overrides)
        return body

    # -- create -----------------------------------------------------------------

    def test_admin_creates_a_staff_account_in_their_own_restaurant(self):
        r = self.post(self.admin1, self.new())
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual((body['error'], body['status']), (None, 'success'))
        self.assertEqual(set(body['data']), {'id', 'role', 'is_active', 'is_online', 'last_seen'})
        self.assertEqual((body['data']['id'], body['data']['role'], body['data']['is_active'],
                          body['data']['is_online']), ('waiter01', 'Staff', True, False))
        user = User.objects.get(pk='waiter01')
        self.assertEqual(user.restaurant_id, self.r1.pk)
        self.assertTrue(user.check_password(GOOD_PASSWORD))
        self.assertNotEqual(user.password, GOOD_PASSWORD)            # stored hashed

    def test_the_new_account_can_log_in(self):
        self.post(self.admin1, self.new())
        r = APIClient().post(LOGIN, {'id': 'waiter01', 'password': GOOD_PASSWORD,
                                     'role': 'Staff'}, format='json')
        self.assertEqual(r.status_code, 200)

    def test_role_defaults_to_staff_and_kitchen_is_allowed(self):
        body = self.new(id='w2')
        del body['role']
        self.assertEqual(self.post(self.admin1, body).json()['data']['role'], 'Staff')
        r = self.post(self.admin1, self.new(id='k2', role='Kitchen'))
        self.assertEqual((r.status_code, r.json()['data']['role']), (201, 'Kitchen'))

    def test_admin_cannot_create_admin_or_platform_accounts(self):
        for role in ('Admin', 'Platform', 'Nonsense'):
            with self.subTest(role=role):
                r = self.post(self.admin1, self.new(id=f'x-{role}', role=role))
                self.assertEqual(r.status_code, 400)
                self.assertIn('role', r.json()['error']['details'])
        self.assertFalse(User.objects.filter(pk__startswith='x-').exists())

    def test_the_restaurant_cannot_be_chosen_by_the_client(self):
        r = self.post(self.admin1, self.new(restaurant=self.r2.pk, restaurant_id=self.r2.pk))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(User.objects.get(pk='waiter01').restaurant_id, self.r1.pk)

    def test_validation(self):
        cases = {
            'duplicate id': ({'id': 'staff1'}, 'id'),
            'duplicate id of another restaurant': ({'id': 'staff2'}, 'id'),
            'spaces in id': ({'id': 'two words'}, 'id'),
            'bad first character': ({'id': '-abc'}, 'id'),
            'too long id': ({'id': 'a' * 51}, 'id'),
            'reserved id online': ({'id': 'online'}, 'id'),
            'reserved id ME': ({'id': 'ME'}, 'id'),
            'reserved id scorecards': ({'id': 'scorecards'}, 'id'),
            'short password': ({'password': 'a1!'}, 'password'),
            'numeric password': ({'password': '1234567890123'}, 'password'),
            'common password': ({'password': 'password123'}, 'password'),
        }
        for name, (override, field) in cases.items():
            with self.subTest(name):
                r = self.post(self.admin1, self.new(**override))
                self.assertEqual(r.status_code, 400)
                self.assertIn(field, r.json()['error']['details'])
        self.assertEqual(User.objects.filter(restaurant=self.r1).count(), 4)   # nothing created

    def test_a_concurrent_create_cannot_overwrite_the_existing_account(self):
        def another_request_wins(*args, **kwargs):
            # Runs after the "id is free" check and before our insert.
            User.objects.create_user(
                'waiter01', 'Other-Pasta-9921!', role='Kitchen', restaurant=self.r2)

        with mock.patch.object(StaffService, '_checked_password',
                               side_effect=another_request_wins):
            r = self.post(self.admin1, self.new())
        self.assertEqual(r.status_code, 400)
        self.assertIn('id', r.json()['error']['details'])
        winner = User.objects.get(pk='waiter01')
        self.assertEqual((winner.restaurant_id, winner.role), (self.r2.pk, 'Kitchen'))
        self.assertTrue(winner.check_password('Other-Pasta-9921!'))

    def test_create_user_never_updates_an_existing_account(self):
        before = User.objects.get(pk='staff2')
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(
                'staff2', GOOD_PASSWORD, role='Kitchen', restaurant=self.r1)
        after = User.objects.get(pk='staff2')
        self.assertEqual((after.restaurant_id, after.role, after.password),
                         (before.restaurant_id, before.role, before.password))

    def test_the_createsuperuser_command_still_works(self):
        with mock.patch.dict(os.environ, {'DJANGO_SUPERUSER_PASSWORD': GOOD_PASSWORD}):
            call_command('createsuperuser', id='cmdroot', interactive=False, verbosity=0)
        user = User.objects.get(pk='cmdroot')
        self.assertEqual((user.role, user.is_superuser), ('Platform', True))
        self.assertTrue(user.check_password(GOOD_PASSWORD))

    def test_missing_fields(self):
        r = self.post(self.admin1, {})
        self.assertEqual(r.status_code, 400)
        self.assertTrue({'id', 'password'} <= set(r.json()['error']['details']))

    def test_only_the_admin_may_manage_staff(self):
        for user in (self.staff1, self.kitchen1, self.platform):
            with self.subTest(user=user.id):
                self.assertEqual(self.post(user, self.new()).status_code, 403)
                self.assertEqual(client_for(user).get(STAFF).status_code, 403)
                self.assertEqual(client_for(user).get(detail('staff1b')).status_code, 403)
                self.assertEqual(client_for(user).patch(
                    detail('staff1b'), {'is_active': False}, format='json').status_code, 403)
                self.assertEqual(client_for(user).delete(detail('staff1b')).status_code, 403)
        self.assertEqual(client_for(None).get(STAFF).status_code, 401)
        self.assertTrue(User.objects.get(pk='staff1b').is_active)
        self.assertFalse(User.objects.filter(pk='waiter01').exists())

    # -- list / retrieve ----------------------------------------------------------

    def test_list_contains_only_staff_and_kitchen_of_the_own_restaurant(self):
        r = client_for(self.admin1).get(STAFF)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([u['id'] for u in r.json()['data']], ['kitchen1', 'staff1', 'staff1b'])
        self.assertEqual([u['id'] for u in client_for(self.admin2).get(STAFF).json()['data']],
                         ['kitchen2', 'staff2'])

    def test_list_filters(self):
        User.objects.filter(pk='staff1b').update(is_active=False)
        ids = lambda **p: [u['id'] for u in client_for(self.admin1).get(STAFF, p).json()['data']]  # noqa: E731
        self.assertEqual(ids(role='Staff'), ['staff1', 'staff1b'])
        self.assertEqual(ids(role='Kitchen'), ['kitchen1'])
        self.assertEqual(ids(is_active='false'), ['staff1b'])
        self.assertEqual(ids(is_active='true'), ['kitchen1', 'staff1'])

    def test_no_secret_is_ever_returned(self):
        self.post(self.admin1, self.new())
        for response in (client_for(self.admin1).get(STAFF),
                         client_for(self.admin1).get(detail('waiter01'))):
            text = response.content.decode()
            for secret in ('password', 'pbkdf2', GOOD_PASSWORD, 'is_superuser',
                           'user_permissions', 'groups'):
                self.assertNotIn(secret, text)

    def test_retrieve(self):
        r = client_for(self.admin1).get(detail('staff1'))
        self.assertEqual((r.status_code, r.json()['data']['id']), (200, 'staff1'))

    def test_other_restaurants_and_privileged_accounts_are_invisible(self):
        for user_id in ('staff2', 'kitchen2', 'admin1', 'admin2', 'platform1', 'nobody'):
            with self.subTest(user_id=user_id):
                self.assertEqual(client_for(self.admin1).get(detail(user_id)).status_code, 404)
                self.assertEqual(client_for(self.admin1).patch(
                    detail(user_id), {'is_active': False}, format='json').status_code, 404)
                self.assertEqual(client_for(self.admin1).delete(detail(user_id)).status_code, 404)
        self.assertTrue(User.objects.get(pk='staff2').is_active)
        self.assertTrue(User.objects.get(pk='admin1').is_active)

    # -- update -------------------------------------------------------------------

    def patch(self, user_id, body, admin=None):
        return client_for(admin or self.admin1).patch(detail(user_id), body, format='json')

    def test_change_role(self):
        r = self.patch('staff1b', {'role': 'Kitchen'})
        self.assertEqual((r.status_code, r.json()['data']['role']), (200, 'Kitchen'))
        self.assertEqual(self.patch('staff1b', {'role': 'Admin'}).status_code, 400)
        self.assertEqual(User.objects.get(pk='staff1b').role, 'Kitchen')

    def test_reset_password(self):
        r = self.patch('staff1b', {'password': GOOD_PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('password', r.json()['data'])
        self.assertTrue(User.objects.get(pk='staff1b').check_password(GOOD_PASSWORD))
        self.assertEqual(self.patch('staff1b', {'password': '123'}).status_code, 400)
        self.assertTrue(User.objects.get(pk='staff1b').check_password(GOOD_PASSWORD))

    def test_the_id_and_restaurant_cannot_be_changed(self):
        r = self.patch('staff1b', {'id': 'renamed', 'restaurant': self.r2.pk, 'is_active': True})
        self.assertEqual(r.status_code, 200)
        user = User.objects.get(pk='staff1b')
        self.assertEqual(user.restaurant_id, self.r1.pk)
        self.assertFalse(User.objects.filter(pk='renamed').exists())

    def test_an_empty_update_is_rejected(self):
        self.assertEqual(self.patch('staff1b', {}).status_code, 400)
        self.assertEqual(self.patch('staff1b', {'id': 'x'}).status_code, 400)

    # -- deactivate (soft delete) --------------------------------------------------------

    def test_delete_deactivates_instead_of_removing(self):
        order = self.place(self.staff1b, 'Dine-In', self.t1, [(self.tea, 1)])
        r = client_for(self.admin1).delete(detail('staff1b'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()['data']['id'], r.json()['data']['is_active']),
                         ('staff1b', False))
        user = User.objects.get(pk='staff1b')
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_online)
        # their history is untouched
        self.assertEqual(Order.objects.get(pk=order.pk).waiter_id, 'staff1b')
        # repeating is harmless
        self.assertEqual(client_for(self.admin1).delete(detail('staff1b')).status_code, 200)

    def test_a_deactivated_account_can_no_longer_use_the_api_or_log_in(self):
        user = User.objects.create_user('temp1', GOOD_PASSWORD, role='Staff',
                                        restaurant=self.r1)
        client = client_for(user)
        self.assertEqual(client.get('/api/v1/calls/').status_code, 200)
        client_for(self.admin1).delete(detail('temp1'))
        self.assertEqual(client.get('/api/v1/calls/').status_code, 401)
        r = APIClient().post(LOGIN, {'id': 'temp1', 'password': GOOD_PASSWORD, 'role': 'Staff'},
                             format='json')
        self.assertEqual(r.status_code, 401)

    def test_reactivate(self):
        client_for(self.admin1).delete(detail('staff1b'))
        r = self.patch('staff1b', {'is_active': True})
        self.assertEqual((r.status_code, r.json()['data']['is_active']), (200, True))


class OnlineStaffTests(OrderFixtureMixin, APITestCase):
    def ids(self, user):
        r = client_for(user).get(ONLINE)
        self.assertEqual(r.status_code, 200)
        return [u['id'] for u in r.json()['data']]

    def test_only_online_waiters_of_the_own_restaurant(self):
        go_online(self.staff1, self.staff2, self.kitchen1, self.admin1)
        # online Kitchen/Admin are not waiters; staff2 is another restaurant's waiter
        self.assertEqual(self.ids(self.admin1), ['staff1'])
        self.assertEqual(self.ids(self.kitchen1), ['staff1'])
        self.assertEqual(self.ids(self.admin2), ['staff2'])

    def test_response_fields(self):
        go_online(self.staff1)
        item = client_for(self.admin1).get(ONLINE).json()['data'][0]
        self.assertEqual(set(item), {'id', 'role', 'is_active', 'is_online', 'last_seen'})
        self.assertTrue(item['is_online'])

    def test_an_empty_list_is_fine(self):
        self.assertEqual(self.ids(self.admin1), [])

    def test_offline_timed_out_and_deactivated_waiters_are_excluded(self):
        go_online(self.staff1, self.staff1b)
        User.objects.filter(pk='staff1b').update(
            last_seen=timezone.now() - timedelta(seconds=10_000))
        self.assertEqual(self.ids(self.admin1), ['staff1'])
        User.objects.filter(pk='staff1').update(is_online=False)       # logged out
        self.assertEqual(self.ids(self.admin1), [])
        go_online(self.staff1)
        User.objects.filter(pk='staff1').update(is_active=False)       # deactivated
        self.assertEqual(self.ids(self.admin1), [])

    def test_only_admin_and_kitchen_may_look(self):
        for user in (self.staff1, self.platform):
            self.assertEqual(client_for(user).get(ONLINE).status_code, 403, user.id)
        self.assertEqual(client_for(None).get(ONLINE).status_code, 401)

    def test_the_timeout_is_configurable(self):
        go_online(self.staff1)
        User.objects.filter(pk='staff1').update(last_seen=timezone.now() - timedelta(seconds=60))
        with override_settings(ONLINE_TIMEOUT_SECONDS=30):
            self.assertEqual(self.ids(self.admin1), [])
        with override_settings(ONLINE_TIMEOUT_SECONDS=300):
            self.assertEqual(self.ids(self.admin1), ['staff1'])

    def test_default_timeout_is_documented_in_settings(self):
        from django.conf import settings
        self.assertEqual(settings.ONLINE_TIMEOUT_SECONDS, 120)
        self.assertEqual(settings.ACTIVITY_TOUCH_INTERVAL_SECONDS, 30)


class PresenceTests(OrderFixtureMixin, APITestCase):
    """Login, logout and the heartbeat of authenticated requests."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pw_user = User.objects.create_user(
            'waiter9', GOOD_PASSWORD, role='Staff', restaurant=cls.r1)

    def login(self, user_id='waiter9', role='Staff'):
        return APIClient().post(LOGIN, {'id': user_id, 'password': GOOD_PASSWORD,
                                        'role': role}, format='json')

    def fresh(self, user_id='waiter9'):
        return User.objects.get(pk=user_id)

    def test_login_marks_the_user_online(self):
        self.assertFalse(PresenceService.is_online(self.fresh()))
        r = self.login()
        self.assertEqual(r.status_code, 200)
        user = self.fresh()
        self.assertTrue(user.is_online)
        self.assertIsNotNone(user.last_seen)
        self.assertTrue(PresenceService.is_online(user))
        self.assertEqual([u['id'] for u in client_for(self.admin1).get(ONLINE).json()['data']],
                         ['waiter9'])

    def test_failed_logins_do_not_mark_anyone_online(self):
        APIClient().post(LOGIN, {'id': 'waiter9', 'password': 'wrong', 'role': 'Staff'},
                         format='json')
        APIClient().post(LOGIN, {'id': 'waiter9', 'password': GOOD_PASSWORD, 'role': 'Kitchen'},
                         format='json')                                   # role mismatch
        self.assertFalse(self.fresh().is_online)
        self.assertIsNone(self.fresh().last_seen)

    def test_the_login_response_is_unchanged(self):
        data = self.login().json()['data']
        self.assertEqual(set(data), {'id', 'role', 'access', 'refresh'})

    def test_logout_marks_the_user_offline_immediately(self):
        token = self.login().json()['data']['access']
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        r = client.post(LOGOUT)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'data': {'logged_out': True}, 'error': None,
                                    'status': 'success'})
        self.assertFalse(self.fresh().is_online)
        self.assertEqual(client_for(self.admin1).get(ONLINE).json()['data'], [])

    def test_a_stale_token_after_logout_does_not_bring_the_user_back_online(self):
        token = self.login().json()['data']['access']
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.post(LOGOUT)
        User.objects.filter(pk='waiter9').update(last_seen=timezone.now() - timedelta(minutes=5))
        self.assertEqual(client.get('/api/v1/calls/').status_code, 200)   # token still valid
        self.assertFalse(self.fresh().is_online)
        self.assertFalse(PresenceService.is_online(self.fresh()))

    def test_logout_needs_authentication(self):
        self.assertEqual(APIClient().post(LOGOUT).status_code, 401)

    def test_every_role_can_log_out(self):
        for user in (self.admin1, self.staff1, self.kitchen1, self.platform):
            go_online(user)
            self.assertEqual(client_for(user).post(LOGOUT).status_code, 200, user.id)
            self.assertFalse(User.objects.get(pk=user.pk).is_online)

    def test_authenticated_requests_refresh_last_seen(self):
        go_online(self.staff1)
        old = timezone.now() - timedelta(seconds=90)
        User.objects.filter(pk='staff1').update(last_seen=old)
        client_for(self.staff1).get('/api/v1/calls/')
        self.assertGreater(User.objects.get(pk='staff1').last_seen, old + timedelta(seconds=60))

    def test_the_heartbeat_is_written_at_most_once_per_interval(self):
        go_online(self.staff1)
        recent = timezone.now() - timedelta(seconds=5)
        User.objects.filter(pk='staff1').update(last_seen=recent)
        client_for(self.staff1).get('/api/v1/calls/')
        self.assertEqual(User.objects.get(pk='staff1').last_seen, recent)   # not rewritten

    def test_the_heartbeat_keeps_a_polling_waiter_online(self):
        go_online(self.staff1)
        User.objects.filter(pk='staff1').update(last_seen=timezone.now() - timedelta(seconds=110))
        self.assertTrue(PresenceService.is_online(User.objects.get(pk='staff1')))
        client_for(self.staff1).get('/api/v1/calls/')
        User.objects.filter(pk='staff1').update(is_online=True)
        self.assertTrue(PresenceService.is_online(User.objects.get(pk='staff1')))

    def test_anonymous_requests_do_not_touch_anyone(self):
        before = list(User.objects.values_list('pk', 'last_seen'))
        APIClient().get('/api/v1/calls/')
        self.assertEqual(list(User.objects.values_list('pk', 'last_seen')), before)
