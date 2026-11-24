"""Separate local container inspection from availability of original-byte delivery."""
import struct

import publisher as p


def container_checks(path):
    """Bounded ISO BMFF walk; never scan compressed payload for box-name strings."""
    top=[]; edits=False; count=0
    with path.open('rb') as source:
        size=path.stat().st_size
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
    if not direct:reasons.append('official_original_transfer_path_missing')
    return {'platform':platform,'media_type':'REELS' if platform=='instagram' else 'VIDEO',
            'local':{'status':'blocked' if local_reasons else 'checked_operating_profile',
                     'reasons':local_reasons,'container_checks':checks,
                     'codec_checks':'existing MP4/H264/yuv420p, 23-60fps, optional AAC <=48kHz mono/stereo, 30-150s',
                     'not_certified':['closed_GOP','VBR_mode','128kbps_audio_target','provider_acceptance']},
            'delivery':{'status':'local_original_upload_available' if direct else 'blocked',
                        'remote_copy_verified':False,'bytes_sent':False,
                        'reason':None if direct else 'official_original_transfer_path_missing',
                        'message':None if direct else '공식 원본 전송 경로 없음'},
            'blocking_reasons':reasons,'publish_ready':False}


def require_new(platform,video,upload_mode=None):
    if platform not in ('threads','instagram') or not isinstance(video,p.Video):return
    result=inspect(platform,video,upload_mode)
    if result['blocking_reasons']:
        raise p.SafeError('Video preflight blocked: '+','.join(result['blocking_reasons'])+
                          (' (공식 원본 전송 경로 없음)' if result['delivery']['status']=='blocked' else ''))
