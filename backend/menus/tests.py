from django.db import IntegrityError, transaction
from rest_framework.test import APITestCase

from accounts.models import User
from restaurants.models import Restaurant

from .models import Category, MenuItem

PASSWORD = 'S3cure-pass!'
CATEGORIES = '/api/v1/menu/categories'
ITEMS = '/api/v1/menu/items'


class MenuTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.r1 = Restaurant.objects.create(name='Bistro One')
        cls.r2 = Restaurant.objects.create(name='Bistro Two')
        for role in ('Admin', 'Staff', 'Kitchen'):
            User.objects.create_user(f'{role.lower()}1', PASSWORD, role=role, restaurant=cls.r1)
        User.objects.create_user('admin2', PASSWORD, role='Admin', restaurant=cls.r2)
        User.objects.create_user('platform1', PASSWORD, role='Platform')

        cls.starters = Category.objects.create(restaurant=cls.r1, name='Starters')
        cls.soups = Category.objects.create(restaurant=cls.r1, name='Soups', parent=cls.starters)
        cls.item = MenuItem.objects.create(
            category=cls.starters, sub_category=cls.soups, name='Tomato Soup',
            price='5.50', dish_type='Veg',
        )
        cls.other_cat = Category.objects.create(restaurant=cls.r2, name='Mains')
        cls.other_item = MenuItem.objects.create(
            category=cls.other_cat, name='Steak', price='20.00', dish_type='Non-Veg'
        )

    def login_as(self, user_id):
        self.client.force_authenticate(User.objects.get(pk=user_id))

    def item_body(self, **overrides):
        body = {
            'name': 'Paneer Tikka', 'price': '9.99', 'category': self.starters.pk,
            'dish_type': 'Veg', 'description': 'Grilled', 'ingredients': 'paneer, spices',
            'calories': 320, 'portion_size': '6 pcs', 'image_url': 'https://example.com/p.jpg',
        }
        body.update(overrides)
        return body


class CategoryAPITests(MenuTestBase):
    def test_create(self):
        self.login_as('admin1')
        r = self.client.post(CATEGORIES, {'name': 'Desserts', 'description': 'Sweet'}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['status'], 'success')
        self.assertIsNone(r.data['error'])
        category = Category.objects.get(pk=r.data['data']['id'])
        self.assertEqual(category.restaurant, self.r1)  # taken from the user, not the body

    def test_create_ignores_restaurant_in_body(self):
        self.login_as('admin1')
        r = self.client.post(CATEGORIES, {'name': 'X', 'restaurant': self.r2.pk}, format='json')
        self.assertEqual(Category.objects.get(pk=r.data['data']['id']).restaurant, self.r1)

    def test_create_subcategory(self):
        self.login_as('admin1')
        r = self.client.post(CATEGORIES, {'name': 'Salads', 'parent': self.starters.pk}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['data']['parent'], self.starters.pk)

    def test_subcategories_cannot_nest(self):
        self.login_as('admin1')
        r = self.client.post(CATEGORIES, {'name': 'Deep', 'parent': self.soups.pk}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_parent_from_other_restaurant_rejected(self):
        self.login_as('admin1')
        r = self.client.post(CATEGORIES, {'name': 'X', 'parent': self.other_cat.pk}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('parent', r.data['error']['details'])

    def test_duplicate_name_rejected(self):
        self.login_as('admin1')
        r = self.client.post(CATEGORIES, {'name': 'starters'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_same_name_allowed_in_other_restaurant(self):
        self.login_as('admin2')
        r = self.client.post(CATEGORIES, {'name': 'Starters'}, format='json')
        self.assertEqual(r.status_code, 201)

    def test_blank_name_rejected(self):
        self.login_as('admin1')
        r = self.client.post(CATEGORIES, {'name': ''}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data['status'], 'error')

    def test_list_only_own_restaurant(self):
        self.login_as('admin1')
        r = self.client.get(CATEGORIES)
        self.assertEqual(r.status_code, 200)
        names = {c['name'] for c in r.data['data']}
        self.assertEqual(names, {'Starters', 'Soups'})

    def test_retrieve(self):
        self.login_as('staff1')
        r = self.client.get(f'{CATEGORIES}/{self.starters.pk}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['data']['name'], 'Starters')

    def test_update_patch_and_put(self):
        self.login_as('admin1')
        r = self.client.patch(f'{CATEGORIES}/{self.starters.pk}', {'description': 'Light bites'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.starters.refresh_from_db()
        self.assertEqual(self.starters.description, 'Light bites')
        r = self.client.put(f'{CATEGORIES}/{self.starters.pk}', {'name': 'Appetisers'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['data']['name'], 'Appetisers')

    def test_delete_empty_category(self):
        self.login_as('admin1')
        empty = Category.objects.create(restaurant=self.r1, name='Empty')
        r = self.client.delete(f'{CATEGORIES}/{empty.pk}')
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Category.objects.filter(pk=empty.pk).exists())

    def test_delete_category_in_use_is_conflict(self):
        self.login_as('admin1')
        r = self.client.delete(f'{CATEGORIES}/{self.starters.pk}')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['error']['code'], 'conflict')
        self.assertTrue(Category.objects.filter(pk=self.starters.pk).exists())

    def test_not_found_uses_error_envelope(self):
        self.login_as('admin1')
        r = self.client.get(f'{CATEGORIES}/99999')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.data['status'], 'error')
        self.assertEqual(r.data['error']['code'], 'not_found')


class MenuItemAPITests(MenuTestBase):
    def test_create(self):
        self.login_as('admin1')
        r = self.client.post(ITEMS, self.item_body(sub_category=self.soups.pk), format='json')
        self.assertEqual(r.status_code, 201, r.data)
        data = r.data['data']
        self.assertEqual(data['name'], 'Paneer Tikka')
        self.assertEqual(data['price'], '9.99')
        self.assertTrue(data['is_available'])
        self.assertEqual(data['sub_category'], self.soups.pk)
        self.assertEqual(data['category_name'], 'Starters')

    def test_create_validation(self):
        self.login_as('admin1')
        cases = {
            'negative price': {'price': '-1'},
            'bad dish type': {'dish_type': 'Vegan'},
            'bad url': {'image_url': 'not a url'},
            'negative calories': {'calories': -5},
            'missing name': {'name': ''},
            'category of other restaurant': {'category': self.other_cat.pk},
            'top-level category used as sub_category': {'sub_category': self.starters.pk},
        }
        for label, override in cases.items():
            with self.subTest(label):
                r = self.client.post(ITEMS, self.item_body(**override), format='json')
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.data['error']['code'], 'validation_error')

    def test_subcategory_must_belong_to_category(self):
        self.login_as('admin1')
        mains = Category.objects.create(restaurant=self.r1, name='Mains')
        r = self.client.post(ITEMS, self.item_body(category=mains.pk, sub_category=self.soups.pk), format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('sub_category', r.data['error']['details'])

    def test_duplicate_name_in_category_rejected(self):
        self.login_as('admin1')
        r = self.client.post(ITEMS, self.item_body(name='tomato soup'), format='json')
        self.assertEqual(r.status_code, 400)

    def test_list_filters_and_isolation(self):
        self.login_as('admin1')
        r = self.client.get(ITEMS)
        self.assertEqual([i['name'] for i in r.data['data']], ['Tomato Soup'])
        self.assertEqual(len(self.client.get(ITEMS, {'dish_type': 'Non-Veg'}).data['data']), 0)
        self.assertEqual(len(self.client.get(ITEMS, {'is_available': 'true'}).data['data']), 1)
        self.assertEqual(len(self.client.get(ITEMS, {'category': self.starters.pk}).data['data']), 1)

    def test_retrieve(self):
        self.login_as('kitchen1')
        r = self.client.get(f'{ITEMS}/{self.item.pk}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['data']['name'], 'Tomato Soup')

    def test_update(self):
        self.login_as('admin1')
        r = self.client.patch(f'{ITEMS}/{self.item.pk}', {'price': '6.00', 'calories': 150}, format='json')
        self.assertEqual(r.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(str(self.item.price), '6.00')
        self.assertEqual(self.item.calories, 150)
        r = self.client.put(f'{ITEMS}/{self.item.pk}', self.item_body(name='Soup', sub_category=self.soups.pk), format='json')
        self.assertEqual(r.status_code, 200, r.data)

    def test_update_clearing_category_leaves_valid_subcategory_check(self):
        self.login_as('admin1')
        mains = Category.objects.create(restaurant=self.r1, name='Mains')
        r = self.client.patch(f'{ITEMS}/{self.item.pk}', {'category': mains.pk}, format='json')
        self.assertEqual(r.status_code, 400)  # existing sub_category no longer matches

    def test_delete(self):
        self.login_as('admin1')
        r = self.client.delete(f'{ITEMS}/{self.item.pk}')
        self.assertEqual(r.status_code, 204)
        self.assertFalse(MenuItem.objects.filter(pk=self.item.pk).exists())


class AvailabilityTests(MenuTestBase):
    def url(self, item=None):
        return f'/api/v1/menu/{(item or self.item).pk}/availability'

    def test_admin_and_staff_can_toggle_and_db_is_updated(self):
        for user in ('admin1', 'staff1'):
            for value in (False, True):
                with self.subTest(user=user, value=value):
                    self.login_as(user)
                    r = self.client.patch(self.url(), {'is_available': value}, format='json')
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(r.data['data']['id'], self.item.pk)
                    self.assertEqual(r.data['data']['is_available'], value)
                    self.assertEqual(r.data['data']['is_orderable'], value)
                    self.item.refresh_from_db()
                    self.assertEqual(self.item.is_available, value)

    def test_kitchen_and_platform_forbidden(self):
        for user in ('kitchen1', 'platform1'):
            with self.subTest(user=user):
                self.login_as(user)
                r = self.client.patch(self.url(), {'is_available': False}, format='json')
                self.assertEqual(r.status_code, 403)
                self.assertEqual(r.data['error']['code'], 'permission_denied')
        self.item.refresh_from_db()
        self.assertTrue(self.item.is_available)

    def test_invalid_values_rejected(self):
        self.login_as('admin1')
        for bad in ('yes', 'true', 1, 0, None, 'maybe', [], {}):
            with self.subTest(bad=bad):
                r = self.client.patch(self.url(), {'is_available': bad}, format='json')
                self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.patch(self.url(), {}, format='json').status_code, 400)
        self.item.refresh_from_db()
        self.assertTrue(self.item.is_available)

    def test_other_restaurant_item_is_404_and_untouched(self):
        self.login_as('admin1')
        r = self.client.patch(self.url(self.other_item), {'is_available': False}, format='json')
        self.assertEqual(r.status_code, 404)
        self.other_item.refresh_from_db()
        self.assertTrue(self.other_item.is_available)

    def test_unauthenticated(self):
        r = self.client.patch(self.url(), {'is_available': False}, format='json')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.data['error']['code'], 'not_authenticated')

    def test_unavailable_dish_is_not_orderable_but_still_listed(self):
        self.login_as('staff1')
        self.client.patch(self.url(), {'is_available': False}, format='json')
        self.item.refresh_from_db()
        self.assertFalse(self.item.is_orderable)
        r = self.client.get('/api/v1/menu')
        dish = r.data['data'][0]['sub_categories'][0]['items'][0]
        self.assertFalse(dish['is_available'])
        self.assertFalse(dish['is_orderable'])


class CatalogTests(MenuTestBase):
    def test_public_with_restaurant_param(self):
        r = self.client.get('/api/v1/menu', {'restaurant': self.r1.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c['name'] for c in r.data['data']], ['Starters'])
        starters = r.data['data'][0]
        self.assertEqual(starters['items'], [])  # the dish lives in the Soups sub-category
        self.assertEqual(starters['sub_categories'][0]['items'][0]['name'], 'Tomato Soup')
        self.assertNotIn('Steak', str(r.data))

    def test_public_requires_valid_restaurant(self):
        self.assertEqual(self.client.get('/api/v1/menu').status_code, 400)
        self.assertEqual(self.client.get('/api/v1/menu', {'restaurant': 'x'}).status_code, 400)
        self.assertEqual(self.client.get('/api/v1/menu', {'restaurant': 99999}).status_code, 404)

    def test_logged_in_user_gets_own_restaurant_even_with_other_param(self):
        self.login_as('admin2')
        r = self.client.get('/api/v1/menu', {'restaurant': self.r1.pk})
        self.assertEqual([c['name'] for c in r.data['data']], ['Mains'])

    def test_invalid_token_is_rejected(self):
        r = self.client.get('/api/v1/menu', {'restaurant': self.r1.pk}, HTTP_AUTHORIZATION='Bearer junk')
        self.assertEqual(r.status_code, 401)


class AccessControlTests(MenuTestBase):
    def test_unauthenticated_cannot_use_crud(self):
        for method, url in (('get', CATEGORIES), ('post', CATEGORIES), ('get', ITEMS),
                            ('post', ITEMS), ('delete', f'{ITEMS}/{self.item.pk}'),
                            ('patch', f'{CATEGORIES}/{self.starters.pk}')):
            with self.subTest(method=method, url=url):
                r = getattr(self.client, method)(url, {}, format='json')
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.data['status'], 'error')

    def test_staff_and_kitchen_are_read_only(self):
        for user in ('staff1', 'kitchen1'):
            self.login_as(user)
            with self.subTest(user=user):
                self.assertEqual(self.client.get(CATEGORIES).status_code, 200)
                self.assertEqual(self.client.get(ITEMS).status_code, 200)
                self.assertEqual(self.client.post(CATEGORIES, {'name': 'N'}, format='json').status_code, 403)
                self.assertEqual(self.client.post(ITEMS, self.item_body(name='N'), format='json').status_code, 403)
                self.assertEqual(self.client.patch(f'{ITEMS}/{self.item.pk}', {'price': '1'}, format='json').status_code, 403)
                self.assertEqual(self.client.delete(f'{CATEGORIES}/{self.starters.pk}').status_code, 403)
        self.assertTrue(MenuItem.objects.filter(pk=self.item.pk).exists())

    def test_platform_has_no_access_to_restaurant_menu_crud(self):
        self.login_as('platform1')
        for method, url in (('get', CATEGORIES), ('get', ITEMS), ('post', CATEGORIES),
                            ('get', f'{ITEMS}/{self.item.pk}'),
                            ('delete', f'{ITEMS}/{self.item.pk}')):
            with self.subTest(method=method, url=url):
                r = getattr(self.client, method)(url, {'name': 'N'}, format='json')
                self.assertEqual(r.status_code, 403)
                self.assertEqual(r.data['error']['code'], 'permission_denied')
        self.assertTrue(MenuItem.objects.filter(pk=self.item.pk).exists())

    def test_404_message_does_not_leak_model_names(self):
        self.login_as('admin1')
        r = self.client.get(f'{ITEMS}/99999')
        self.assertEqual(r.data['error']['message'], 'Resource not found.')

    def test_cross_restaurant_access_is_404(self):
        self.login_as('admin2')
        for method, url in (('get', f'{CATEGORIES}/{self.starters.pk}'),
                            ('patch', f'{CATEGORIES}/{self.starters.pk}'),
                            ('delete', f'{CATEGORIES}/{self.starters.pk}'),
                            ('get', f'{ITEMS}/{self.item.pk}'),
                            ('patch', f'{ITEMS}/{self.item.pk}'),
                            ('delete', f'{ITEMS}/{self.item.pk}')):
            with self.subTest(method=method, url=url):
                r = getattr(self.client, method)(url, {'name': 'hack'}, format='json')
                self.assertEqual(r.status_code, 404)
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Tomato Soup')

    def test_cannot_attach_item_to_other_restaurants_category(self):
        self.login_as('admin2')
        r = self.client.post(ITEMS, self.item_body(category=self.starters.pk), format='json')
        self.assertEqual(r.status_code, 400)


class ModelConstraintTests(MenuTestBase):
    def test_db_rejects_negative_price(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            MenuItem.objects.create(category=self.starters, name='Bad', price='-1', dish_type='Veg')

    def test_db_rejects_duplicate_top_level_category(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.create(restaurant=self.r1, name='Starters')
