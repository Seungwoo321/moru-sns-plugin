"""Candidate-bound observations of user-confirmed app deletions; never delete state."""
import re

import publisher as p
import candidate_review as review
import private_runtime as runtime


def reservation(item):
    return 'external_absence:'+':'.join(item[k] for k in ('platform','account_id','post_id'))


def validate(ledger, items, key, *, require_replacement=True):
    review.require(isinstance(items,list) and (0 < len(items) <= 20 if require_replacement else len(items)<=20))
    marker=p.read_state_marker(ledger.directory/'runtime.json')
    seen=set(); immediate=[]
    for item in items:
        review.keys(item,'platform account_id job_id post_id permalink user_confirmation_record report_sha256 immediate_replacement')
        platform=item['platform'];binding=marker['accounts'].get(platform,{})
        review.require(binding.get('account_id')==item['account_id'])
        review.identifier(item['job_id']);review.identifier(item['user_confirmation_record']);review.sha(item['report_sha256'])
        review.require(isinstance(item['post_id'],str) and re.fullmatch(r'[0-9]+',item['post_id']))
        review.require(type(item['immediate_replacement']) is bool)
        identity=(platform,item['account_id'],item['post_id'])
        review.require(identity not in seen);seen.add(identity)
        row=ledger.get((platform,item['account_id'],item['job_id']))
        review.require(row is not None and row['state']=='published' and row['post_id']==item['post_id'] and row['permalink']==item['permalink'])
        if item['immediate_replacement']:
            review.require(identity[:2]==key[:2]);immediate.append(item)
            owner=ledger.db.execute('SELECT value FROM recovery_metadata WHERE key=?',(reservation(item),)).fetchone()
            review.require(owner is None or owner[0]==review.canonical(key))
    # One explicitly approved replacement of one receipt, never a general exemption.
    review.require(len(immediate)==1 if require_replacement else len(immediate)<=1)
    return {(i['platform'],i['account_id'],i['job_id']) for i in immediate}


def match(ledger, rows, items, key, clients=None, *, require_replacement=True):
    validate(ledger,items,key,require_replacement=require_replacement)
    actual={(r['platform'],r['account_id'],r['post_id']):r for r in rows}
    saved={(r['platform'],r['account_id'],r['post_id']):dict(r) for r in ledger.db.execute("SELECT * FROM jobs WHERE state='published'")}
    absent={(i['platform'],i['account_id'],i['post_id']) for i in items}
    review.require(len(actual)==len(rows) and set(actual)==set(saved)-absent)
    review.require(all(row['permalink']==saved[k]['permalink'] for k,row in actual.items()))
    marker=p.read_state_marker(ledger.directory/'runtime.json')
    for item in items:
        platform=item['platform'];binding=marker['accounts'][platform]
        client=(clients or {}).get(platform)
        if client is None:
            config=runtime.load_environment(platform)
            review.require(config['expected_user_id']==binding['account_id'] and config['expected_username']==binding['username'] and config['instagram_login_mode']==binding['auth_mode'])
            client=p.APIClient(platform,config['access_token'],binding['auth_mode'])
        # A complete owned list brackets the individual GET. An unsupported-object
        # error alone is NOT proof of deletion (it can also mean lost permission).
        expected=[r for r in rows if r['platform']==platform]
        review.require(review.snapshot_digest(review.owned(platform,binding,client))==review.snapshot_digest(expected))
        try:
            client.request('GET','/'+item['post_id'],params={'fields':'id,permalink'})
        except p.ApiError as exc:
            review.require(exc.status==400 and exc.graph_code==100 and exc.graph_subcode==33)
        else:
            raise p.SafeError('The app-deleted receipt remains queryable; replacement is blocked.')
        review.require(review.snapshot_digest(review.owned(platform,binding,client))==review.snapshot_digest(expected))


def reserve(ledger,items,key):
    for item in items:
        if item['immediate_replacement']:
            ledger.db.execute('INSERT INTO recovery_metadata VALUES(?,?)',(reservation(item),review.canonical(key)))
