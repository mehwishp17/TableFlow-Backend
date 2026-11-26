import os
from unittest import mock

from django.core.exceptions import ValidationError
from rest_framework.test import APITestCase

from orders.testutils import OrderFixtureMixin, client_for
from printing.models import PrinterSettings
from printing.validators import validate_folder_path

URL = '/api/v1/restaurant/printer-settings/'

VALID_PATHS = [
    'C:\\TableFlow\\KOT',
    'C:/TableFlow/KOT',
    'D:\\',
    'c:\\Kitchen Printer\\Tickets 2026',
    '\\\\print-server\\share\\kot',
    '/var/spool/tableflow',
    '/',
    'C:\\TableFlow\\KOT\\',
    'C:\\a.b\\c-d_e',
]

INVALID_PATHS = {
    'relative': 'TableFlow\\KOT',
    'relative dot': '.\\kot',
    'no slash after drive': 'C:kot',
    'dot dot': 'C:\\TableFlow\\..\\Windows',
    'dot dot posix': '/var/../etc',
    'illegal char <': 'C:\\a<b',
    'illegal char |': 'C:\\a|b',
    'illegal char ?': 'C:\\a?b',
    'illegal char *': 'C:\\*',
    'illegal quote': 'C:\\"kot"',
    'second colon': 'C:\\a:b',
    'newline': 'C:\\ko\nt',
    'tab': 'C:\\ko\tt',
    'null byte': 'C:\\kot\x00',
    'trailing space in a folder': 'C:\\kot \\x',
    'trailing dot in a folder': 'C:\\kot.\\x',
    'unc without share': '\\\\server',
    'url': 'https://example.com/kot',
    'too long': 'C:\\' + 'a' * 300,
}


class FolderPathValidatorTests(APITestCase):
    def test_valid_paths(self):
        for path in VALID_PATHS:
            with self.subTest(path=path):
                self.assertEqual(validate_folder_path(path), path)

    def test_an_empty_value_means_not_configured(self):
        self.assertEqual(validate_folder_path(''), '')

    def test_invalid_paths(self):
        for name, path in INVALID_PATHS.items():
            with self.subTest(name):
                with self.assertRaises(ValidationError):
                    validate_folder_path(path)


class PrinterSettingsTests(OrderFixtureMixin, APITestCase):
    def get(self, user):
        return client_for(user).get(URL)

    def patch(self, user, body):
        return client_for(user).patch(URL, body, format='json')

    def test_an_unconfigured_restaurant_gets_an_empty_setting(self):
        r = self.get(self.admin1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'data': {'folder_path': '', 'is_configured': False,
                                             'updated_at': None},
                                    'error': None, 'status': 'success'})
        self.assertEqual(PrinterSettings.objects.count(), 0)         # a GET never writes

    def test_admin_saves_and_reads_the_folder(self):
        r = self.patch(self.admin1, {'folder_path': 'C:\\TableFlow\\KOT'})
        self.assertEqual(r.status_code, 200)
        data = r.json()['data']
        self.assertEqual(set(data), {'folder_path', 'is_configured', 'updated_at'})
        self.assertEqual((data['folder_path'], data['is_configured']), ('C:\\TableFlow\\KOT', True))
        self.assertTrue(data['updated_at'])
        again = self.get(self.admin1).json()['data']
        self.assertEqual(again['folder_path'], 'C:\\TableFlow\\KOT')
        self.assertEqual(PrinterSettings.objects.get().restaurant_id, self.r1.pk)

    def test_saving_again_updates_the_same_row(self):
        self.patch(self.admin1, {'folder_path': 'C:\\One'})
        self.patch(self.admin1, {'folder_path': 'C:\\Two'})
        self.assertEqual(PrinterSettings.objects.count(), 1)
        self.assertEqual(self.get(self.admin1).json()['data']['folder_path'], 'C:\\Two')

    def test_surrounding_whitespace_is_trimmed_and_blank_clears_the_setting(self):
        self.patch(self.admin1, {'folder_path': '  /var/spool/tableflow  '})
        self.assertEqual(self.get(self.admin1).json()['data']['folder_path'],
                         '/var/spool/tableflow')
        r = self.patch(self.admin1, {'folder_path': '   '})
        self.assertEqual((r.status_code, r.json()['data']['folder_path'],
                          r.json()['data']['is_configured']), (200, '', False))
        r = self.patch(self.admin1, {'folder_path': 'C:\\Back'})
        self.patch(self.admin1, {'folder_path': ''})
        self.assertFalse(self.get(self.admin1).json()['data']['is_configured'])

    def test_an_update_without_the_field_changes_nothing(self):
        self.patch(self.admin1, {'folder_path': 'C:\\Keep'})
        self.assertEqual(self.patch(self.admin1, {}).status_code, 200)
        self.assertEqual(self.get(self.admin1).json()['data']['folder_path'], 'C:\\Keep')

    def test_every_valid_format_is_accepted_through_the_api(self):
        for path in VALID_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.patch(self.admin1, {'folder_path': path}).status_code, 200)

    def test_invalid_paths_are_rejected_and_nothing_is_saved(self):
        self.patch(self.admin1, {'folder_path': 'C:\\Safe'})
        for name, path in INVALID_PATHS.items():
            with self.subTest(name):
                r = self.patch(self.admin1, {'folder_path': path})
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.json()['error']['code'], 'validation_error')
                self.assertIn('folder_path', r.json()['error']['details'])
        self.assertEqual(self.get(self.admin1).json()['data']['folder_path'], 'C:\\Safe')

    def test_settings_are_per_restaurant(self):
        self.patch(self.admin1, {'folder_path': 'C:\\One'})
        self.patch(self.admin2, {'folder_path': 'C:\\Two'})
        self.assertEqual(self.get(self.admin1).json()['data']['folder_path'], 'C:\\One')
        self.assertEqual(self.get(self.admin2).json()['data']['folder_path'], 'C:\\Two')
        self.assertEqual(self.get(self.kitchen1).json()['data']['folder_path'], 'C:\\One')
        self.assertEqual(self.get(self.kitchen2).json()['data']['folder_path'], 'C:\\Two')
        self.assertEqual(PrinterSettings.objects.count(), 2)

    def test_the_restaurant_cannot_be_chosen_by_the_client(self):
        self.patch(self.admin1, {'folder_path': 'C:\\One', 'restaurant': self.r2.pk,
                                 'restaurant_id': self.r2.pk})
        self.assertEqual(list(PrinterSettings.objects.values_list('restaurant_id', flat=True)),
                         [self.r1.pk])

    def test_kitchen_may_read_but_not_change_so_print_py_can_fetch_it(self):
        self.patch(self.admin1, {'folder_path': 'C:\\TableFlow\\KOT'})
        self.assertEqual(self.get(self.kitchen1).status_code, 200)
        self.assertEqual(self.patch(self.kitchen1, {'folder_path': 'C:\\Evil'}).status_code, 403)
        self.assertEqual(self.get(self.admin1).json()['data']['folder_path'], 'C:\\TableFlow\\KOT')

    def test_staff_and_platform_have_no_access(self):
        for user in (self.staff1, self.platform):
            self.assertEqual(self.get(user).status_code, 403, user.id)
            self.assertEqual(self.patch(user, {'folder_path': 'C:\\x'}).status_code, 403, user.id)
        for method in ('get', 'patch'):
            self.assertEqual(getattr(client_for(None), method)(URL).status_code, 401)

    def test_other_methods_are_not_allowed(self):
        self.assertEqual(client_for(self.admin1).post(URL, {}, format='json').status_code, 405)
        self.assertEqual(client_for(self.admin1).delete(URL).status_code, 405)

    def test_the_server_never_touches_the_filesystem_path(self):
        """The path is stored as text; the cloud backend never opens, creates or lists it."""
        boom = AssertionError('the backend must not access the configured folder')
        with mock.patch.object(os, 'makedirs', side_effect=boom), \
                mock.patch.object(os, 'mkdir', side_effect=boom), \
                mock.patch.object(os, 'listdir', side_effect=boom), \
                mock.patch.object(os, 'scandir', side_effect=boom), \
                mock.patch.object(os.path, 'isdir', side_effect=boom), \
                mock.patch.object(os.path, 'exists', side_effect=boom):
            self.assertEqual(
                self.patch(self.admin1, {'folder_path': 'C:\\DoesNotExist\\Here'}).status_code, 200)
            self.assertEqual(self.get(self.admin1).status_code, 200)
