// Native Work subprocess only. No server, routes, Functions, or public upload API.
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

export async function run(input, sdk, token) {
  if (!token || !/^[a-f0-9]{64}$/.test(input.sha256) ||
      !/^moru-native\/[a-f0-9]{64}\.mp4$/.test(input.pathname)) throw Error('invalid');
  if(input.action==='check')return {supported:['put','issueSignedToken','presignUrl','head','del'].every(k=>typeof sdk[k]==='function')};
  const auth={token,abortSignal:AbortSignal.timeout(120000)};
  if(input.action==='put') {
    // Read inherited O_RDONLY fd, not a caller-supplied URL or alternate file.
    const bytes=await readFile('/proc/self/fd/'+input.fd);
    if(bytes.length!==input.bytes || createHash('sha256').update(bytes).digest('hex')!==input.sha256) throw Error('changed');
    const blob=await sdk.put(input.pathname,bytes,{...auth,access:'private',contentType:'video/mp4',
      addRandomSuffix:false,allowOverwrite:false,multipart:false});
    return {url:blob.url,pathname:blob.pathname,contentType:blob.contentType,etag:blob.etag};
  }
  if(input.action==='sign') {
    if(!Number.isSafeInteger(input.validUntil) || input.validUntil<=Date.now() || input.validUntil>Date.now()+1800000) throw Error('expiry');
    const signed=await sdk.issueSignedToken({...auth,pathname:input.pathname,operations:['get'],validUntil:input.validUntil+60000});
    if(!Number.isSafeInteger(signed.validUntil) || signed.validUntil<=input.validUntil || signed.validUntil>input.validUntil+60000) throw Error('expiry');
    const result=await sdk.presignUrl(signed,{operation:'get',pathname:input.pathname,access:'private',validUntil:input.validUntil,useCache:false});
    return {url:result.presignedUrl,operation:'get',validUntil:input.validUntil};
  }
  if(input.action==='head') {
    const blob=await sdk.head(input.pathname,auth);
    if(blob.pathname!==input.pathname || blob.contentType!=='video/mp4' || !blob.etag)throw Error('changed');
    return {url:blob.url,pathname:blob.pathname,size:blob.size,contentType:blob.contentType,etag:blob.etag};
  }
  if(input.action==='delete' || input.action==='verify_delete') {
    if(!input.etag) throw Error('etag');
    // Confirm exact object before conditional deletion. A missing object is already clean.
    let blob;
    try {blob=await sdk.head(input.pathname,auth);} catch(e) {
      if(e instanceof sdk.BlobNotFoundError)return {deleted:true,alreadyAbsent:true};
      throw e;
    }
    if(blob.etag!==input.etag || blob.pathname!==input.pathname || blob.contentType!=='video/mp4')throw Error('changed');
    if(input.action==='verify_delete')return {deleted:false,verificationPending:true};
    await sdk.del(input.pathname,{...auth,ifMatch:input.etag});
    try {await sdk.head(input.pathname,auth);} catch(e) {
      if(e instanceof sdk.BlobNotFoundError)return {deleted:true};
      return {deleted:false,verificationPending:true};
    }
    return {deleted:false,verificationPending:true};
  }
  throw Error('action');
}

if(import.meta.url===pathToFileURL(process.argv[1]).href) {
  try {
    let raw='';for await(const chunk of process.stdin){raw+=chunk;if(raw.length>65536)throw Error('input');}
    const input=JSON.parse(raw);
    const sdk=await import('@vercel/blob');
    const result=await run(input,sdk,process.env.MORU_BLOB_READ_WRITE_TOKEN);
    process.stdout.write(JSON.stringify(result));
  } catch {process.stdout.write('{"error":"blob_operation_unconfirmed"}');process.exitCode=2;}
}
