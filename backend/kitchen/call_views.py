from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from menus.views import _E400, _E401, _E403, _E404, EnvelopeMixin, _envelope, _err

from .call_permissions import CanResolveCall, CanUseCalls, IsCallAdmin, IsWaiter
from .call_serializers import (
    CallFilterSerializer,
    CallSerializer,
    CustomerCallResultSerializer,
    CustomerCallSerializer,
    KitchenCallSerializer,
)
from .call_services import CallService

_AUTH_NOTE = ' Requires a JWT bearer token.'
_E409 = _err('The call is not in a state that allows this change (invalid transition).')
_LIFECYCLE = (
    ' Lifecycle: `PENDING` -> `PICKED` -> `RESOLVED`; it only moves forward. The Admin can also '
    '`CANCELLED` an open call.'
)


class CallListCreateView(EnvelopeMixin, APIView):
    permission_classes = [CanUseCalls]

    @extend_schema(
        tags=['Calls'], summary='List call alerts',
        description='Roles and what they see: **Staff** the red-flagged `PENDING` alerts of '
                    'their restaurant that any waiter may take, plus kitchen calls addressed '
                    'to them (`status=PICKED` / `RESOLVED`: the ones they picked); **Kitchen** '
                    'the calls they made; **Admin** every call of the restaurant. Never '
                    'another restaurant\'s. Oldest alert first. Poll this every few seconds '
                    '(it also keeps the waiter "online").' + _LIFECYCLE + _AUTH_NOTE,
        parameters=[CallFilterSerializer],
        responses={200: _envelope('CallListEnvelope', CallSerializer, many=True),
                   400: _E400, 401: _E401, 403: _E403},
    )
    def get(self, request):
        filters = CallFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        calls = CallService.list_calls(request.user, filters.validated_data['status'])
        return Response(CallSerializer(calls, many=True).data)

    @extend_schema(
        tags=['Calls'], summary='Kitchen: call an online waiter',
        description='Role: **Kitchen** only. Creates a `PENDING` alert addressed to one '
                    'waiter, chosen from `GET /api/v1/staff/online/`. The waiter must be '
                    'online and belong to the kitchen\'s own restaurant; the `order` and '
                    '`table` too. Only that waiter can pick the alert.' + _LIFECYCLE
                    + _AUTH_NOTE,
        request=KitchenCallSerializer,
        responses={201: _envelope('KitchenCallEnvelope', CallSerializer),
                   400: _E400, 401: _E401, 403: _E403},
    )
    def post(self, request):
        serializer = KitchenCallSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        call = CallService.create_kitchen_call(
            request.user, data['waiter'], data.get('order'), data.get('table'))
        return Response(CallSerializer(call).data, status=status.HTTP_201_CREATED)


class CustomerCallThrottle(AnonRateThrottle):
    """Limits abuse of the public route; the rate is `customer_call` in settings
    (30/min per IP, like the QR resolver; guests on one Wi-Fi share an IP)."""

    scope = 'customer_call'


class CustomerCallView(EnvelopeMixin, APIView):
    # Public, and only this view: no authentication is attempted, so a stale or bogus
    # Authorization header cannot turn a guest's request into a 401.
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [CustomerCallThrottle]

    @extend_schema(
        tags=['Calls'], summary='Customer: call a waiter from the table QR page (public)',
        description='**Public** (no token). The guest\'s QR link carries `restaurant_id`, '
                    '`table` and `token`; send the `token` (the other two are optional but, '
                    'if sent, must match it). Creates a red-flagged `PENDING` alert for the '
                    'table\'s restaurant: **201**. If that table already has an open customer '
                    'call, no second one is created and the open call is returned: **200**. '
                    'Unknown, malformed and mismatching tokens all return the same 404, so '
                    'another restaurant\'s table can never be targeted. Rate limited. Only '
                    'public values are returned (no staff ids).' + _LIFECYCLE,
        request=CustomerCallSerializer,
        responses={201: _envelope('CustomerCallCreatedEnvelope', CustomerCallResultSerializer),
                   200: _envelope('CustomerCallOpenEnvelope', CustomerCallResultSerializer),
                   400: _E400, 404: _err('Unknown or mismatching QR token.'),
                   429: _err('Too many requests.')},
    )
    def post(self, request):
        serializer = CustomerCallSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        call, created = CallService.create_customer_call(
            data['token'], data.get('restaurant_id'), data.get('table'))
        body = {'id': call.pk, 'status': call.status,
                'table_number': call.table.table_number, 'triggered_at': call.triggered_at}
        return Response(
            CustomerCallResultSerializer(body).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


_ACTION_DOC = (
    ' The waiter is always the authenticated caller; any `assigned_waiter` or other field in '
    'the body is ignored. 404 for a call of another restaurant, 403 if it belongs to another '
    'waiter, 409 for an invalid transition.'
)


class CallPickView(EnvelopeMixin, APIView):
    permission_classes = [IsWaiter]

    @extend_schema(
        tags=['Calls'], summary='Waiter: pick a call (PENDING -> PICKED)',
        description='Role: **Staff** only. Assigns the call to the caller and records '
                    '`picked_at`. The call leaves the pending queue (`GET /calls/` default) '
                    'and appears under `status=PICKED` for that waiter. A kitchen call '
                    'addressed to another waiter gives 403.' + _ACTION_DOC + _AUTH_NOTE,
        request=None,
        responses={200: _envelope('CallPickedEnvelope', CallSerializer),
                   401: _E401, 403: _E403, 404: _E404, 409: _E409},
    )
    def patch(self, request, pk):
        return Response(CallSerializer(CallService.pick(request.user, pk)).data)


class CallResolveView(EnvelopeMixin, APIView):
    permission_classes = [CanResolveCall]

    @extend_schema(
        tags=['Calls'], summary='Resolve a call (PICKED -> RESOLVED)',
        description='Roles: **Staff**, only the waiter who picked the call: records '
                    '`resolved_at` and credits the waiter\'s score exactly once (+5, and +5 '
                    'more if the call was picked within 2 minutes of `triggered_at`). '
                    '**Admin**: may close any `PICKED` call of their own restaurant on the '
                    'waiter\'s behalf (for example when the waiter went home); this records '
                    '`closed_by` and earns **no** score. A call that is still `PENDING` '
                    'cannot be resolved (the Admin uses cancel). A second resolve gives 409 '
                    'and credits nothing.' + _ACTION_DOC + _AUTH_NOTE,
        request=None,
        responses={200: _envelope('CallResolvedEnvelope', CallSerializer),
                   401: _E401, 403: _E403, 404: _E404, 409: _E409},
    )
    def patch(self, request, pk):
        return Response(CallSerializer(CallService.resolve(request.user, pk)).data)


class CallCancelView(EnvelopeMixin, APIView):
    permission_classes = [IsCallAdmin]

    @extend_schema(
        tags=['Calls'], summary='Admin: cancel an open call (PENDING or PICKED -> CANCELLED)',
        description='Role: **Admin** only, for calls of their own restaurant (404 for another '
                    'restaurant\'s). Records `cancelled_at` and `closed_by`. Nobody is '
                    'credited, and the table can call again. Use it for false alarms or '
                    'calls nobody is going to answer. A call that is already `RESOLVED` or '
                    '`CANCELLED` gives 409. When a waiter is deactivated their calls are '
                    'released automatically (see `PATCH /staff/{id}/`).' + _AUTH_NOTE,
        request=None,
        responses={200: _envelope('CallCancelledEnvelope', CallSerializer),
                   401: _E401, 403: _E403, 404: _E404, 409: _E409},
    )
    def patch(self, request, pk):
        return Response(CallSerializer(CallService.cancel(request.user, pk)).data)
