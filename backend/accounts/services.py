"""Staff management and online-status logic (PDF section 29: service layer)."""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import User


class PresenceService:
    """Online status of users.

    A user is *online* while all of these hold: the account is active, the login flag
    `is_online` is set (set at login, cleared at logout) and `last_seen` is newer than
    `settings.ONLINE_TIMEOUT_SECONDS`. `last_seen` is refreshed by every authenticated
    API request, but at most once per `settings.ACTIVITY_TOUCH_INTERVAL_SECONDS`, so
    polling clients keep a user online without a database write per request.
    """

    @staticmethod
    def cutoff(now=None):
        now = now or timezone.now()
        return now - timedelta(seconds=settings.ONLINE_TIMEOUT_SECONDS)

    @staticmethod
    def mark_online(user, now=None):
        now = now or timezone.now()
        User.objects.filter(pk=user.pk).update(is_online=True, last_seen=now)
        user.is_online, user.last_seen = True, now

    @staticmethod
    def mark_offline(user):
        User.objects.filter(pk=user.pk).update(is_online=False)
        user.is_online = False

    @staticmethod
    def touch(user, now=None):
        """Heartbeat from an authenticated request. Never changes the login flag, so a
        stale token used after logout cannot bring a user back online."""
        now = now or timezone.now()
        interval = timedelta(seconds=settings.ACTIVITY_TOUCH_INTERVAL_SECONDS)
        if user.last_seen is None or now - user.last_seen >= interval:
            User.objects.filter(pk=user.pk).update(last_seen=now)
            user.last_seen = now

    @staticmethod
    def is_online(user, now=None):
        return bool(
            user.is_active and user.is_online and user.last_seen
            and user.last_seen >= PresenceService.cutoff(now)
        )

    @staticmethod
    def online_waiters(restaurant_id, now=None):
        """Staff (waiters) of one restaurant who are online right now."""
        return User.objects.filter(
            restaurant_id=restaurant_id, role=User.Role.STAFF, is_active=True,
            is_online=True, last_seen__gte=PresenceService.cutoff(now),
        ).order_by('id')


class StaffService:
    """Admin-side management of a restaurant's Staff and Kitchen accounts. Admin and
    Platform accounts are never touched here (no privilege escalation)."""

    MANAGED_ROLES = (User.Role.STAFF, User.Role.KITCHEN)

    @staticmethod
    def team_of(admin):
        return User.objects.filter(
            restaurant_id=admin.restaurant_id, role__in=StaffService.MANAGED_ROLES
        ).order_by('id')

    @staticmethod
    def _checked_password(password, user=None):
        try:
            validate_password(password, user)
        except DjangoValidationError as exc:
            raise ValidationError({'password': list(exc.messages)})

    @staticmethod
    def create(admin, user_id, password, role):
        taken = {'id': 'A user with this id already exists.'}
        if User.objects.filter(pk=user_id).exists():
            raise ValidationError(taken)
        StaffService._checked_password(password)
        try:
            with transaction.atomic():
                return User.objects.create_user(
                    user_id, password, role=role, restaurant_id=admin.restaurant_id)
        except IntegrityError:
            # Another request created this id after the check above.
            if User.objects.filter(pk=user_id).exists():
                raise ValidationError(taken)
            raise

    @staticmethod
    def update(user, role=None, is_active=None, password=None, actor=None):
        """Apply an Admin's changes. Only the fields that change are written
        (`update_fields`), so a heartbeat or logout happening at the same moment
        (`last_seen`, `is_online`) can never be overwritten by this save."""
        from kitchen.call_services import CallService  # kitchen depends on accounts

        with transaction.atomic():
            was_waiter = user.role == User.Role.STAFF
            fields = []
            if password is not None:
                StaffService._checked_password(password, user)
                user.set_password(password)
                fields.append('password')
            if role is not None and role != user.role:
                user.role = role
                fields.append('role')
            if is_active is not None and is_active != user.is_active:
                user.is_active = is_active
                fields.append('is_active')
                if not is_active:
                    # A single-column UPDATE, so it cannot overwrite anything else.
                    User.objects.filter(pk=user.pk).update(is_online=False)
                    user.is_online = False
            if fields:
                user.save(update_fields=fields)
            stopped_being_a_waiter = was_waiter and (
                not user.is_active or user.role != User.Role.STAFF)
            if stopped_being_a_waiter:
                CallService.release_waiter_calls(user, actor)
        return user

    @staticmethod
    def deactivate(user, actor=None):
        """Soft delete: the account can no longer log in (its tokens stop working), but
        its orders, calls and score history stay, because they reference it. The calls the
        waiter was holding are released so somebody else can take them."""
        return StaffService.update(user, is_active=False, actor=actor)
