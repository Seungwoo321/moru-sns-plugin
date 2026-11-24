import { test } from 'node:test';
import assert from 'node:assert/strict';
import { run } from './bridge.mjs';
import { createHash } from 'node:crypto';
import { mkdtemp,writeFile,open,rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const pathname='moru-native/'+'a'.repeat(64)+'.mp4';
const base={pathname,sha256:'a'.repeat(64)};

test('GET-only pathname/expiry delegation; no wildcard or credentials in result',async()=>{
  const validUntil=Date.now()+600000;let calls=[];
  const sdk={issueSignedToken:async o=>{calls.push(o);return {validUntil};},presignUrl:async(t,o)=>{calls.push(o);return {presignedUrl:'private-memory-only'};}};
  const result=await run({...base,action:'sign',validUntil},sdk,'synthetic-secret');
  assert.deepEqual(calls[0].operations,['get']);assert.equal(calls[0].pathname,pathname);
  assert.equal(calls[1].access,'private');assert.equal(calls[1].operation,'get');assert.equal(calls[1].useCache,false);
  assert.deepEqual(result,{url:'private-memory-only',operation:'get',validUntil});
});
test('conditional delete and missing-object retry never invoke put',async()=>{
  class Missing extends Error{};let present=true,deleted=0;
  const sdk={BlobNotFoundError:Missing,head:async()=>{if(!present)throw new Missing();return {pathname,etag:'exact',contentType:'video/mp4'};},
    del:async(p,o)=>{assert.equal(p,pathname);assert.equal(o.ifMatch,'exact');deleted++;present=false;}};
  assert.equal((await run({...base,action:'delete',etag:'exact'},sdk,'secret')).deleted,true);
  assert.equal((await run({...base,action:'delete',etag:'exact'},sdk,'secret')).alreadyAbsent,true);
  assert.equal(deleted,1);
  present=true;await assert.rejects(run({...base,action:'delete',etag:'other'},sdk,'secret'));assert.equal(deleted,1);
});
test('private immutable upload reads only inherited bytes and rejects wrong SHA',async()=>{
  const root=await mkdtemp(join(tmpdir(),'moru-blob-sdk-'));let fd;
  try{
    const path=join(root,'final.mp4');const bytes=Buffer.from('synthetic final bytes');await writeFile(path,bytes,{mode:0o400});fd=await open(path,'r');
    const sha256=createHash('sha256').update(bytes).digest('hex');let calls=0;
    const sdk={put:async(p,b,o)=>{calls++;assert.equal(p,pathname);assert.deepEqual(b,bytes);
      assert.equal(o.access,'private');assert.equal(o.allowOverwrite,false);assert.equal(o.addRandomSuffix,false);
      assert.equal(o.contentType,'video/mp4');assert.equal(o.ttlDays,undefined);
      return {url:'unsigned-private-url',pathname,contentType:'video/mp4',etag:'exact'};}};
    await run({...base,action:'put',fd:fd.fd,bytes:bytes.length,sha256},sdk,'secret');
    await assert.rejects(run({...base,action:'put',fd:fd.fd,bytes:bytes.length},sdk,'secret'));assert.equal(calls,1);
  }finally{if(fd)await fd.close();await rm(root,{recursive:true,force:true});}
});
test('expired signing request is rejected without an SDK call',async()=>{
  await assert.rejects(run({...base,action:'sign',validUntil:Date.now()-1},{},'secret'));
});
test('accepted delete with cached HEAD separates verification from retry',async()=>{
  class Missing extends Error{};let cached=true,deleted=0;
  const sdk={BlobNotFoundError:Missing,head:async()=>{if(!cached)throw new Missing();return {pathname,etag:'exact',contentType:'video/mp4'};},del:async()=>{deleted++;}};
  assert.equal((await run({...base,action:'delete',etag:'exact'},sdk,'secret')).verificationPending,true);
  assert.equal((await run({...base,action:'verify_delete',etag:'exact'},sdk,'secret')).verificationPending,true);
  cached=false;
  assert.equal((await run({...base,action:'verify_delete',etag:'exact'},sdk,'secret')).deleted,true);
  assert.equal(deleted,1);
});
