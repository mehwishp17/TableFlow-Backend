"""Waiter call alerts: business logic (PDF section 16 and 29)."""
import uuid

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from accounts.models import User
from accounts.services import PresenceService
from analytics.services import ScoreService
from orders.models import Order
from tables.models import Table

from .models import StaffCall

Status = StaffCall.Status


class Conflict(APIException):
    """409: the call is not in a state that allows the requested change."""

    status_code = 409
    default_detail = 'The call is not in a state that allows this change.'
    default_code = 'conflict'


class CallService:
    # ------------------------------------------------------------------
    # Creating calls
    # ------------------------------------------------------------------

    @staticmethod
    def create_customer_call(token, restaurant_id=None, table_id=None):
        """A guest pressed "call waiter" on the table's QR page. Returns (call, created).

        The QR `token` alone identifies the table (and so the restaurant). `restaurant_id`
        and `table_id`, the other values of the QR link, are optional but must agree with
        the token; any mismatch looks exactly like an unknown token (same 404), so nothing
        can be learned about other restaurants' tables. A table with an open customer call
        gets no second one: the open call is returned instead."""
        try:
            token = uuid.UUID(str(token))
        except ValueError:
            token = None
        table = Table.objects.filter(qr_token=token).first() if token else None
        if (table is None
                or (restaurant_id is not None and table.restaurant_id != restaurant_id)
                or (table_id is not None and table.pk != table_id)):
            raise NotFound('QR code not found.')

        open_calls = StaffCall.objects.filter(
            table=table, source=StaffCall.Source.CUSTOMER,
            status__in=[Status.PENDING, Status.PICKED])
        existing = open_calls.first()
        if existing:
            return existing, False
        try:
            with transaction.atomic():
                call = StaffCall.objects.create(
                    restaurant_id=table.restaurant_id, table=table,
                    source=StaffCall.Source.CUSTOMER)
        except IntegrityError:
            # Two guests pressed at the same moment: the unique constraint let one win.
            existing = open_calls.first()
            if existing is None:
                raise
            return existing, False
        return call, True

    @staticmethod
    def create_kitchen_call(user, waiter_id, order_id=None, table_id=None):
        """The kitchen alerts one online waiter of its own restaurant."""
        restaurant_id = user.restaurant_id
        waiter = PresenceService.online_waiters(restaurant_id).filter(pk=waiter_id).first()
        if waiter is None:
            # Same message for "unknown", "other restaurant", "not a waiter" and "offline".
            raise ValidationError({'waiter': 'Select an online waiter of your restaurant.'})

        order = table = None
        if order_id:
            order = Order.objects.select_related('table').filter(
                pk=order_id, restaurant_id=restaurant_id).first()
            if order is None:
                raise ValidationError({'order': 'Order not found in your restaurant.'})
            table = order.table
        if table_id:
            chosen = Table.objects.filter(pk=table_id, restaurant_id=restaurant_id).first()
            if chosen is None:
                raise ValidationError({'table': 'Table not found in your restaurant.'})
            if order is not None and order.table_id not in (None, chosen.pk):
                raise ValidationError({'table': 'The table does not belong to that order.'})
            table = chosen
        return StaffCall.objects.create(
            restaurant_id=restaurant_id, table=table, order=order,
            source=StaffCall.Source.KITCHEN, created_by=user, target_waiter=waiter)

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    @staticmethod
    def list_calls(user, status='PENDING'):
        """Calls a user may see, always inside their own restaurant.

        * Staff: PENDING = the red-flagged alerts waiting for a waiter (customer calls and
          kitchen calls addressed to them). PICKED / RESOLVED = only the ones they picked.
        * Kitchen: only the calls they made.   * Admin: every call of the restaurant.
        CANCELLED calls are only ever listed on request (`status=CANCELLED` or `all`)."""
        qs = StaffCall.objects.filter(restaurant_id=user.restaurant_id)
        if user.role == User.Role.KITCHEN:
            qs = qs.filter(created_by=user)
        elif user.role == User.Role.STAFF:
            waiting = Q(status=Status.PENDING) & (
                Q(target_waiter__isnull=True) | Q(target_waiter=user))
            mine = Q(status__in=[Status.PICKED, Status.RESOLVED, Status.CANCELLED],
                     assigned_waiter=user)
            qs = qs.filter(waiting | mine)

        if status != 'all':
            qs = qs.filter(status=status)
        # The longest-waiting alert first; history newest first.
        qs = qs.select_related('table')
        return qs.order_by('triggered_at' if status == Status.PENDING else '-triggered_at', 'id')

    # ------------------------------------------------------------------
    # Status changes: PENDING -> PICKED -> RESOLVED
    # ------------------------------------------------------------------

    @staticmethod
    def _locked(user, call_id):
        """The call, row-locked, only if it belongs to the user's restaurant (else 404)."""
        return get_object_or_404(
            StaffCall.objects.select_for_update(of=('self',)).select_related('table'),
            pk=call_id, restaurant_id=user.restaurant_id)

    @staticmethod
    def pick(user, call_id):
        """The authenticated waiter takes a PENDING call. The assignee is always the
        caller; nothing in the request can name another waiter."""
        with transaction.atomic():
            call = CallService._locked(user, call_id)
            if call.target_waiter_id is not None and call.target_waiter_id != user.pk:
                raise PermissionDenied('This call is addressed to another waiter.')
            if call.status != Status.PENDING:
                raise Conflict(f'The call is already {call.status}; only a PENDING call '
                               f'can be picked.')
            call.status = Status.PICKED
            call.assigned_waiter = user
            call.picked_at = timezone.now()
            call.save(update_fields=['status', 'assigned_waiter', 'picked_at'])
        return call

    @staticmethod
    def resolve(user, call_id):
        """Close a PICKED call.

        * The waiter who picked it resolves it and is credited exactly once.
        * The restaurant Admin may close any PICKED call of their restaurant on the
          waiter's behalf (e.g. the waiter went home). That earns NO score: the waiter
          did not resolve it. `closed_by` records who did."""
        is_admin = user.role == User.Role.ADMIN
        with transaction.atomic():
            call = CallService._locked(user, call_id)
            if call.status == Status.CANCELLED:
                raise Conflict('The call is already CANCELLED.')
            if call.status == Status.PENDING:
                raise Conflict('The call must be picked before it can be resolved.'
                               + (' Use cancel to close an unpicked call.' if is_admin else ''))
            if not is_admin and call.assigned_waiter_id != user.pk:
                raise PermissionDenied('This call was picked by another waiter.')
            if call.status == Status.RESOLVED:
                raise Conflict('The call is already RESOLVED.')
            call.status = Status.RESOLVED
            call.resolved_at = timezone.now()
            fields = ['status', 'resolved_at']
            if is_admin:
                call.closed_by = user
                fields.append('closed_by')
            call.save(update_fields=fields)
            if not is_admin:
                # Same transaction as the status change: both happen or neither does.
                ScoreService.credit_resolved_call(call)
        return call

    @staticmethod
    def cancel(admin, call_id):
        """The restaurant Admin cancels an open (PENDING or PICKED) call of their own
        restaurant. Nobody is credited, and the table may call again."""
        with transaction.atomic():
            call = CallService._locked(admin, call_id)
            if call.status not in (Status.PENDING, Status.PICKED):
                raise Conflict(f'The call is already {call.status}; only an open call '
                               f'can be cancelled.')
            call.status = Status.CANCELLED
            call.cancelled_at = timezone.now()
            call.closed_by = admin
            call.save(update_fields=['status', 'cancelled_at', 'closed_by'])
        return call

    @staticmethod
    def release_waiter_calls(waiter, actor):
        """A waiter was deactivated (or is no longer a waiter): free the calls they hold.

        * Customer calls they had PICKED go back to PENDING, so another waiter can take
          them (the table stays unblocked).
        * Kitchen calls addressed to them, PENDING or PICKED, are CANCELLED: only that
          waiter could ever take them, so the kitchen has to call someone else.
        Nothing is credited. Runs inside the caller's transaction, scoped to the waiter's
        own restaurant."""
        mine = StaffCall.objects.filter(restaurant_id=waiter.restaurant_id)
        mine.filter(
            source=StaffCall.Source.CUSTOMER, status=Status.PICKED, assigned_waiter=waiter,
        ).update(status=Status.PENDING, assigned_waiter=None, picked_at=None)
        mine.filter(
            source=StaffCall.Source.KITCHEN, target_waiter=waiter,
            status__in=[Status.PENDING, Status.PICKED],
        ).update(status=Status.CANCELLED, cancelled_at=timezone.now(), closed_by=actor)
