"""Exact local artwork routes; no general-purpose file serving."""
from .common import AppError

IMAGE_ROUTES={
    '/assets/silver-route-map.png':('silver-route-map.png','image/png'),
    '/assets/silver-route-map.webp':('silver-route-map.webp','image/webp'),
    '/assets/overseas-sail.png':('overseas-sail.png','image/png'),
    '/assets/nav-development.png':('nav-development.png','image/png'),
    '/assets/nav-deals.png':('nav-deals.png','image/png'),
    '/assets/nav-settings.png':('nav-settings.png','image/png'),
    '/assets/compass.png':('compass.png','image/png'),
    '/assets/precision-fixture.png':('precision-fixture.png','image/png'),
    '/assets/row-arrow.png':('row-arrow.png','image/png'),
}
MAX_IMAGE_BYTES=5*1024*1024

def image_asset(root,path):
    if path not in IMAGE_ROUTES:raise AppError('图片资源不存在',404,'not_found')
    filename,mime=IMAGE_ROUTES[path]
    directory=root.resolve()/'static'/'assets'
    candidate=directory/filename
    try:
        resolved=candidate.resolve(strict=True)
        resolved.relative_to(directory)
        if not resolved.is_file():raise ValueError()
        if resolved.stat().st_size>MAX_IMAGE_BYTES:raise AppError('图片资源超过5MiB',413,'asset_too_large')
        with resolved.open('rb') as handle:body=handle.read(MAX_IMAGE_BYTES+1)
    except (OSError,ValueError):raise AppError('图片资源不存在',404,'not_found')
    if len(body)>MAX_IMAGE_BYTES:raise AppError('图片资源超过5MiB',413,'asset_too_large')
    valid=body.startswith(b'\x89PNG\r\n\x1a\n') if mime=='image/png' else len(body)>=12 and body[:4]==b'RIFF' and body[8:12]==b'WEBP'
    if not valid:raise AppError('图片资源类型与路径不符',415,'asset_type_invalid')
    return body,mime
