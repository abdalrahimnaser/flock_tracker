import mimetypes
import os

from werkzeug.utils import secure_filename

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', 'sheep')


def ensure_upload_folder():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def r2_configured():
    return all(os.environ.get(key) for key in (
        'R2_BUCKET_NAME',
        'R2_ACCOUNT_ID',
        'R2_ACCESS_KEY_ID',
        'R2_SECRET_ACCESS_KEY',
        'R2_PUBLIC_URL',
    ))


def _get_r2_client():
    import boto3
    from botocore.config import Config

    account_id = os.environ['R2_ACCOUNT_ID']
    return boto3.client(
        's3',
        endpoint_url=f'https://{account_id}.r2.cloudflarestorage.com',
        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
        config=Config(signature_version='s3v4'),
        region_name='auto',
    )


def sheep_photo_key(tag_id, ext):
    safe_tag = secure_filename(tag_id) or 'sheep'
    return f'sheep/{safe_tag}.{ext.lower()}'


def _object_key(stored_value):
    if not stored_value:
        return None
    if stored_value.startswith('sheep/'):
        return stored_value
    return f'sheep/{stored_value}'


def photo_public_url(stored_value):
    if not stored_value:
        return None
    if stored_value.startswith('http://') or stored_value.startswith('https://'):
        return stored_value
    if r2_configured():
        base = os.environ['R2_PUBLIC_URL'].rstrip('/')
        return f'{base}/{_object_key(stored_value)}'
    name = stored_value.split('/')[-1]
    return f'/uploads/sheep/{name}'


def save_sheep_photo(tag_id, file_storage):
    ext = file_storage.filename.rsplit('.', 1)[1].lower()
    data = file_storage.read()
    key = sheep_photo_key(tag_id, ext)

    if r2_configured():
        content_type = mimetypes.guess_type(key)[0] or 'application/octet-stream'
        _get_r2_client().put_object(
            Bucket=os.environ['R2_BUCKET_NAME'],
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return key

    local_name = f'{secure_filename(tag_id) or "sheep"}.{ext}'
    ensure_upload_folder()
    with open(os.path.join(UPLOAD_FOLDER, local_name), 'wb') as handle:
        handle.write(data)
    return local_name


def delete_sheep_photo(stored_value):
    if not stored_value:
        return
    if stored_value.startswith('http://') or stored_value.startswith('https://'):
        return

    if r2_configured():
        key = _object_key(stored_value)
        if key:
            _get_r2_client().delete_object(
                Bucket=os.environ['R2_BUCKET_NAME'],
                Key=key,
            )
        return

    local_name = stored_value.split('/')[-1]
    path = os.path.join(UPLOAD_FOLDER, local_name)
    if os.path.isfile(path):
        os.remove(path)
