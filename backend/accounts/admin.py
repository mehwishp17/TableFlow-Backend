from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from .models import User


class UserCreationForm(BaseUserCreationForm):
    class Meta:
        model = User
        fields = ('id', 'role', 'restaurant')


class UserEditForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Lets you create users with properly hashed passwords from /admin/."""

    form = UserEditForm
    add_form = UserCreationForm
    list_display = ('id', 'role', 'restaurant', 'is_active')
    list_filter = ('role', 'is_active')
    search_fields = ('id',)
    ordering = ('id',)
    fieldsets = (
        (None, {'fields': ('id', 'password')}),
        ('TableFlow', {'fields': ('role', 'restaurant', 'is_online')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser',
                                    'groups', 'user_permissions')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',),
                'fields': ('id', 'role', 'restaurant', 'password1', 'password2')}),
    )
