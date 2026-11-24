"""Source-first format policy. Delivery failures never choose another format."""

def formats(*, approved_video=False, approved_screenshot=False):
    if approved_video:
        return {'threads': 'VIDEO', 'instagram': 'REELS'}
    if approved_screenshot:
        return {'threads': 'IMAGE'}
    return {'threads': 'TEXT'}


def require_format(platform, media, source_kind):
    import publisher as p
    if platform not in ('threads', 'instagram'):
        return
    required = formats(approved_video=source_kind == 'video', approved_screenshot=source_kind == 'screenshot')
    actual = 'IMAGE' if isinstance(media, p.Image) else ('REELS' if platform == 'instagram' else 'VIDEO') if media else 'TEXT'
    if required.get(platform) != actual:
        raise p.SafeError('Approved source format must be preserved; report the blocked platform without image/text fallback.')


def require_new_video_pair_format(image_mode):
    if image_mode:
        import publisher as p
        raise p.SafeError('Approved video pair requires Threads VIDEO and Instagram REELS; thumbnail fallback is disabled for new approvals.')
