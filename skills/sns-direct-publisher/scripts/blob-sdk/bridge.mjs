// Native Work subprocess only. No server, routes, Functions, or public upload API.
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

// Use only the environment's approved proxy. No NO_PROXY/direct fallback.
export async function configureProxy(env=process.env) {
  const {ProxyAgent,setGlobalDispatcher}=await import('undici');
  const raw=env.HTTPS_PROXY || env.https_proxy;
  if(!raw)throw Error('approved_proxy_required');
  const uri=new URL(raw);
  if(!['http:','https:'].includes(uri.protocol) || uri.hash || (uri.pathname && uri.pathname!='/') || uri.search)throw Error('proxy_configuration');
  const proxy=new ProxyAgent({uri:raw});
  const dispatcher={dispatch(options,handler){
    const origin=new URL(String(options.origin));
    if(origin.protocol!=='https:' || (origin.port && origin.port!=='443') ||
       !(origin.hostname==='vercel.com' && /^\/api\/blob(?:[/?]|$)/.test(options.path) || /^(?:[a-z0-9-]+\.)*blob\.vercel-storage\.com$/.test(origin.hostname)))throw Error('destination');
    return proxy.dispatch(options,handler);
  }};
  setGlobalDispatcher(dispatcher);
  return proxy;
}

export function classifyError(error) {
  const tls=new Set(['DEPTH_ZERO_SELF_SIGNED_CERT','SELF_SIGNED_CERT_IN_CHAIN','UNABLE_TO_VERIFY_LEAF_SIGNATURE','ERR_TLS_CERT_ALTNAME_INVALID','CERT_HAS_EXPIRED']);
  for(let n=0;error && n<4;n++,error=error.cause) {
    if(tls.has(error.code))return 'tls_validation_failed';
    if(error.code==='ECONNREFUSED')return 'connection_refused';
    if(['ETIMEDOUT','UND_ERR_CONNECT_TIMEOUT','UND_ERR_HEADERS_TIMEOUT','ABORT_ERR'].includes(error.code) || ['AbortError','TimeoutError','BlobRequestAbortedError'].includes(error.name))return 'request_timeout_or_aborted';
  }
  return 'blob_operation_unconfirmed';
}

export async function run(input, sdk, token) {
  if (!token || !/^[a-f0-9]{64}$/.test(input.sha256) ||
      !/^moru-native\/[a-f0-9]{64}\.mp4$/.test(input.pathname)) throw Error('invalid');
  if(input.action==='check')return {supported:['put','issueSignedToken','presignUrl','head','del','list'].every(k=>typeof sdk[k]==='function')};
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
  if(input.action==='absent') {
    let cursor;const seen=new Set();let pages=0;
    do {
      const page=await sdk.list({...auth,prefix:input.pathname,limit:1000,...(cursor?{cursor}:{})});
      if(!page || !Array.isArray(page.blobs) || typeof page.hasMore!=='boolean')throw Error('incomplete');
      // Prefix collisions also hold: do not widen the absence interpretation.
      if(page.blobs.length!==0)throw Error('present_or_ambiguous');
      pages++;
      if(!page.hasMore) {
        if(page.cursor!==undefined && page.cursor!==null && typeof page.cursor!=='string')throw Error('ambiguous_cursor');
        return {absent:true,complete:true,prefix:input.pathname,pages};
      }
      if(typeof page.cursor!=='string' || !page.cursor || seen.has(page.cursor) || pages>=100)throw Error('incomplete');
      cursor=page.cursor;seen.add(cursor);
    } while(true);
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

if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  try {
    let raw='';for await(const chunk of process.stdin){raw+=chunk;if(raw.length>65536)throw Error('input');}
    const input=JSON.parse(raw);
    await configureProxy();
    const sdk=await import('@vercel/blob');
    const result=await run(input,sdk,process.env.MORU_BLOB_READ_WRITE_TOKEN);
    process.stdout.write(JSON.stringify(result));
  } catch(error) {process.stdout.write(JSON.stringify({error:classifyError(error)}));process.exitCode=2;}
}
