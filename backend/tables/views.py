import uuid

from django.db.models import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from drf_spectacular.types import OpenApiTypes
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
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

# Reuse the Menu module's envelope so every API answers in { data, error, status }.
from menus.views import EnvelopeMixin, _E400, _E401, _E403, _E404, _envelope, _err

from .models import Table
from .permissions import CanExportQR, CanManageTables, CanUpdateTableStatus
from .serializers import TableSerializer, TableStatusSerializer
from .services import render_qr_pdf

_AUTH_NOTE = ' Requires a JWT bearer token.'


def _tables_of(user):
    """Restaurant isolation: users only ever see their own restaurant's tables."""
    return Table.objects.filter(restaurant_id=user.restaurant_id)


# --------------------------------------------------------------------------
# Table CRUD
# --------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(
        tags=['Tables'], summary='List tables',
        description='List the restaurant\'s tables. Roles: Admin, Staff (read-only). '
                    'Kitchen and Platform have no access.' + _AUTH_NOTE,
        parameters=[
            OpenApiParameter('status', str, enum=Table.Status.values),
            OpenApiParameter('table_type', str, enum=Table.Type.values),
        ],
        responses={200: _envelope('TableListEnvelope', TableSerializer, many=True),
                   401: _E401, 403: _E403},
    ),
    retrieve=extend_schema(
        tags=['Tables'], summary='Retrieve a table',
        description='Roles: Admin, Staff.' + _AUTH_NOTE,
        responses={200: _envelope('TableEnvelope', TableSerializer),
                   401: _E401, 403: _E403, 404: _E404},
    ),
    create=extend_schema(
        tags=['Tables'], summary='Create a table',
        description='Role: Admin. The restaurant is taken from the logged-in user, never from '
                    'the body. `table_number` is an alphanumeric label (e.g. `T1`, `A-2`, `12`) '
                    'and must be unique (case-insensitive) within the restaurant.' + _AUTH_NOTE,
        request=TableSerializer,
        responses={201: _envelope('TableCreatedEnvelope', TableSerializer),
                   400: _E400, 401: _E401, 403: _E403},
    ),
    partial_update=extend_schema(
        tags=['Tables'], summary='Update table details',
        description='Role: Admin only. Staff must use `PATCH /tables/{id}/status`.' + _AUTH_NOTE,
        request=TableSerializer,
        responses={200: _envelope('TablePatchedEnvelope', TableSerializer),
                   400: _E400, 401: _E401, 403: _E403, 404: _E404},
    ),
    destroy=extend_schema(
        tags=['Tables'], summary='Delete a table', description='Role: Admin.' + _AUTH_NOTE,
        responses={204: OpenApiResponse(description='Deleted.'),
                   401: _E401, 403: _E403, 404: _E404,
                   409: _err('Table is referenced by other records.')},
    ),
)
class TableViewSet(EnvelopeMixin, viewsets.ModelViewSet):
    serializer_class = TableSerializer
    permission_classes = [CanManageTables]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Table.objects.none()
        qs = _tables_of(self.request.user)
        params = self.request.query_params
        if params.get('status') in Table.Status.values:
            qs = qs.filter(status=params['status'])
        if params.get('table_type') in Table.Type.values:
            qs = qs.filter(table_type=params['table_type'])
        return qs

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {'detail': 'Table is referenced by other records and cannot be deleted.'},
                status=status.HTTP_409_CONFLICT,
            )


class TableStatusView(EnvelopeMixin, APIView):
    permission_classes = [CanUpdateTableStatus]

    @extend_schema(
        tags=['Tables'], summary='Update table occupancy status',
        description='Roles: Admin, Staff. Changes only `status` '
                    '(`Available`, `Occupied`, `Reserved`, `Cleaning`); any other field in the '
                    'body is ignored.' + _AUTH_NOTE,
        request=TableStatusSerializer,
        responses={200: _envelope('TableStatusEnvelope', TableSerializer),
                   400: _E400, 401: _E401, 403: _E403, 404: _E404},
    )
    def patch(self, request, pk):
        table = get_object_or_404(_tables_of(request.user), pk=pk)
        serializer = TableStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        table.status = serializer.validated_data['status']
        table.save(update_fields=['status', 'updated_at'])
        return Response(TableSerializer(table, context={'request': request}).data)


# --------------------------------------------------------------------------
# QR PDF export
# --------------------------------------------------------------------------

class TableQRPDFView(EnvelopeMixin, APIView):
    permission_classes = [CanExportQR]

    @extend_schema(
        tags=['Tables'], summary='Download the QR codes of all tables as an A4 PDF',
        description='Role: Admin only. Printable A4 PDF (6 cards per page) with restaurant name, '
                    'table label, type, capacity and a QR code that opens '
                    '`/menu?restaurant_id={restaurant_id}&table={table_id}&token={qr_token}` on '
                    'the frontend; `token` lets the frontend verify the scan through '
                    '`GET /api/v1/qr/resolve/`. Only the caller\'s own restaurant is exported. '
                    'With no tables, a one-page PDF saying "No tables available" is returned.'
                    + _AUTH_NOTE,
        responses={
            (200, 'application/pdf'): OpenApiTypes.BINARY,
            401: _E401, 403: _E403,
        },
    )
    def get(self, request):
        restaurant = request.user.restaurant
        tables = list(_tables_of(request.user).order_by('table_number'))
        pdf = render_qr_pdf(restaurant, tables)
        filename = f'tables-qr-{slugify(restaurant.name) or restaurant.pk}.pdf'
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


# --------------------------------------------------------------------------
# Public QR resolver
# --------------------------------------------------------------------------

class QRResolveThrottle(AnonRateThrottle):
    """Limits token guessing; the rate is set in settings (`qr_resolve`)."""

    scope = 'qr_resolve'


_Resolved = inline_serializer('QRResolved', {
    'restaurant_id': serializers.IntegerField(),
    'restaurant_name': serializers.CharField(),
    'table_id': serializers.IntegerField(),
    'table_number': serializers.CharField(),
})


class QRResolveView(EnvelopeMixin, APIView):
    # Public, but only this view: no authentication is attempted, so a stale or
    # bogus Authorization header cannot turn a public lookup into a 401.
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [QRResolveThrottle]

    @extend_schema(
        tags=['Tables'], summary='Resolve a table QR token (public)',
        description='**Public** (no token needed). Resolves a QR token into the restaurant and '
                    'table it belongs to. Returns only public identifiers; the token itself, '
                    'users and internal data are never exposed. Invalid, malformed and unknown '
                    'tokens all return the same 404. Rate limited.',
        parameters=[OpenApiParameter('token', OpenApiTypes.UUID, required=True)],
        responses={200: inline_serializer('QRResolveEnvelope', {
                       'data': _Resolved,
                       'error': serializers.JSONField(allow_null=True),
                       'status': serializers.CharField(),
                   }),
                   404: _err('Unknown or invalid token.'),
                   429: _err('Too many requests.')},
    )
    def get(self, request):
        raw = request.query_params.get('token', '')
        try:
            token = uuid.UUID(raw)
        except ValueError:
            token = None
        table = (
            Table.objects.select_related('restaurant').filter(qr_token=token).first()
            if token else None
        )
        if table is None:
            return Response({'detail': 'QR code not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'restaurant_id': table.restaurant_id,
            'restaurant_name': table.restaurant.name,
            'table_id': table.pk,
            'table_number': table.table_number,
        })
