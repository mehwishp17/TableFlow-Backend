"""The public rate limits must not be dodged by sending a different X-Forwarded-For header.

These tests ASSUME exactly one trusted proxy that appends the connecting address to the END of
the header, so only the last entry is trusted. That has not been verified on Render: passing
tests show how the code behaves under that assumption, not how the deployed server behaves."""
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from orders.testutils import OrderFixtureMixin

CUSTOMER = '/api/v1/calls/customer/'
RESOLVE = '/api/v1/qr/resolve/'
LIMIT = 30


def rest_framework_with(**changes):
    return {**settings.REST_FRAMEWORK, **changes}


class ProxyThrottleTests(OrderFixtureMixin, APITestCase):
    def setUp(self):
        cache.clear()

    def customer(self, xff=None):
        extra = {'HTTP_X_FORWARDED_FOR': xff} if xff else {}
        return APIClient().post(CUSTOMER, {'token': str(self.t1.qr_token)}, format='json',
                                **extra).status_code

    def resolve(self, xff=None):
        extra = {'HTTP_X_FORWARDED_FOR': xff} if xff else {}
        return APIClient().get(RESOLVE, {'token': str(self.t1.qr_token)}, **extra).status_code

    def test_one_trusted_proxy_is_the_default(self):
        self.assertEqual(settings.REST_FRAMEWORK['NUM_PROXIES'], 1)

    def test_changing_the_forwarded_for_header_does_not_reset_the_customer_limit(self):
        # Same real client (the address the proxy appended), a new fake address every time.
        codes = [self.customer(f'10.9.8.{i}, 203.0.113.7') for i in range(LIMIT + 1)]
        self.assertNotIn(429, codes[:LIMIT])
        self.assertEqual(codes[LIMIT], 429)

    def test_changing_the_forwarded_for_header_does_not_reset_the_qr_resolver_limit(self):
        codes = [self.resolve(f'10.9.8.{i}, 203.0.113.7') for i in range(LIMIT + 1)]
        self.assertEqual(set(codes[:LIMIT]), {200})
        self.assertEqual(codes[LIMIT], 429)

    def test_a_longer_spoofed_chain_makes_no_difference(self):
        codes = [self.customer(', '.join(f'1.1.{i}.{j}' for j in range(1, 6)) + ', 203.0.113.7')
                 for i in range(LIMIT + 1)]
        self.assertEqual(codes[LIMIT], 429)

    def test_different_real_clients_have_separate_limits(self):
        for _ in range(LIMIT + 1):
            self.customer('203.0.113.7')
        self.assertEqual(self.customer('203.0.113.7'), 429)
        self.assertNotEqual(self.customer('203.0.113.8'), 429)       # another real client
        self.assertNotEqual(self.resolve('203.0.113.8'), 429)

    def test_without_the_header_the_socket_address_is_used(self):
        codes = [self.customer() for _ in range(LIMIT + 1)]
        self.assertEqual(codes[LIMIT], 429)

    @override_settings(REST_FRAMEWORK=rest_framework_with(NUM_PROXIES=0))
    def test_with_no_proxy_the_header_is_ignored_completely(self):
        codes = [self.customer(f'10.9.8.{i}') for i in range(LIMIT + 1)]
        self.assertEqual(codes[LIMIT], 429)

    @override_settings(REST_FRAMEWORK=rest_framework_with(NUM_PROXIES=None))
    def test_demonstration_the_old_setting_was_bypassable(self):
        """With NUM_PROXIES unset (the previous behaviour) a new header value is a new
        client, so the limit never triggers. This is why the setting is configured."""
        codes = [self.customer(f'10.9.8.{i}, 203.0.113.7') for i in range(LIMIT + 1)]
        self.assertNotIn(429, codes)
