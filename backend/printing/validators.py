import re

from django.core.exceptions import ValidationError

MAX_LENGTH = 260  # Windows MAX_PATH

_DRIVE = re.compile(r'^[A-Za-z]:[\\/]')          # C:\Orders  or  C:/Orders
_UNC = re.compile(r'^\\\\[^\\/]+[\\/][^\\/]+')   # \\server\share
_ILLEGAL_CHARS = set('<>"|?*')                   # illegal in Windows names, never wanted here
_EXAMPLES = 'C:\\TableFlow\\KOT, \\\\server\\share\\KOT or /var/spool/tableflow'


def validate_folder_path(value):
    """Checks that `value` is a well-formed ABSOLUTE folder path.

    Only the *format* is checked. The path is on the restaurant's own computer, which the
    cloud server cannot see, so whether the folder exists is for `print.py` to find out.
    An empty value is allowed and means "not configured". Accepted: Windows drive paths,
    UNC network paths and POSIX absolute paths. Rejected: relative paths, `..`, control
    characters, `< > " | ? *`, a `:` outside the drive letter, and names ending in a space
    or dot (Windows silently changes those)."""
    if value == '':
        return value
    if len(value) > MAX_LENGTH:
        raise ValidationError(f'The path is longer than {MAX_LENGTH} characters.')
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValidationError('The path contains control characters.')

    if _DRIVE.match(value):
        rest = value[2:]
    elif _UNC.match(value) or value.startswith('/'):
        rest = value
    else:
        raise ValidationError(f'Enter an absolute folder path, e.g. {_EXAMPLES}.')

    if ':' in rest or any(ch in _ILLEGAL_CHARS for ch in rest):
        raise ValidationError('The path contains characters that are not allowed '
                              '(< > " | ? * or a stray ":").')
    segments = [seg for seg in re.split(r'[\\/]+', value) if seg]
    if '..' in segments:
        raise ValidationError('The path must not contain "..".')
    if any(seg != seg.rstrip(' .') for seg in segments):
        raise ValidationError('A folder name must not end with a space or a dot.')
    return value
