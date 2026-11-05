from django.contrib import admin

from .models import Category, MenuItem


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'restaurant', 'parent', 'updated_at')
    list_filter = ('restaurant',)
    search_fields = ('name',)


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'price', 'dish_type', 'is_available')
    list_filter = ('dish_type', 'is_available', 'category__restaurant')
    search_fields = ('name',)
