import uuid
from urllib.parse import parse_qs, urlparse

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from unittest import mock

from django.core import checks
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import User
from restaurants.models import Restaurant

from .models import Table
from . import services
from .apps import check_frontend_base_url
from .services import build_menu_url, render_qr_pdf
from .views import QRResolveView

PASSWORD = 'test-password-123'
TABLES = '/api/v1/tables'
PDF = '/api/v1/tables/qr-pdf'
RESOLVE = '/api/v1/qr/resolve/'


class TableTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.r1 = Restaurant.objects.create(name='Bistro One')
        cls.r2 = Restaurant.objects.create(name='Bistro Two')
        for role in ('Admin', 'Staff', 'Kitchen'):
            User.objects.create_user(f'{role.lower()}1', PASSWORD, role=role, restaurant=cls.r1)
        User.objects.create_user('admin2', PASSWORD, role='Admin', restaurant=cls.r2)
        User.objects.create_user('staff2', PASSWORD, role='Staff', restaurant=cls.r2)
        User.objects.create_user('platform1', PASSWORD, role='Platform')

        cls.t1 = Table.objects.create(restaurant=cls.r1, table_number='T1', capacity=4)
        cls.t2 = Table.objects.create(
            restaurant=cls.r1, table_number='A-2', capacity=2, table_type='VIP')
        cls.other = Table.objects.create(restaurant=cls.r2, table_number='T1', capacity=6)

    def setUp(self):
        cache.clear()  # throttle counters live in the cache

    def login_as(self, user_id):
        self.client.force_authenticate(User.objects.get(pk=user_id))

    def body(self, **overrides):
        data = {'table_number': 'B7', 'capacity': 4, 'table_type': 'Outdoor'}
        data.update(overrides)
        return data


class TableModelTests(TableTestBase):
    def test_alphanumeric_labels_valid(self):
        for i, label in enumerate(['1', '2', 'T10', 'A-2', 'vip_3', '2.1']):
            t = Table(restaurant=self.r2, table_number=label, capacity=2)
            t.full_clean(exclude=['qr_token'])  # does not raise
            t.save()

    def test_invalid_label_rejected(self):
        for label in ['', ' T1', 'T 1', '-x', 'T1!', 'a' * 21]:
            with self.assertRaises(ValidationError, msg=label):
                Table(restaurant=self.r1, table_number=label, capacity=2).full_clean(
                    exclude=['qr_token'])

    def test_duplicate_number_rejected_in_same_restaurant(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Table.objects.create(restaurant=self.r1, table_number='T1', capacity=2)

    def test_duplicate_is_case_insensitive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Table.objects.create(restaurant=self.r1, table_number='t1', capacity=2)

    def test_same_number_allowed_in_other_restaurant(self):
        self.assertEqual(self.other.table_number, self.t1.table_number)

    def test_capacity_minimum(self):
        with self.assertRaises(ValidationError):
            Table(restaurant=self.r1, table_number='Z', capacity=0).full_clean(
                exclude=['qr_token'])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Table.objects.create(restaurant=self.r1, table_number='Z', capacity=0)

    def test_choices(self):
        self.assertEqual(Table.Type.values, ['Indoor', 'Outdoor', 'VIP', 'Bar'])
        self.assertEqual(Table.Status.values, ['Available', 'Occupied', 'Reserved', 'Cleaning'])
        self.assertEqual(self.t1.table_type, 'Indoor')
        self.assertEqual(self.t1.status, 'Available')
        with self.assertRaises(ValidationError):
            Table(restaurant=self.r1, table_number='Q', capacity=1,
                  table_type='Rooftop').full_clean(exclude=['qr_token'])

    def test_qr_tokens_unique(self):
        tokens = {t.qr_token for t in Table.objects.all()}
        self.assertEqual(len(tokens), Table.objects.count())
        self.assertIsInstance(self.t1.qr_token, uuid.UUID)


class TableCRUDTests(TableTestBase):
    def test_admin_create(self):
        self.login_as('admin1')
        r = self.client.post(TABLES, self.body(), format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['status'], 'success')
        self.assertIsNone(r.data['error'])
        self.assertEqual(r.data['data']['status'], 'Available')
        self.assertEqual(Table.objects.get(pk=r.data['data']['id']).restaurant, self.r1)

    def test_create_ignores_restaurant_in_body(self):
        self.login_as('admin1')
        r = self.client.post(TABLES, self.body(restaurant=self.r2.pk), format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Table.objects.get(pk=r.data['data']['id']).restaurant, self.r1)

    def test_create_ignores_qr_token_in_body(self):
        self.login_as('admin1')
        forced = str(uuid.uuid4())
        r = self.client.post(TABLES, self.body(qr_token=forced), format='json')
        self.assertNotEqual(str(Table.objects.get(pk=r.data['data']['id']).qr_token), forced)
        self.assertNotIn('qr_token', r.data['data'])

    def test_create_validation(self):
        self.login_as('admin1')
        for bad in (self.body(capacity=0), self.body(capacity='x'), self.body(table_type='Roof'),
                    self.body(table_number='bad label'), self.body(status='Broken'),
                    {'capacity': 2}):
            r = self.client.post(TABLES, bad, format='json')
            self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(r.data['status'], 'error')
            self.assertEqual(r.data['error']['code'], 'validation_error')

    def test_duplicate_number_rejected_via_api(self):
        self.login_as('admin1')
        for label in ('T1', 't1'):
            r = self.client.post(TABLES, self.body(table_number=label), format='json')
            self.assertEqual(r.status_code, 400)
            self.assertIn('table_number', r.data['error']['details'])

    def test_same_number_other_restaurant_allowed_via_api(self):
        self.login_as('admin2')
        r = self.client.post(TABLES, self.body(table_number='A-2'), format='json')
        self.assertEqual(r.status_code, 201)

    def test_list_scoped_and_filtered(self):
        self.login_as('admin1')
        r = self.client.get(TABLES)
        self.assertEqual(r.status_code, 200)
        self.assertEqual({t['id'] for t in r.data['data']}, {self.t1.pk, self.t2.pk})
        r = self.client.get(TABLES, {'table_type': 'VIP'})
        self.assertEqual([t['id'] for t in r.data['data']], [self.t2.pk])
        Table.objects.filter(pk=self.t1.pk).update(status='Occupied')
        r = self.client.get(TABLES, {'status': 'Occupied'})
        self.assertEqual([t['id'] for t in r.data['data']], [self.t1.pk])

    def test_retrieve(self):
        self.login_as('admin1')
        r = self.client.get(f'{TABLES}/{self.t1.pk}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['data']['table_number'], 'T1')

    def test_admin_patch(self):
        self.login_as('admin1')
        r = self.client.patch(f'{TABLES}/{self.t1.pk}',
                              {'capacity': 8, 'table_type': 'Bar'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.t1.refresh_from_db()
        self.assertEqual((self.t1.capacity, self.t1.table_type), (8, 'Bar'))

    def test_patch_cannot_move_table_or_duplicate_number(self):
        self.login_as('admin1')
        self.client.patch(f'{TABLES}/{self.t1.pk}', {'restaurant': self.r2.pk}, format='json')
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.restaurant, self.r1)
        r = self.client.patch(f'{TABLES}/{self.t1.pk}', {'table_number': 'a-2'}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.client.patch(f'{TABLES}/{self.t1.pk}', {'table_number': 'T1'}, format='json')
        self.assertEqual(r.status_code, 200)  # keeping its own number is fine

    def test_put_not_allowed(self):
        self.login_as('admin1')
        r = self.client.put(f'{TABLES}/{self.t1.pk}', self.body(), format='json')
        self.assertEqual(r.status_code, 405)

    def test_admin_delete(self):
        self.login_as('admin1')
        r = self.client.delete(f'{TABLES}/{self.t1.pk}')
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Table.objects.filter(pk=self.t1.pk).exists())


class TablePermissionTests(TableTestBase):
    def test_staff_can_read(self):
        self.login_as('staff1')
        self.assertEqual(self.client.get(TABLES).status_code, 200)
        self.assertEqual(self.client.get(f'{TABLES}/{self.t1.pk}').status_code, 200)

    def test_staff_cannot_write_details(self):
        self.login_as('staff1')
        self.assertEqual(self.client.post(TABLES, self.body(), format='json').status_code, 403)
        r = self.client.patch(f'{TABLES}/{self.t1.pk}', {'capacity': 99}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['error']['code'], 'permission_denied')
        self.assertEqual(self.client.delete(f'{TABLES}/{self.t1.pk}').status_code, 403)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.capacity, 4)
        self.assertEqual(Table.objects.count(), 3)

    def test_staff_cannot_use_general_patch_even_for_status(self):
        self.login_as('staff1')
        r = self.client.patch(f'{TABLES}/{self.t1.pk}', {'status': 'Occupied'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_kitchen_and_platform_denied_everywhere(self):
        for user in ('kitchen1', 'platform1'):
            self.login_as(user)
            detail = f'{TABLES}/{self.t1.pk}'
            self.assertEqual(self.client.get(TABLES).status_code, 403, user)
            self.assertEqual(self.client.post(TABLES, self.body(), format='json').status_code, 403)
            self.assertEqual(self.client.get(detail).status_code, 403)
            self.assertEqual(self.client.patch(detail, {'capacity': 3}, format='json').status_code, 403)
            self.assertEqual(self.client.delete(detail).status_code, 403)
            self.assertEqual(
                self.client.patch(f'{detail}/status', {'status': 'Occupied'}, format='json').status_code, 403)
            self.assertEqual(self.client.get(PDF).status_code, 403)

    def test_anonymous_denied(self):
        detail = f'{TABLES}/{self.t1.pk}'
        for method, url in (('get', TABLES), ('post', TABLES), ('get', detail),
                            ('patch', detail), ('delete', detail),
                            ('patch', f'{detail}/status'), ('get', PDF)):
            r = getattr(self.client, method)(url)
            self.assertEqual(r.status_code, 401, (method, url))
            self.assertEqual(r.data['error']['code'], 'not_authenticated')


class TableTenantIsolationTests(TableTestBase):
    def test_other_restaurant_table_is_404(self):
        detail = f'{TABLES}/{self.other.pk}'
        for user in ('admin1', 'staff1'):
            self.login_as(user)
            self.assertEqual(self.client.get(detail).status_code, 404)
            self.assertEqual(
                self.client.patch(f'{detail}/status', {'status': 'Occupied'}, format='json').status_code, 404)
        self.login_as('admin1')
        self.assertEqual(self.client.patch(detail, {'capacity': 1}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(detail).status_code, 404)
        self.other.refresh_from_db()
        self.assertEqual((self.other.capacity, self.other.status), (6, 'Available'))

    def test_list_never_includes_other_restaurant(self):
        self.login_as('admin2')
        ids = {t['id'] for t in self.client.get(TABLES).data['data']}
        self.assertEqual(ids, {self.other.pk})


class TableStatusTests(TableTestBase):
    def url(self, table=None):
        return f'{TABLES}/{(table or self.t1).pk}/status'

    def test_all_statuses_for_admin_and_staff(self):
        for user in ('admin1', 'staff1'):
            self.login_as(user)
            for value in Table.Status.values:
                r = self.client.patch(self.url(), {'status': value}, format='json')
                self.assertEqual(r.status_code, 200, (user, value))
                self.assertEqual(r.data['data']['status'], value)
                self.t1.refresh_from_db()
                self.assertEqual(self.t1.status, value)

    def test_invalid_or_missing_status_rejected(self):
        self.login_as('staff1')
        for bad in ({'status': 'Broken'}, {'status': ''}, {}):
            r = self.client.patch(self.url(), bad, format='json')
            self.assertEqual(r.status_code, 400)
            self.assertIn('status', r.data['error']['details'])

    def test_only_status_changes(self):
        self.login_as('staff1')
        r = self.client.patch(self.url(), {
            'status': 'Reserved', 'capacity': 50, 'table_number': 'HACK',
            'table_type': 'Bar', 'restaurant': self.r2.pk,
        }, format='json')
        self.assertEqual(r.status_code, 200)
        self.t1.refresh_from_db()
        self.assertEqual(
            (self.t1.status, self.t1.capacity, self.t1.table_number, self.t1.table_type,
             self.t1.restaurant),
            ('Reserved', 4, 'T1', 'Indoor', self.r1))

    def test_other_http_methods_not_allowed(self):
        self.login_as('admin1')
        self.assertEqual(self.client.put(self.url(), {'status': 'Occupied'}, format='json').status_code, 405)


@override_settings(FRONTEND_BASE_URL='https://app.example.test/')
class QRTests(TableTestBase):
    def test_url_uses_configured_base_and_handles_trailing_slash(self):
        url = build_menu_url(self.r1.pk, self.t1.pk)
        self.assertEqual(
            url, f'https://app.example.test/menu?restaurant_id={self.r1.pk}&table={self.t1.pk}')
        with override_settings(FRONTEND_BASE_URL='http://localhost:5173'):
            self.assertTrue(build_menu_url(1, 2).startswith('http://localhost:5173/menu?'))

    def test_printed_url_keeps_confirmed_params_and_adds_token(self):
        token = self.t1.qr_token
        url = build_menu_url(self.r1.pk, self.t1.pk, token)
        parsed = urlparse(url)
        self.assertEqual(f'{parsed.scheme}://{parsed.netloc}{parsed.path}',
                         'https://app.example.test/menu')
        self.assertEqual(parse_qs(parsed.query), {
            'restaurant_id': [str(self.r1.pk)], 'table': [str(self.t1.pk)], 'token': [str(token)]})
        # Without a token the URL is exactly the originally confirmed format.
        self.assertEqual(urlparse(build_menu_url(1, 2)).query, 'restaurant_id=1&table=2')

    def test_scanned_url_resolves_to_matching_restaurant_and_table(self):
        # What the frontend does after a scan: read the URL, resolve the token,
        # and check the answer matches restaurant_id / table in the URL.
        query = parse_qs(urlparse(build_menu_url(self.r1.pk, self.t1.pk, self.t1.qr_token)).query)
        r = self.client.get(RESOLVE, {'token': query['token'][0]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(str(r.data['data']['restaurant_id']), query['restaurant_id'][0])
        self.assertEqual(str(r.data['data']['table_id']), query['table'][0])

    def test_pdf_embeds_each_tables_own_token_url(self):
        urls = []
        real = services.build_menu_url
        with mock.patch.object(services, 'build_menu_url', side_effect=lambda *a: urls.append(a) or real(*a)):
            render_qr_pdf(self.r1, [self.t1, self.t2])
        self.assertEqual(urls, [(self.r1.pk, self.t1.pk, self.t1.qr_token),
                                (self.r1.pk, self.t2.pk, self.t2.qr_token)])

    def test_url_parameters_are_encoded(self):
        query = parse_qs(urlparse(build_menu_url('a&b=c', 'x y')).query)
        self.assertEqual(query, {'restaurant_id': ['a&b=c'], 'table': ['x y']})

    def test_table_api_exposes_qr_url_but_not_token(self):
        self.login_as('admin1')
        data = self.client.get(f'{TABLES}/{self.t1.pk}').data['data']
        self.assertEqual(data['qr_url'], build_menu_url(self.r1.pk, self.t1.pk))
        self.assertNotIn(str(self.t1.qr_token), str(data))
        self.assertNotIn(str(self.t1.qr_token), data['qr_url'])

    def test_resolver_valid_token_is_public(self):
        r = self.client.get(RESOLVE, {'token': str(self.t1.qr_token)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['status'], 'success')
        self.assertEqual(r.data['data'], {
            'restaurant_id': self.r1.pk, 'restaurant_name': 'Bistro One',
            'table_id': self.t1.pk, 'table_number': 'T1',
        })

    def test_resolver_exposes_nothing_sensitive(self):
        r = self.client.get(RESOLVE, {'token': str(self.t1.qr_token)})
        self.assertEqual(
            set(r.data['data']),
            {'restaurant_id', 'restaurant_name', 'table_id', 'table_number'})
        text = r.content.decode()
        for secret in (str(self.t1.qr_token), 'admin1', 'password', 'capacity', 'Bistro Two'):
            self.assertNotIn(secret, text)

    def test_resolver_ignores_bad_auth_header(self):
        r = self.client.get(RESOLVE, {'token': str(self.t1.qr_token)},
                            HTTP_AUTHORIZATION='Bearer not-a-real-token')
        self.assertEqual(r.status_code, 200)

    def test_resolver_invalid_tokens_all_404_same_body(self):
        bodies = []
        for token in (str(uuid.uuid4()), 'not-a-uuid', '', '1'):
            r = self.client.get(RESOLVE, {'token': token})
            self.assertEqual(r.status_code, 404, token)
            self.assertEqual(r.data['error']['code'], 'not_found')
            self.assertEqual(r.data['status'], 'error')
            self.assertIsNone(r.data['data'])
            bodies.append(r.data)
        self.assertEqual(self.client.get(RESOLVE).status_code, 404)  # no token at all
        self.assertTrue(all(b == bodies[0] for b in bodies))

    def test_resolver_only_get(self):
        self.assertEqual(self.client.post(RESOLVE, {}, format='json').status_code, 405)

    def test_resolver_is_throttled_but_global_auth_unchanged(self):
        self.assertEqual([t.__name__ for t in QRResolveView.throttle_classes], ['QRResolveThrottle'])
        # Other public-looking endpoints still demand authentication.
        self.assertEqual(self.client.get(TABLES).status_code, 401)

    def test_resolver_throttle_triggers(self):
        from .views import QRResolveThrottle
        with override_settings():
            QRResolveThrottle.THROTTLE_RATES = {'qr_resolve': '2/min'}
            try:
                codes = [self.client.get(RESOLVE, {'token': 'x'}).status_code for _ in range(4)]
            finally:
                QRResolveThrottle.THROTTLE_RATES = {'qr_resolve': '30/min'}
        self.assertEqual(codes, [404, 404, 429, 429])


class QRPDFTests(TableTestBase):
    def test_admin_gets_pdf(self):
        self.login_as('admin1')
        r = self.client.get(PDF)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertIn('attachment; filename="tables-qr-bistro-one.pdf"', r['Content-Disposition'])
        content = r.content
        self.assertTrue(content.startswith(b'%PDF-'))
        self.assertIn(b'%%EOF', content[-32:])

    def test_staff_kitchen_platform_denied(self):
        for user in ('staff1', 'kitchen1', 'platform1'):
            self.login_as(user)
            self.assertEqual(self.client.get(PDF).status_code, 403, user)

    def test_pdf_contains_only_own_restaurant(self):
        # Uncompressed render to inspect the text layer.
        from reportlab import rl_config
        rl_config.pageCompression = 0
        try:
            own = render_qr_pdf(self.r1, [self.t1, self.t2])
        finally:
            rl_config.pageCompression = 1
        self.assertIn(b'Bistro One', own)
        self.assertIn(b'Table A-2', own)
        self.assertNotIn(b'Bistro Two', own)
        # And through the endpoint: r2's admin gets a different, own-only PDF.
        self.login_as('admin2')
        r = self.client.get(PDF)
        self.assertEqual(r.status_code, 200)
        self.assertIn('bistro-two', r['Content-Disposition'])

    def test_pdf_paginates(self):
        for i in range(10):
            Table.objects.create(restaurant=self.r1, table_number=f'P{i}', capacity=2)
        pdf = render_qr_pdf(self.r1, list(Table.objects.filter(restaurant=self.r1)))
        self.assertEqual(pdf.count(b'/Type /Page\n'), 2)  # 12 tables, 6 per page

    def test_empty_restaurant_gets_a_valid_pdf_saying_so(self):
        r3 = Restaurant.objects.create(name='Empty Place')
        User.objects.create_user('admin3', PASSWORD, role='Admin', restaurant=r3)
        self.login_as('admin3')
        r = self.client.get(PDF)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content.startswith(b'%PDF-'))
        self.assertIn('tables-qr-empty-place.pdf', r['Content-Disposition'])
        from reportlab import rl_config
        rl_config.pageCompression = 0
        try:
            pdf = render_qr_pdf(r3, [])
        finally:
            rl_config.pageCompression = 1
        self.assertIn(b'No tables available', pdf)
        self.assertIn(b'Empty Place', pdf)
        self.assertEqual(pdf.count(b'/Type /Page\n'), 1)
        self.assertIn(b'/MediaBox [ 0 0 595.27', pdf)  # A4

    def test_card_layout_does_not_overlap(self):
        s = services
        # Vertical stack, measured down from the card top (mm); text heights are
        # generous upper bounds (cap height ~0.72 em, descender ~0.21 em, 1 pt = 0.3528 mm).
        def text_box(baseline, pt):
            return baseline - 0.72 * pt * 0.3528, baseline + 0.21 * pt * 0.3528
        name = text_box(s.NAME_BASELINE, 12)
        label = text_box(s.LABEL_BASELINE, 26)
        kind = text_box(s.TYPE_BASELINE, 10)
        footer = text_box(s.FOOTER_BASELINE, 8)
        qr = (s.QR_TOP, s.QR_BOTTOM)
        guide_bottom = s.CARD_H - s.GUIDE_INSET
        order = [(0, s.GUIDE_INSET), name, label, qr, kind, footer, (guide_bottom, guide_bottom)]
        for upper, lower in zip(order, order[1:]):
            self.assertLess(upper[1], lower[0], (upper, lower))
        self.assertGreaterEqual(s.QR_SIZE, 45)            # large enough to scan when printed
        self.assertLessEqual(s.CARD_W, 105)               # two columns fit on A4 with margins
        self.assertEqual(s.QR_BOTTOM, s.QR_TOP + s.QR_SIZE)
        self.assertLessEqual(2 * s.PAGE_MARGIN + s.ROWS * s.CARD_H, 297 + 1e-9)

    def test_long_names_are_fitted(self):
        r = Restaurant.objects.create(name='X' * 120)
        t = Table.objects.create(restaurant=r, table_number='LONGLABEL-1234567', capacity=1)
        pdf = render_qr_pdf(r, [t])
        self.assertTrue(pdf.startswith(b'%PDF-'))


class FrontendBaseURLCheckTests(TableTestBase):
    def test_default_setting_is_loaded_from_settings(self):
        from django.conf import settings
        self.assertTrue(settings.FRONTEND_BASE_URL.startswith('http'))

    def run_check(self, url, debug):
        with override_settings(FRONTEND_BASE_URL=url, DEBUG=debug):
            return [m.id for m in check_frontend_base_url(None)]

    def test_check(self):
        self.assertEqual(self.run_check('https://app.example.com', False), [])
        self.assertEqual(self.run_check('http://localhost:5173', True), [])
        self.assertEqual(self.run_check('http://localhost:5173', False), ['tables.W001'])
        self.assertEqual(self.run_check('app.example.com', True), ['tables.E001'])


class SwaggerTests(TableTestBase):
    def test_schema_lists_table_endpoints(self):
        r = self.client.get('/api/schema/', {'format': 'json'})
        self.assertEqual(r.status_code, 200)
        paths = r.json()['paths']
        for path in ('/api/v1/tables', '/api/v1/tables/{id}', '/api/v1/tables/{id}/status',
                     '/api/v1/tables/qr-pdf', '/api/v1/qr/resolve/'):
            self.assertIn(path, paths)
        self.assertIn('application/pdf', paths['/api/v1/tables/qr-pdf']['get']['responses']['200']['content'])
        self.assertNotIn('put', paths['/api/v1/tables/{id}'])
        self.assertEqual(paths['/api/v1/qr/resolve/']['get']['tags'], ['Tables'])
