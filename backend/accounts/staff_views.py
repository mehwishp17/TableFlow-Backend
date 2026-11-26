from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

# Reuse the Menu module's envelope so every API answers in { data, error, status }.
from menus.views import _E400, _E401, _E403, _E404, EnvelopeMixin, _envelope

from .permissions import CanSeeOnlineStaff, IsAdminRole
from .services import PresenceService, StaffService
from .staff_serializers import (
    StaffCreateSerializer,
    StaffSerializer,
    StaffUpdateSerializer,
)

_AUTH_NOTE = ' Requires a JWT bearer token.'
_TEAM_NOTE = (
    ' The "staff" of a restaurant are its **Staff** (waiter) and **Kitchen** accounts; Admin '
    'and Platform accounts are never listed or changed here.'
)


class StaffListCreateView(EnvelopeMixin, APIView):
    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=['Staff'], summary='List the restaurant\'s staff accounts',
        operation_id='staff_list',
        description='Role: **Admin** only. Lists the Staff and Kitchen accounts of the '
                    'Admin\'s own restaurant, never another restaurant\'s. `is_online` is true '
                    'while the user is logged in and recently active (see the README, '
                    '"Online status").' + _TEAM_NOTE + _AUTH_NOTE,
        parameters=[
            OpenApiParameter('role', enum=['Staff', 'Kitchen'], required=False),
            OpenApiParameter('is_active', bool, required=False),
        ],
        responses={200: _envelope('StaffListEnvelope', StaffSerializer, many=True),
                   401: _E401, 403: _E403},
    )
    def get(self, request):
        team = StaffService.team_of(request.user)
        role = request.query_params.get('role')
        if role in (r.value for r in StaffService.MANAGED_ROLES):
            team = team.filter(role=role)
        active = request.query_params.get('is_active')
        if active in ('true', 'false'):
            team = team.filter(is_active=(active == 'true'))
        return Response(StaffSerializer(team, many=True).data)

    @extend_schema(
        tags=['Staff'], summary='Create a staff account',
        description='Role: **Admin** only. Creates a Staff (waiter) or Kitchen account in the '
                    'Admin\'s own restaurant (`role` defaults to Staff). The password is '
                    'checked against the project password rules, stored hashed and never '
                    'returned.' + _AUTH_NOTE,
        request=StaffCreateSerializer,
        responses={201: _envelope('StaffCreatedEnvelope', StaffSerializer),
                   400: _E400, 401: _E401, 403: _E403},
    )
    def post(self, request):
        serializer = StaffCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = StaffService.create(request.user, data['id'], data['password'], data['role'])
        return Response(StaffSerializer(user).data, status=status.HTTP_201_CREATED)


class StaffDetailView(EnvelopeMixin, APIView):
    permission_classes = [IsAdminRole]

    def _get(self, request, pk):
        return get_object_or_404(StaffService.team_of(request.user), pk=pk)

    @extend_schema(
        tags=['Staff'], summary='Retrieve a staff account',
        description='Role: **Admin** only. 404 for an id of another restaurant or of an '
                    'Admin/Platform account.' + _AUTH_NOTE,
        responses={200: _envelope('StaffDetailEnvelope', StaffSerializer),
                   401: _E401, 403: _E403, 404: _E404},
    )
    def get(self, request, pk):
        return Response(StaffSerializer(self._get(request, pk)).data)

    @extend_schema(
        tags=['Staff'], summary='Update a staff account',
        description='Role: **Admin** only. Send any of `role` (Staff/Kitchen), `is_active` '
                    '(false deactivates, true reactivates) and `password` (reset). The id '
                    'cannot be changed. Deactivating a waiter (or changing their role away from Staff) '
                    'releases the calls they were holding: customer calls they had picked go back to '
                    '`PENDING` for another waiter, kitchen calls addressed to them are '
                    '`CANCELLED`.' + _AUTH_NOTE,
        request=StaffUpdateSerializer,
        responses={200: _envelope('StaffUpdatedEnvelope', StaffSerializer),
                   400: _E400, 401: _E401, 403: _E403, 404: _E404},
    )
    def patch(self, request, pk):
        user = self._get(request, pk)
        serializer = StaffUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = StaffService.update(user, actor=request.user, **serializer.validated_data)
        return Response(StaffSerializer(user).data)

    @extend_schema(
        tags=['Staff'], summary='Deactivate a staff account',
        description='Role: **Admin** only. This is a **soft delete**: the account is '
                    'deactivated (it can no longer log in and its tokens stop working) but '
                    'kept, because its orders, calls and score history reference it. '
                    'Repeating the call is harmless. Reactivate with `PATCH is_active=true`.'
                    + _AUTH_NOTE,
        responses={200: _envelope('StaffDeactivatedEnvelope', StaffSerializer),
                   401: _E401, 403: _E403, 404: _E404},
    )
    def delete(self, request, pk):
        user = StaffService.deactivate(self._get(request, pk), actor=request.user)
        return Response(StaffSerializer(user).data)


class OnlineStaffView(EnvelopeMixin, APIView):
    permission_classes = [CanSeeOnlineStaff]

    @extend_schema(
        tags=['Staff'], summary='List online waiters',
        description='Roles: **Admin** and **Kitchen** (the KDS "Call Online Staff" dropdown). '
                    'Returns only active **Staff** (waiter) accounts of the caller\'s own '
                    'restaurant that are online: logged in and seen within '
                    '`ONLINE_TIMEOUT_SECONDS` (default 120 s). Any authenticated API request '
                    'counts as activity.' + _AUTH_NOTE,
        responses={200: _envelope('OnlineStaffEnvelope', StaffSerializer, many=True),
                   401: _E401, 403: _E403},
    )
    def get(self, request):
        waiters = PresenceService.online_waiters(request.user.restaurant_id)
        return Response(StaffSerializer(waiters, many=True).data)

