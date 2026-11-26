from rest_framework.test import APIClient, APITestCase

ROUTES = {
    '/api/v1/calls/': {'get', 'post'},
    '/api/v1/calls/customer/': {'post'},
    '/api/v1/calls/{id}/pick/': {'patch'},
    '/api/v1/calls/{id}/resolve/': {'patch'},
    '/api/v1/calls/{id}/cancel/': {'patch'},
    '/api/v1/staff/': {'get', 'post'},
    '/api/v1/staff/{id}/': {'get', 'patch', 'delete'},
    '/api/v1/staff/online/': {'get'},
    '/api/v1/staff/me/scorecard/': {'get'},
    '/api/v1/staff/scorecards/': {'get'},
    '/api/v1/staff/{id}/scorecard/': {'get'},
    '/api/v1/restaurant/profile/': {'get', 'patch'},
    '/api/v1/restaurant/printer-settings/': {'get', 'patch'},
    '/api/v1/auth/logout': {'post'},
}


class NewRoutesAreDocumentedTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.schema = APIClient().get('/api/schema/', {'format': 'json'}).json()

    def test_every_new_route_and_method_is_in_the_openapi_schema(self):
        paths = {}
        for path, item in self.schema['paths'].items():
            paths[path.replace('{pk}', '{id}')] = {m for m in item if m != 'parameters'}
        for route, methods in ROUTES.items():
            with self.subTest(route=route):
                self.assertIn(route, paths)
                self.assertEqual(paths[route], methods)

    def test_each_operation_has_a_summary_description_and_tag(self):
        for route in ROUTES:
            item = next(v for k, v in self.schema['paths'].items()
                        if k.replace('{pk}', '{id}') == route)
            for method, op in item.items():
                if method == 'parameters':
                    continue
                with self.subTest(route=route, method=method):
                    self.assertTrue(op.get('summary'))
                    self.assertTrue(op.get('description'))
                    self.assertTrue(op.get('tags'))

    def test_the_customer_route_is_public_and_the_others_need_a_token(self):
        public = self.schema['paths']['/api/v1/calls/customer/']['post']
        self.assertEqual(public.get('security'), [{}])
        for route, method in (('/api/v1/calls/', 'get'), ('/api/v1/staff/', 'post'),
                              ('/api/v1/restaurant/profile/', 'patch')):
            op = self.schema['paths'][route][method]
            self.assertIn({'jwtAuth': []}, op.get('security', self.schema.get('security', [])))

    def test_status_codes_of_the_call_actions_are_documented(self):
        pick = self.schema['paths']['/api/v1/calls/{id}/pick/']['patch'] \
            if '/api/v1/calls/{id}/pick/' in self.schema['paths'] else \
            self.schema['paths']['/api/v1/calls/{pk}/pick/']['patch']
        self.assertTrue({'200', '401', '403', '404', '409'} <= set(pick['responses']))
