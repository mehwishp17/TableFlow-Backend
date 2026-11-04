from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, APITestCase
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from restaurants.models import Restaurant

from .models import User

PASSWORD = 'S3cure-pass!'


class LoginAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        restaurant = Restaurant.objects.create(name='Test Bistro')
        for role in ('Admin', 'Staff', 'Kitchen'):
            User.objects.create_user(
                f'{role.lower()}1', PASSWORD, role=role, restaurant=restaurant
            )
        User.objects.create_user('platform1', PASSWORD, role='Platform')
        cls.url = reverse('auth-login')

    def login(self, **overrides):
        body = {'id': 'admin1', 'password': PASSWORD, 'role': 'Admin'}
        body.update(overrides)
        return self.client.post(self.url, body, format='json')

    def test_url_matches_spec(self):
        self.assertEqual(self.url, '/api/v1/auth/login')

    def test_successful_login_for_each_role(self):
        for role in ('Admin', 'Staff', 'Kitchen', 'Platform'):
            with self.subTest(role=role):
                response = self.login(id=f'{role.lower()}1', role=role)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['status'], 'success')
                self.assertIsNone(response.data['error'])
                data = response.data['data']
                self.assertEqual(data['id'], f'{role.lower()}1')
                self.assertEqual(data['role'], role)
                # exactly these keys: no password or hash can leak
                self.assertEqual(set(data), {'id', 'role', 'access', 'refresh'})

    def test_incorrect_password(self):
        response = self.login(password='wrong')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data['status'], 'error')
        self.assertIsNone(response.data['data'])
        self.assertEqual(response.data['error']['code'], 'invalid_credentials')

    def test_nonexistent_user_gets_same_error_as_wrong_password(self):
        missing = self.login(id='ghost')
        wrong = self.login(password='wrong')
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.data, wrong.data)

    def test_invalid_role(self):
        response = self.login(role='Owner')
        self.assertEqual(response.status_code, 400)
        self.assertIn('role', response.data['error']['details'])

    def test_role_is_case_sensitive(self):
        self.assertEqual(self.login(role='admin').status_code, 400)

    def test_role_mismatch_is_rejected_and_no_tokens_issued(self):
        response = self.login(id='staff1', role='Admin')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['error']['code'], 'role_mismatch')
        self.assertIsNone(response.data['data'])

    def test_wrong_password_and_wrong_role_reports_credentials_error(self):
        response = self.login(id='staff1', password='wrong', role='Admin')
        self.assertEqual(response.status_code, 401)

    def test_missing_fields(self):
        for field in ('id', 'password', 'role'):
            with self.subTest(missing=field):
                body = {'id': 'admin1', 'password': PASSWORD, 'role': 'Admin'}
                del body[field]
                response = self.client.post(self.url, body, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data['error']['details'])

    def test_inactive_user_cannot_log_in(self):
        User.objects.filter(id='admin1').update(is_active=False)
        self.assertEqual(self.login().status_code, 401)

    def test_jwt_tokens_are_valid_and_carry_role(self):
        data = self.login(id='kitchen1', role='Kitchen').data['data']
        access = AccessToken(data['access'])  # raises if invalid or tampered
        self.assertEqual(access['user_id'], 'kitchen1')
        self.assertEqual(access['role'], 'Kitchen')
        refresh = RefreshToken(data['refresh'])
        self.assertEqual(refresh['user_id'], 'kitchen1')

    def test_login_ignores_a_bad_authorization_header(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer not-a-token')
        self.assertEqual(self.login().status_code, 200)


class ProtectedEndpointTests(TestCase):
    """The project default stays 'authentication required', and the access
    token issued by login is accepted by it."""

    class Probe(APIView):  # uses only the project-wide DRF defaults
        def get(self, request):
            return Response({'user': request.user.id})

    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user('platform1', PASSWORD, role='Platform')

    def test_anonymous_request_is_rejected(self):
        response = self.Probe.as_view()(self.factory.get('/'))
        self.assertEqual(response.status_code, 401)

    def test_valid_access_token_is_accepted(self):
        token = RefreshToken.for_user(self.user).access_token
        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Bearer {token}')
        response = self.Probe.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['user'], 'platform1')

    def test_tampered_access_token_is_rejected(self):
        token = RefreshToken.for_user(self.user).access_token
        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Bearer {token}x')
        self.assertEqual(self.Probe.as_view()(request).status_code, 401)


class UserModelTests(TestCase):
    def test_password_is_stored_hashed(self):
        user = User.objects.create_user('platform1', PASSWORD, role='Platform')
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.password.startswith('pbkdf2_sha256$'))
        self.assertTrue(user.check_password(PASSWORD))

    def test_restaurant_user_requires_restaurant(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user('admin1', PASSWORD, role='Admin')

    def test_platform_user_cannot_belong_to_restaurant(self):
        restaurant = Restaurant.objects.create(name='X')
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(
                'platform1', PASSWORD, role='Platform', restaurant=restaurant
            )

    def test_superuser_is_platform(self):
        user = User.objects.create_superuser('root', PASSWORD)
        self.assertEqual(user.role, 'Platform')
        self.assertTrue(user.is_staff and user.is_superuser)
