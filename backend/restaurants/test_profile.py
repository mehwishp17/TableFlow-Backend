from rest_framework.test import APITestCase

from accounts.models import User
from orders.testutils import OrderFixtureMixin, client_for
from restaurants.models import Restaurant
from tables.models import Table

URL = '/api/v1/restaurant/profile/'


class RestaurantProfileTests(OrderFixtureMixin, APITestCase):
    def get(self, user):
        return client_for(user).get(URL)

    def patch(self, user, body):
        return client_for(user).patch(URL, body, format='json')

    def test_admin_reads_the_own_restaurant(self):
        Restaurant.objects.filter(pk=self.r1.pk).update(
            address='1 Main St', subscription_plan='Pro', logo_url='https://x.test/logo.png')
        r = self.get(self.admin1)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body['error'], body['status']), (None, 'success'))
        data = body['data']
        self.assertEqual(set(data), {'id', 'name', 'address', 'logo_url', 'subscription_plan',
                                     'created_at', 'counters'})
        self.assertEqual((data['id'], data['name'], data['address'], data['subscription_plan'],
                          data['logo_url']),
                         (self.r1.pk, 'Bistro One', '1 Main St', 'Pro', 'https://x.test/logo.png'))
        self.assertEqual(self.get(self.admin2).json()['data']['name'], 'Bistro Two')

    def test_counters_are_counted_from_the_database(self):
        # restaurant one: admin1, staff1, staff1b, kitchen1 and two tables
        self.assertEqual(self.get(self.admin1).json()['data']['counters'],
                         {'users': 4, 'staff': 2, 'kitchen_terminals': 1, 'tables': 2})
        self.assertEqual(self.get(self.admin2).json()['data']['counters'],
                         {'users': 3, 'staff': 1, 'kitchen_terminals': 1, 'tables': 1})
        User.objects.create_user('k9', None, role='Kitchen', restaurant=self.r1)
        User.objects.create_user('s9', None, role='Staff', restaurant=self.r1)
        Table.objects.create(restaurant=self.r1, table_number='T9', capacity=2)
        self.assertEqual(self.get(self.admin1).json()['data']['counters'],
                         {'users': 6, 'staff': 3, 'kitchen_terminals': 2, 'tables': 3})

    def test_deactivated_accounts_do_not_count(self):
        client_for(self.admin1).delete('/api/v1/staff/staff1b/')
        self.assertEqual(self.get(self.admin1).json()['data']['counters'],
                         {'users': 3, 'staff': 1, 'kitchen_terminals': 1, 'tables': 2})

    def test_update_permitted_fields(self):
        r = self.patch(self.admin1, {'name': '  New Bistro ', 'address': '9 High St',
                                     'logo_url': 'https://cdn.test/logo.png'})
        self.assertEqual(r.status_code, 200)
        data = r.json()['data']
        self.assertEqual((data['name'], data['address'], data['logo_url']),
                         ('New Bistro', '9 High St', 'https://cdn.test/logo.png'))
        self.r1.refresh_from_db()
        self.assertEqual((self.r1.name, self.r1.address), ('New Bistro', '9 High St'))

    def test_partial_update_changes_only_what_is_sent(self):
        self.patch(self.admin1, {'address': 'Somewhere'})
        self.r1.refresh_from_db()
        self.assertEqual((self.r1.name, self.r1.address), ('Bistro One', 'Somewhere'))

    def test_the_logo_can_be_cleared(self):
        self.patch(self.admin1, {'logo_url': 'https://cdn.test/logo.png'})
        r = self.patch(self.admin1, {'logo_url': ''})
        self.assertEqual((r.status_code, r.json()['data']['logo_url']), (200, ''))

    def test_plan_counters_and_ids_cannot_be_edited(self):
        Restaurant.objects.filter(pk=self.r1.pk).update(subscription_plan='Basic')
        before = self.r1.created_at
        r = self.patch(self.admin1, {
            'subscription_plan': 'Enterprise', 'id': 999, 'created_at': '2000-01-01T00:00:00Z',
            'counters': {'tables': 500, 'users': 500}, 'name': 'Renamed'})
        self.assertEqual(r.status_code, 200)
        self.r1.refresh_from_db()
        self.assertEqual((self.r1.subscription_plan, self.r1.created_at, self.r1.name),
                         ('Basic', before, 'Renamed'))
        self.assertEqual(r.json()['data']['counters']['tables'], 2)
        self.assertEqual(r.json()['data']['id'], self.r1.pk)

    def test_validation(self):
        for body in ({'name': ''}, {'name': '   '}, {'name': 'x' * 151},
                     {'logo_url': 'ftp://x.test/logo.png'}, {'logo_url': 'javascript:alert(1)'},
                     {'logo_url': 'not a url'}, {'logo_url': 'https://' + 'a' * 200 + '.test'}):
            with self.subTest(body=body):
                r = self.patch(self.admin1, body)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.json()['error']['code'], 'validation_error')
        self.r1.refresh_from_db()
        self.assertEqual(self.r1.name, 'Bistro One')

    def test_an_admin_only_ever_touches_their_own_restaurant(self):
        self.patch(self.admin1, {'name': 'Changed', 'id': self.r2.pk})
        self.r2.refresh_from_db()
        self.assertEqual(self.r2.name, 'Bistro Two')

    def test_only_the_admin_has_access(self):
        for user in (self.staff1, self.kitchen1, self.platform):
            with self.subTest(user=user.id):
                self.assertEqual(self.get(user).status_code, 403)
                self.assertEqual(self.patch(user, {'name': 'Hacked'}).status_code, 403)
        for method in ('get', 'patch'):
            self.assertEqual(getattr(client_for(None), method)(URL).status_code, 401)
        self.r1.refresh_from_db()
        self.assertEqual(self.r1.name, 'Bistro One')

    def test_other_methods_are_not_allowed(self):
        client = client_for(self.admin1)
        self.assertEqual(client.post(URL, {}, format='json').status_code, 405)
        self.assertEqual(client.delete(URL).status_code, 405)
