"""Separate local container inspection from availability of original-byte delivery."""
import struct
import os
import stat
import hashlib
import time
import urllib.parse

import publisher as p


def container_checks(path):
    """Bounded ISO BMFF walk; never scan compressed payload for box-name strings."""
    top=[]; edits=False; count=0
    fd=os.open(path,os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as source:
        before=os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):raise ValueError()
        size=before.st_size
        def walk(start,end,depth=0):
            nonlocal edits,count
            if depth>8:raise ValueError()
            pos=start
            while pos<end:
                count+=1
                if count>10000 or end-pos<8:raise ValueError()
                source.seek(pos);length,kind=struct.unpack('>I4s',source.read(8));header=8
                if length==1:
                    if end-pos<16:raise ValueError()
                    length=struct.unpack('>Q',source.read(8))[0];header=16
                elif length==0:length=end-pos
                if length<header or pos+length>end:raise ValueError()
                if depth==0:top.append(kind)
                if kind in (b'edts',b'elst'):edits=True
                if kind in (b'moov',b'trak',b'edts'):walk(pos+header,pos+length,depth+1)
                pos+=length
        walk(0,size)
        after=os.fstat(source.fileno())
        if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError()
    if top.count(b'moov')!=1 or b'mdat' not in top or b'ftyp' not in top:raise ValueError()
    return {'no_edit_lists':not edits,'moov_before_media':top.index(b'moov')<top.index(b'mdat')}


def inspect(platform,video,upload_mode=None):
    reasons=[];checks={}
    try:checks=container_checks(video.path)
    except (OSError,ValueError,struct.error):reasons.append('mp4_container_unverified')
    if checks.get('no_edit_lists') is False:reasons.append('mp4_edit_list_present')
    if checks.get('moov_before_media') is False:reasons.append('mp4_moov_after_media')
    if video.preflight!='verified_local_profile':reasons.append('local_codec_profile_unverified')
    local_reasons=list(reasons)
    direct=platform=='instagram' and upload_mode=='resumable' and video.url is None
    route_reason=None if direct else 'https_original_approval_and_bytes_unverified' if video.url else 'official_original_transfer_path_missing'
    if route_reason:reasons.append(route_reason)
    return {'platform':platform,'media_type':'REELS' if platform=='instagram' else 'VIDEO',
            'local':{'status':'blocked' if local_reasons else 'checked_operating_profile',
                     'reasons':local_reasons,'container_checks':checks,
                     'codec_checks':'existing MP4/H264/yuv420p, 23-60fps, optional AAC <=48kHz mono/stereo, 30-150s',
                     'not_certified':['closed_GOP','VBR_mode','128kbps_audio_target','provider_acceptance']},
            'delivery':{'status':'local_original_upload_available' if direct else 'unverified_https_original' if video.url else 'blocked',
                        'remote_copy_verified':False,'bytes_sent':False,
                        'reason':route_reason,
                        'message':'공식 원본 전송 경로 없음' if route_reason=='official_original_transfer_path_missing' else None},
            'blocking_reasons':reasons,'publish_ready':False}


def require_new(platform,video,upload_mode=None,*,account_id=None,job_id=None,text=None):
    if platform not in ('threads','instagram') or not isinstance(video,p.Video):return
    result=inspect(platform,video,upload_mode)
    reasons=result['local']['reasons']
    if reasons:raise p.SafeError('Video preflight blocked: '+','.join(reasons))
    video.verify_for_publish()
    if platform=='instagram' and upload_mode=='resumable' and video.url is None:return
    if not video.url:
        raise p.SafeError('official_original_transfer_path_missing (공식 원본 전송 경로 없음)')
    # One private operator approval for this account, job, text and final original.
    # A HTTPS string alone or another account's approval grants no delivery scope.
    import publication_profile as profiles
    import candidate_review as review
    import delivery_binding as delivery
    try:
        approval=profiles.read_private(os.environ.get('MORU_VIDEO_URL_APPROVAL'))
        review.keys(approval,'version platform account_id job_id media_sha256 url_sha256 content_sha256 approval_record final_original expires_at')
        review.require(type(approval['version']) is int and approval['version']==1 and approval['final_original'] is True)
        review.require((approval['platform'],approval['account_id'],approval['job_id'])==(platform,account_id,job_id))
        review.identifier(approval['approval_record'])
        review.require(approval['media_sha256']==video.sha256 and approval['url_sha256']==review.digest(video.url) and approval['content_sha256']==p.content_hash(text,video))
        review.require(type(approval['expires_at']) in (int,float) and time.time()<approval['expires_at']<=time.time()+1800)
    except Exception:
        raise p.SafeError('HTTPS original account/candidate approval missing, expired or mismatched.') from None
    p.public_video_url(video.url)
    host=urllib.parse.urlsplit(video.url).hostname
    delivery.remote_bytes(video.url,video.size,video.sha256,approved_host=host,
                          maximum_size=300_000_000 if platform=='instagram' else p.MAX_VIDEO_BYTES)
    video.verify_for_publish()
    if time.time()>=approval['expires_at']:raise p.SafeError('HTTPS original approval expired during verification.')
    return (platform,account_id,job_id,p.content_hash(text,video),video.sha256,review.digest(video.url),approval['expires_at'])


def check_handoff(proof,platform,account_id,job_id,text,video):
    expected=(platform,account_id,job_id,p.content_hash(text,video),video.sha256,hashlib.sha256(video.url.encode()).hexdigest())
    if tuple(proof[:6])!=expected or time.time()>=proof[6]:
        raise p.SafeError('HTTPS original approval expired or changed before container creation.')
    video.verify_for_publish()
