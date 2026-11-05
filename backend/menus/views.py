from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from restaurants.models import Restaurant

from .models import Category, MenuItem
from .permissions import CanManageMenu, CanReadMenu, CanToggleAvailability
from .serializers import (
    AvailabilitySerializer,
    CatalogCategorySerializer,
    CategorySerializer,
    MenuItemSerializer,
)

# --------------------------------------------------------------------------
# Response envelope: { data, error, status }, same as the auth API.
# --------------------------------------------------------------------------

_ERROR_CODES = {
    400: 'validation_error',
    401: 'not_authenticated',
    403: 'permission_denied',
    404: 'not_found',
    405: 'method_not_allowed',
    409: 'conflict',
}


class EnvelopeMixin:
    """Wraps every response of the view in the project's standard envelope."""

    def finalize_response(self, request, response, *args, **kwargs):
        if isinstance(response, Response) and not getattr(response, 'enveloped', False):
            code = response.status_code
            if code >= 400:
                data = response.data
                if code == 400:
                    error = {
                        'code': 'validation_error',
                        'message': 'Invalid request data.',
                        'details': data,
                    }
                else:
                    message = data.get('detail') if isinstance(data, dict) else None
                    if code == 404:
                        message = 'Resource not found.'
                    error = {
                        'code': _ERROR_CODES.get(code, 'error'),
                        'message': str(message or 'Request failed.'),
                    }
                response.data = {'data': None, 'error': error, 'status': 'error'}
            elif code != status.HTTP_204_NO_CONTENT:
                response.data = {'data': response.data, 'error': None, 'status': 'success'}
            response.enveloped = True
        return super().finalize_response(request, response, *args, **kwargs)


def _envelope(name, data=None, many=False):
    """Serializer describing the envelope, for the OpenAPI schema only."""
    data_field = (
        data(many=many) if data is not None else serializers.JSONField(allow_null=True)
    )
    return inline_serializer(name, {
        'data': data_field,
        'error': serializers.JSONField(allow_null=True),
        'status': serializers.CharField(),
    })


_ErrorEnvelope = inline_serializer('MenuErrorEnvelope', {
    'data': serializers.JSONField(allow_null=True),
    'error': inline_serializer('MenuErrorBody', {
        'code': serializers.CharField(),
        'message': serializers.CharField(),
        'details': serializers.JSONField(required=False),
    }),
    'status': serializers.CharField(),
})


def _err(description):
    return OpenApiResponse(response=_ErrorEnvelope, description=description)


_E401 = _err('Missing, invalid or expired JWT access token.')
_E403 = _err('The authenticated role is not allowed to perform this action.')
_E404 = _err('Resource not found (or it belongs to another restaurant).')
_E400 = _err('Validation error; see `error.details` for field messages.')

_AUTH_NOTE = ' Requires a JWT bearer token.'


def _scoped(queryset, user, lookup):
    """Restaurant isolation: users only ever see their own restaurant's rows."""
    return queryset.filter(**{lookup: user.restaurant_id})


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(
        tags=['Menu'], summary='List categories',
        description='List the restaurant\'s categories and sub-categories. '
                    'Roles: Admin, Staff, Kitchen (read-only). Platform has no access.' + _AUTH_NOTE,
        parameters=[OpenApiParameter('parent', int, description='Only sub-categories of this category')],
        responses={200: _envelope('CategoryListEnvelope', CategorySerializer, many=True),
                   401: _E401, 403: _E403},
    ),
    retrieve=extend_schema(
        tags=['Menu'], summary='Retrieve a category',
        description='Roles: Admin, Staff, Kitchen. Platform has no access.' + _AUTH_NOTE,
        responses={200: _envelope('CategoryEnvelope', CategorySerializer),
                   401: _E401, 403: _E403, 404: _E404},
    ),
    create=extend_schema(
        tags=['Menu'], summary='Create a category',
        description='Role: Admin. Provide `parent` to create a sub-category (one level only).'
                    + _AUTH_NOTE,
        request=CategorySerializer,
        responses={201: _envelope('CategoryCreatedEnvelope', CategorySerializer),
                   400: _E400, 401: _E401, 403: _E403},
    ),
    update=extend_schema(
        tags=['Menu'], summary='Replace a category', description='Role: Admin.' + _AUTH_NOTE,
        request=CategorySerializer,
        responses={200: _envelope('CategoryUpdatedEnvelope', CategorySerializer),
                   400: _E400, 401: _E401, 403: _E403, 404: _E404},
    ),
    partial_update=extend_schema(
        tags=['Menu'], summary='Update a category', description='Role: Admin.' + _AUTH_NOTE,
        request=CategorySerializer,
        responses={200: _envelope('CategoryPatchedEnvelope', CategorySerializer),
                   400: _E400, 401: _E401, 403: _E403, 404: _E404},
    ),
    destroy=extend_schema(
        tags=['Menu'], summary='Delete a category',
        description='Role: Admin. Fails with 409 if the category still has sub-categories '
                    'or dishes.' + _AUTH_NOTE,
        responses={204: OpenApiResponse(description='Deleted.'),
                   401: _E401, 403: _E403, 404: _E404,
                   409: _err('Category is still in use.')},
    ),
)
class CategoryViewSet(EnvelopeMixin, viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    permission_classes = [CanManageMenu]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Category.objects.none()
        qs = _scoped(Category.objects.all(), self.request.user, 'restaurant_id')
        parent = self.request.query_params.get('parent')
        if parent and parent.isdigit():
            qs = qs.filter(parent_id=int(parent))
        return qs

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {'detail': 'Category still has sub-categories or dishes; remove or move them first.'},
                status=status.HTTP_409_CONFLICT,
            )


# --------------------------------------------------------------------------
# Food items
# --------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(
        tags=['Menu'], summary='List dishes',
        description='List the restaurant\'s dishes. Roles: Admin, Staff, Kitchen (read-only). '
                    'Platform has no access.' + _AUTH_NOTE,
        parameters=[OpenApiParameter('category', int), OpenApiParameter('sub_category', int),
                    OpenApiParameter('dish_type', str, enum=['Veg', 'Non-Veg']),
                    OpenApiParameter('is_available', bool)],
        responses={200: _envelope('MenuItemListEnvelope', MenuItemSerializer, many=True),
                   401: _E401, 403: _E403},
    ),
    retrieve=extend_schema(
        tags=['Menu'], summary='Retrieve a dish',
        description='Roles: Admin, Staff, Kitchen. Platform has no access.' + _AUTH_NOTE,
        responses={200: _envelope('MenuItemEnvelope', MenuItemSerializer),
                   401: _E401, 403: _E403, 404: _E404},
    ),
    create=extend_schema(
        tags=['Menu'], summary='Create a dish',
        description='Role: Admin. `category` must be a top-level category and `sub_category` '
                    'one of its sub-categories, both in the caller\'s restaurant.' + _AUTH_NOTE,
        request=MenuItemSerializer,
        responses={201: _envelope('MenuItemCreatedEnvelope', MenuItemSerializer),
                   400: _E400, 401: _E401, 403: _E403},
    ),
    update=extend_schema(
        tags=['Menu'], summary='Replace a dish', description='Role: Admin.' + _AUTH_NOTE,
        request=MenuItemSerializer,
        responses={200: _envelope('MenuItemUpdatedEnvelope', MenuItemSerializer),
                   400: _E400, 401: _E401, 403: _E403, 404: _E404},
    ),
    partial_update=extend_schema(
        tags=['Menu'], summary='Update a dish', description='Role: Admin.' + _AUTH_NOTE,
        request=MenuItemSerializer,
        responses={200: _envelope('MenuItemPatchedEnvelope', MenuItemSerializer),
                   400: _E400, 401: _E401, 403: _E403, 404: _E404},
    ),
    destroy=extend_schema(
        tags=['Menu'], summary='Delete a dish', description='Role: Admin.' + _AUTH_NOTE,
        responses={204: OpenApiResponse(description='Deleted.'),
                   401: _E401, 403: _E403, 404: _E404,
                   409: _err('Dish is referenced elsewhere and cannot be deleted.')},
    ),
)
class MenuItemViewSet(EnvelopeMixin, viewsets.ModelViewSet):
    serializer_class = MenuItemSerializer
    permission_classes = [CanManageMenu]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return MenuItem.objects.none()
        params = self.request.query_params
        qs = _scoped(
            MenuItem.objects.select_related('category', 'sub_category'),
            self.request.user, 'category__restaurant_id',
        )
        for param in ('category', 'sub_category'):
            if params.get(param, '').isdigit():
                qs = qs.filter(**{f'{param}_id': int(params[param])})
        if params.get('dish_type') in MenuItem.DishType.values:
            qs = qs.filter(dish_type=params['dish_type'])
        flag = params.get('is_available', '').lower()
        if flag in ('true', 'false'):
            qs = qs.filter(is_available=(flag == 'true'))
        return qs

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {'detail': 'Dish is referenced by other records and cannot be deleted; mark it unavailable instead.'},
                status=status.HTTP_409_CONFLICT,
            )


# --------------------------------------------------------------------------
# Documented endpoints: GET /menu and PATCH /menu/{item_id}/availability
# --------------------------------------------------------------------------

class MenuCatalogView(EnvelopeMixin, APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['Menu'],
        summary='Get the full categorized menu',
        description=(
            '**Public** (no token needed) when `restaurant` is supplied; this is what the '
            'customer QR menu uses. A logged-in Admin/Staff/Kitchen user always gets their '
            'own restaurant\'s menu. Dishes are grouped by category and sub-category and '
            'include `is_available` / `is_orderable` so the UI can grey out unavailable '
            'dishes; unavailable dishes must not be orderable.'
        ),
        parameters=[OpenApiParameter(
            'restaurant', int,
            description='Restaurant ID. Required for anonymous and Platform callers.')],
        responses={
            200: _envelope('MenuCatalogEnvelope', CatalogCategorySerializer, many=True),
            400: _err('`restaurant` is missing or not a number.'),
            401: _E401,
            404: _err('Restaurant not found.'),
        },
    )
    def get(self, request):
        user = request.user
        if user.is_authenticated and user.restaurant_id is not None:
            restaurant_id = user.restaurant_id
        else:
            raw = request.query_params.get('restaurant', '')
            if not raw.isdigit():
                return Response(
                    {'restaurant': ['A numeric restaurant ID is required.']},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            restaurant_id = get_object_or_404(Restaurant, pk=int(raw)).pk

        categories = (
            Category.objects.filter(restaurant_id=restaurant_id, parent__isnull=True)
            .prefetch_related(
                'items__category', 'items__sub_category',
                'sub_categories__sub_category_items__category',
                'sub_categories__sub_category_items__sub_category',
            )
        )
        return Response(CatalogCategorySerializer(categories, many=True).data)


class MenuItemAvailabilityView(EnvelopeMixin, APIView):
    permission_classes = [CanToggleAvailability]

    @extend_schema(
        tags=['Menu'],
        summary='Toggle dish availability',
        description=(
            'Roles: Admin, Staff (Kitchen is read-only; Platform has no access). Body must '
            'contain a real JSON boolean `is_available`. The change is written to the '
            'database immediately and the updated dish is returned. Unavailable dishes have '
            '`is_orderable: false`. This does not push a WebSocket event; clients must '
            're-fetch the menu.' + _AUTH_NOTE
        ),
        request=AvailabilitySerializer,
        responses={
            200: _envelope('MenuItemAvailabilityEnvelope', MenuItemSerializer),
            400: _E400, 401: _E401, 403: _E403, 404: _E404,
        },
    )
    def patch(self, request, item_id):
        # Looked up inside the caller's restaurant: other tenants' IDs give 404.
        item = get_object_or_404(
            MenuItem.objects.select_related('category', 'sub_category'),
            pk=item_id, category__restaurant_id=request.user.restaurant_id,
        )
        serializer = AvailabilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item.is_available = serializer.validated_data['is_available']
        item.save(update_fields=['is_available', 'updated_at'])
        return Response(MenuItemSerializer(item).data)
