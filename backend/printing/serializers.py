from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import PrinterSettings
from .validators import MAX_LENGTH, validate_folder_path


class PrinterSettingsSerializer(serializers.ModelSerializer):
    """Printer configuration of the caller's restaurant."""

    folder_path = serializers.CharField(
        max_length=MAX_LENGTH, allow_blank=True, trim_whitespace=True,
        validators=[validate_folder_path],
        help_text='Absolute folder path ON THE RESTAURANT PC, e.g. `C:\\TableFlow\\KOT`. '
                  'Empty clears the setting.',
    )
    is_configured = serializers.SerializerMethodField()

    class Meta:
        model = PrinterSettings
        fields = ['folder_path', 'is_configured', 'updated_at']
        read_only_fields = ['is_configured', 'updated_at']

    @extend_schema_field(serializers.BooleanField())
    def get_is_configured(self, obj):
        return bool(obj.folder_path)
