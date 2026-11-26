from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models import Q


class UserManager(BaseUserManager):
    """Knows how to create users; Django's createsuperuser command calls this."""

    def create_user(self, id, password=None, **extra_fields):
        if not id:
            raise ValueError('Users must have an id.')
        user = self.model(id=id, **extra_fields)
        user.set_password(password)  # hashes the password (PBKDF2 by default)
        # Insert only: ids are chosen by the caller, so a plain save() would UPDATE (and
        # silently take over) an existing account with the same id.
        user.save(using=self._db, force_insert=True)
        return user

    def create_superuser(self, id, password=None, **extra_fields):
        # A superuser is a Platform user: global access, no restaurant.
        extra_fields.setdefault('role', User.Role.PLATFORM)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(id, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """TableFlow user (PDF section 34, `users` table).

    AbstractBaseUser supplies `password` (stored as a hash) and `last_login`.
    PermissionsMixin supplies the fields Django admin needs for permissions.
    """

    class Role(models.TextChoices):
        ADMIN = 'Admin', 'Admin'
        STAFF = 'Staff', 'Staff'
        KITCHEN = 'Kitchen', 'Kitchen'
        PLATFORM = 'Platform', 'Platform'

    id = models.CharField(max_length=50, primary_key=True)
    # Null only for Platform users, who belong to no single restaurant.
    restaurant = models.ForeignKey(
        'restaurants.Restaurant',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='users',
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    # Presence (see accounts.services.PresenceService): `is_online` is set at login and
    # cleared at logout; `last_seen` is refreshed by authenticated API requests. A user
    # only counts as online while BOTH say so (flag set and recent activity).
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True, editable=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # may open Django admin

    USERNAME_FIELD = 'id'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        constraints = [
            # Platform users have no restaurant; everyone else must have one.
            models.CheckConstraint(
                condition=(
                    Q(role='Platform', restaurant__isnull=True)
                    | (~Q(role='Platform') & Q(restaurant__isnull=False))
                ),
                name='user_restaurant_matches_role',
            ),
        ]

    def __str__(self):
        return f'{self.id} ({self.role})'
