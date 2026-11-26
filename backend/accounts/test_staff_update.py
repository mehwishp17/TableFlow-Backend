"""An Admin edit and a heartbeat/logout can happen at the same moment. The edit must only
write the columns it changes."""
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from accounts.services import StaffService
from orders.testutils import OrderFixtureMixin

GOOD_PASSWORD = 'Tr4nsparent-Pasta!'


class StaffUpdateDoesNotClobberPresenceTests(OrderFixtureMixin, APITestCase):
    def stale_copy_then_presence_changes(self):
        stale = User.objects.get(pk='staff1b')          # what the Admin's request loaded
        newer = timezone.now()
        User.objects.filter(pk='staff1b').update(is_online=True, last_seen=newer)
        return stale, newer

    def test_changing_the_role_keeps_the_newer_presence_values(self):
        stale, newer = self.stale_copy_then_presence_changes()
        self.assertIsNone(stale.last_seen)                       # the stale copy is behind
        StaffService.update(stale, role='Kitchen')
        fresh = User.objects.get(pk='staff1b')
        self.assertEqual((fresh.role, fresh.is_online, fresh.last_seen), ('Kitchen', True, newer))

    def test_resetting_the_password_keeps_the_newer_presence_values(self):
        stale, newer = self.stale_copy_then_presence_changes()
        StaffService.update(stale, password=GOOD_PASSWORD)
        fresh = User.objects.get(pk='staff1b')
        self.assertTrue(fresh.check_password(GOOD_PASSWORD))
        self.assertEqual((fresh.is_online, fresh.last_seen), (True, newer))

    def test_reactivating_keeps_the_newer_presence_values(self):
        User.objects.filter(pk='staff1b').update(is_active=False)
        stale, newer = self.stale_copy_then_presence_changes()
        StaffService.update(stale, is_active=True)
        fresh = User.objects.get(pk='staff1b')
        self.assertEqual((fresh.is_active, fresh.is_online, fresh.last_seen), (True, True, newer))

    def test_deactivating_keeps_last_seen_and_clears_only_the_online_flag(self):
        stale, newer = self.stale_copy_then_presence_changes()
        StaffService.deactivate(stale)
        fresh = User.objects.get(pk='staff1b')
        self.assertEqual((fresh.is_active, fresh.is_online, fresh.last_seen), (False, False, newer))

    def test_only_the_changed_columns_are_written(self):
        user = User.objects.get(pk='staff1b')
        with CaptureQueriesContext(connection) as ctx:
            StaffService.update(user, role='Kitchen')
        updates = [q['sql'] for q in ctx if q['sql'].startswith('UPDATE "accounts_user"')]
        self.assertEqual(len(updates), 1)
        assignments = updates[0].split(' WHERE ')[0]
        self.assertIn('"role"', assignments)
        for column in ('last_seen', 'is_online', 'password', 'is_active', 'restaurant_id'):
            self.assertNotIn(column, assignments)

    def test_an_update_that_changes_nothing_writes_nothing(self):
        user = User.objects.get(pk='staff1b')
        with CaptureQueriesContext(connection) as ctx:
            StaffService.update(user, role='Staff', is_active=True)
        self.assertEqual([q for q in ctx if q['sql'].startswith('UPDATE')], [])
