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
  const sdk={issueSignedToken:async o=>{calls.push(o);return {validUntil:o.validUntil};},presignUrl:async(t,o)=>{calls.push(o);return {presignedUrl:'private-memory-only'};}};
  const result=await run({...base,action:'sign',validUntil},sdk,'synthetic-secret');
  assert.deepEqual(calls[0].operations,['get']);assert.equal(calls[0].pathname,pathname);
  assert.equal(calls[0].validUntil,validUntil+60000);assert.equal(calls[1].validUntil,validUntil);
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

test('pinned SDK signs an explicit shorter URL expiry without network',async()=>{
  const {presignUrl}=await import('@vercel/blob');
  const validUntil=Date.now()+900000;
  const sdk={presignUrl,issueSignedToken:async o=>({
    validUntil:o.validUntil,clientSigningToken:'synthetic-local-signing-key',
    delegationToken:Buffer.from(JSON.stringify({storeId:'synthetic',pathname:o.pathname,operations:o.operations,validUntil:o.validUntil})).toString('base64url')+'.synthetic'
  })};
  const originalFetch=globalThis.fetch;
  globalThis.fetch=()=>{throw Error('No network permitted');};
  try {
    const result=await run({...base,action:'sign',validUntil},sdk,'synthetic-secret');
    const url=new URL(result.url);
    assert.equal(url.protocol,'https:');assert.equal(url.hostname,'synthetic.private.blob.vercel-storage.com');
    assert.equal(url.pathname,'/'+pathname);assert.equal(url.searchParams.get('vercel-blob-valid-until'),String(validUntil));
    assert.equal(url.searchParams.get('cache'),'0');
    await assert.rejects(run({...base,action:'sign',validUntil},{...sdk,issueSignedToken:async()=>({validUntil})},'synthetic-secret'));
  } finally {globalThis.fetch=originalFetch;}
});

test('actual pinned SDK ignores HTTPS_PROXY until approved dispatcher; no direct fallback',async()=>{
  const {createServer}=await import('node:http');
  const {Agent,getGlobalDispatcher,setGlobalDispatcher}=await import('undici');
  const {head}=await import('@vercel/blob');
  const {configureProxy}=await import('./bridge.mjs');
  let direct=0,connects=0;const server=createServer();
  server.on('connect',(req,socket)=>{connects++;assert.equal(req.url,'vercel.com:443');socket.end('HTTP/1.1 502 Synthetic failure\r\nContent-Length: 0\r\n\r\n');});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const old=getGlobalDispatcher(),oldProxy=process.env.HTTPS_PROXY,oldRetries=process.env.VERCEL_BLOB_RETRIES;
  process.env.VERCEL_BLOB_RETRIES='0';
  const blocked=new Agent({connect:(options,callback)=>{direct++;callback(Error('synthetic blocked direct'),null);}});
  let proxy;
  try {
    process.env.HTTPS_PROXY='http://127.0.0.1:'+server.address().port;
    setGlobalDispatcher(blocked);
    const probe=async()=>{const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),150);
      try{return await head(pathname,{token:'vercel_blob_rw_synthetic_test',abortSignal:controller.signal});}finally{clearTimeout(timer);}};
    await assert.rejects(probe());assert.ok(direct>0);assert.equal(connects,0);
    const count=direct;proxy=await configureProxy();
    await assert.rejects(probe());assert.ok(connects>0);assert.equal(direct,count);
    await assert.rejects(configureProxy({}));
  } finally {
    if(oldRetries===undefined)delete process.env.VERCEL_BLOB_RETRIES;else process.env.VERCEL_BLOB_RETRIES=oldRetries;
    setGlobalDispatcher(old);if(oldProxy===undefined)delete process.env.HTTPS_PROXY;else process.env.HTTPS_PROXY=oldProxy;
    if(proxy)await proxy.close();await blocked.close();await new Promise(r=>server.close(r));
  }
});

test('SDK and adapter resolve the same pinned Undici instance',async()=>{
  const {createRequire}=await import('node:module');const req=createRequire(import.meta.url);
  const sdkRequire=createRequire(req.resolve('@vercel/blob'));
  assert.equal(sdkRequire.resolve('undici'),req.resolve('undici'));
  assert.equal(req('undici/package.json').version,'6.23.0');
});

test('TLS errors are bounded categories with no provider messages or credentials',async()=>{
  const {classifyError}=await import('./bridge.mjs');
  assert.equal(classifyError({message:'signed-secret-url',cause:{code:'ERR_TLS_CERT_ALTNAME_INVALID'}}),'tls_validation_failed');
  assert.equal(classifyError({cause:{code:'ECONNREFUSED'}}),'connection_refused');
  assert.equal(classifyError({name:'AbortError'}),'request_timeout_or_aborted');
  assert.equal(classifyError({message:'https://private.invalid/?token=secret'}),'blob_operation_unconfirmed');
});

test('untrusted HTTPS proxy certificate is rejected before CONNECT',async()=>{
  const {createServer}=await import('node:https');const {execFileSync}=await import('node:child_process');
  const {readFile}=await import('node:fs/promises');const {getGlobalDispatcher,setGlobalDispatcher}=await import('undici');
  const {configureProxy,classifyError}=await import('./bridge.mjs');const {head}=await import('@vercel/blob');
  const root=await mkdtemp(join(tmpdir(),'moru-proxy-tls-'));
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',join(root,'key.pem'),'-out',join(root,'cert.pem'),'-days','1','-subj','/CN=localhost'],{stdio:'ignore'});
  const server=createServer({key:await readFile(join(root,'key.pem')),cert:await readFile(join(root,'cert.pem'))});let connects=0;
  server.on('connect',(req,socket)=>{connects++;socket.destroy();});await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const old=getGlobalDispatcher(),retries=process.env.VERCEL_BLOB_RETRIES;process.env.VERCEL_BLOB_RETRIES='0';let proxy;
  try {
    proxy=await configureProxy({HTTPS_PROXY:'https://127.0.0.1:'+server.address().port});
    await assert.rejects(head(pathname,{token:'vercel_blob_rw_synthetic_test',abortSignal:AbortSignal.timeout(2000)}),e=>classifyError(e)==='tls_validation_failed');
    assert.equal(connects,0);
  } finally {
    setGlobalDispatcher(old);if(retries===undefined)delete process.env.VERCEL_BLOB_RETRIES;else process.env.VERCEL_BLOB_RETRIES=retries;
    if(proxy)await proxy.close();await new Promise(r=>server.close(r));await rm(root,{recursive:true,force:true});
  }
});

test('official SDK child uses only injected managed CA for HTTPS proxy TLS',async()=>{
  const {createServer}=await import('node:https');const {execFileSync,spawn}=await import('node:child_process');
  const {readFile}=await import('node:fs/promises');
  const root=await mkdtemp(join(tmpdir(),'moru-managed-ca-'));const cert=join(root,'cert.pem');
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',join(root,'key.pem'),'-out',cert,'-days','1','-subj','/CN=synthetic','-addext','subjectAltName=IP:127.0.0.1'],{stdio:'ignore'});
  let connects=0;
  const server=createServer({key:await readFile(join(root,'key.pem')),cert:await readFile(cert)});
  server.on('connect',(req,socket)=>{connects++;assert.equal(req.url,'vercel.com:443');socket.end('HTTP/1.1 502 Synthetic\r\nContent-Length: 0\r\n\r\n');});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const script=`import {configureProxy,classifyError} from './bridge.mjs';import {head} from '@vercel/blob';
    const p=await configureProxy();try{await head(${JSON.stringify(pathname)},{token:'vercel_blob_rw_synthetic_test',abortSignal:AbortSignal.timeout(1000)});}
    catch(e){process.stdout.write(classifyError(e));}finally{await p.close();}`;
  const probe=(flags,caEnv)=>new Promise((resolve,reject)=>{
    const child=spawn(process.execPath,[...flags,'--input-type=module','-e',script],{
      cwd:new URL('.',import.meta.url),env:{PATH:process.env.PATH,HTTPS_PROXY:'https://127.0.0.1:'+server.address().port,VERCEL_BLOB_RETRIES:'0',...caEnv},stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',c=>output+=c);child.stderr.resume();child.on('error',reject);child.on('close',code=>code?reject(Error('child failed')):resolve(output));
  });
  try {
    assert.equal(await probe([],{}),'tls_validation_failed');assert.equal(connects,0);
    await probe(['--use-system-ca'],{NODE_EXTRA_CA_CERTS:cert});assert.equal(connects,1);
    await probe(['--use-system-ca'],{SSL_CERT_FILE:cert});assert.equal(connects,2);
  } finally {await new Promise(r=>server.close(r));await rm(root,{recursive:true,force:true});}
});

test('authenticated exact-prefix absence requires explicit complete bounded pagination',async()=>{
  const calls=[];let index=0;
  const sdk={list:async options=>{calls.push(options);return index++===0?{blobs:[],hasMore:true,cursor:'next'}:{blobs:[],hasMore:false};}};
  assert.deepEqual(await run({...base,action:'absent'},sdk,'synthetic-token'),{absent:true,complete:true,prefix:pathname,pages:2});
  assert.equal(calls[0].token,'synthetic-token');assert.equal(calls[0].prefix,pathname);assert.equal(calls[1].cursor,'next');
  for(const page of [{},{blobs:[]},{blobs:[],hasMore:'false'},{blobs:[{pathname}],hasMore:false},{blobs:[],hasMore:true},{blobs:[],hasMore:true,cursor:'cycle'}]) {
    await assert.rejects(run({...base,action:'absent'},{list:async()=>page},'synthetic-token'));
  }
  await assert.rejects(run({...base,action:'absent'},{list:async()=>{throw Error('auth failure');}},'synthetic-token'));
});
