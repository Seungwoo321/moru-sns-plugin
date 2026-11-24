import hashlib
from email.message import Message
import io
import os
import socket
import ssl
import unittest
import urllib.error
import urllib.request
from unittest import mock

import reconcile as r
import delivery_binding as d
import publisher as p


class DeliveryProxyTests(unittest.TestCase):
    def setUp(self):
        self.host='scontent-example.cdninstagram.com'
        self.url='https://'+self.host+'/approved.mp4?sig=private-query'
        self.proxy='http://proxy-user:proxy-secret@approved-environment-proxy:8080'
        self.body=b'approved-derivative-bytes'
        self.sha=hashlib.sha256(self.body).hexdigest()
        env={'HTTPS_PROXY':self.proxy,'https_proxy':self.proxy,
             'MORU_CDN_PROXY_HOST':self.host,'MORU_CDN_PROXY_SHA256':hashlib.sha256(self.proxy.encode()).hexdigest()}
        patch=mock.patch.dict(os.environ,env,clear=True);patch.start();self.addCleanup(patch.stop)
        dns=mock.patch.object(d.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))])
        self.dns=dns.start();self.addCleanup(dns.stop)

    def response(self,status=200,body=None,headers=None):
        body=self.body if body is None else body
        result=mock.Mock(status=status)
        values={'Content-Type':'video/mp4','Content-Length':str(len(body)),**(headers or {})}
        result.getheader.side_effect=lambda name,default=None:values.get(name,default)
        result.read.side_effect=io.BytesIO(body).read
        return result

    def fetch(self):d.remote_bytes(self.url,len(self.body),self.sha)

    def test_exact_inherited_proxy_route_preserves_tls_hostname_and_no_redirects(self):
        with mock.patch.object(d.urllib.request,'build_opener') as build,mock.patch.object(d,'PinnedHTTPS') as direct:
            response=self.response();build.return_value.open.return_value=response
            self.fetch()
            direct.assert_not_called()
            handlers=build.call_args.args
            proxy=next(h for h in handlers if isinstance(h,urllib.request.ProxyHandler))
            self.assertEqual(proxy.proxies,{'https':self.proxy})
            tls=next(h for h in handlers if isinstance(h,urllib.request.HTTPSHandler))
            self.assertTrue(tls._context.check_hostname)
            self.assertEqual(tls._context.verify_mode,ssl.CERT_REQUIRED)
            redirect=next(h for h in handlers if isinstance(h,p.NoRedirect))
            self.assertIsNone(redirect.redirect_request(None,None,302,'',{},'https://elsewhere.invalid'))
            request=build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url,self.url)
            self.assertEqual(request.host,self.host)
            self.assertEqual(request.get_method(),'GET')
            self.assertFalse(any('authorization' in k.lower() or 'cookie' in k.lower() for k in request.headers))
            self.assertEqual(build.return_value.open.call_args.kwargs,{'timeout':30})
            self.assertEqual(self.dns.call_count,2)
            response.close.assert_called_once()

    def test_stdlib_connect_keeps_proxy_credentials_out_of_origin_headers(self):
        recorded={};body=self.body
        class Response(io.BytesIO):
            status=code=200
            reason='OK'
            def __init__(self):
                super().__init__(body);self.headers=Message()
                self.headers['Content-Type']='video/mp4';self.headers['Content-Length']=str(len(body))
            def getheader(self,name,default=None):return self.headers.get(name,default)
            def info(self):return self.headers
        class Connection:
            sock=None
            debuglevel=0
            def __init__(self,host,**kwargs):recorded['proxy_host']=host;recorded['context']=kwargs['context']
            def set_debuglevel(self,value):pass
            def set_tunnel(self,host,port=None,headers=None):recorded['tunnel']=(host,port,headers)
            def request(self,method,target,body=None,headers=None,**kwargs):recorded['origin_headers']=headers;recorded['method']=method
            def getresponse(self):return Response()
            def close(self):pass
        with mock.patch.object(d.http.client,'HTTPSConnection',Connection):self.fetch()
        self.assertEqual(recorded['proxy_host'],'approved-environment-proxy:8080')
        self.assertEqual(recorded['tunnel'][0],self.host)
        self.assertTrue(recorded['context'].check_hostname)
        self.assertEqual(recorded['context'].verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(any(k.lower()=='proxy-authorization' for k in recorded['tunnel'][2]))
        self.assertFalse(any('authorization' in k.lower() for k in recorded['origin_headers']))
        self.assertEqual(recorded['method'],'GET')

    def test_missing_partial_changed_or_conflicting_proxy_settings_block(self):
        for changes in [{'MORU_CDN_PROXY_HOST':'other.cdninstagram.com'},
                        {'MORU_CDN_PROXY_SHA256':'0'*64},
                        {'https_proxy':'http://different-proxy:8080'},
                        {'MORU_CDN_PROXY_HOST':''},
                        {'HTTPS_PROXY':'','https_proxy':''}]:
            with self.subTest(case=list(changes)),mock.patch.dict(os.environ,changes),mock.patch.object(d,'PinnedHTTPS') as direct,mock.patch.object(d.urllib.request,'build_opener') as build:
                with self.assertRaises(p.SafeError):self.fetch()
                direct.assert_not_called();build.assert_not_called()

    def test_inherited_no_proxy_is_not_overridden_or_used_to_go_direct(self):
        with mock.patch.dict(os.environ,{'NO_PROXY':self.host}),mock.patch.object(d,'PinnedHTTPS') as direct,mock.patch.object(d.urllib.request,'build_opener') as build:
            with self.assertRaises(p.SafeError):self.fetch()
            direct.assert_not_called();build.assert_not_called()

    def test_proxy_denial_timeout_and_tls_failure_never_fall_back_or_leak(self):
        errors=[urllib.error.HTTPError(self.url,403,'denied',{},None),
                urllib.error.HTTPError(self.url,302,'redirect',{'Location':self.url},None),
                ssl.SSLCertVerificationError('private-query proxy-secret'),TimeoutError(self.url)]
        for error in errors:
            with self.subTest(kind=type(error).__name__),mock.patch.object(d.urllib.request,'build_opener') as build,mock.patch.object(d,'PinnedHTTPS') as direct:
                build.return_value.open.side_effect=error
                with self.assertRaises(p.SafeError) as result:self.fetch()
                self.assertNotIn('private-query',str(result.exception));self.assertNotIn('proxy-secret',str(result.exception))
                self.assertNotIn(self.url,str(result.exception));direct.assert_not_called()

    def test_proxy_bytes_and_response_restrictions_match_direct(self):
        for status,body,headers in [(200,b'wrong',{}),(302,self.body,{}),(200,self.body,{'Content-Encoding':'gzip'}),
                                    (200,self.body,{'Content-Type':'text/html'}),(200,self.body,{'Content-Length':'1'})]:
            with self.subTest(status=status,headers=headers),mock.patch.object(d.urllib.request,'build_opener') as build:
                build.return_value.open.return_value=self.response(status,body,headers)
                with self.assertRaises(p.SafeError):self.fetch()

    def test_private_mixed_and_failed_dns_still_block_proxy(self):
        for addresses in [[(2,1,6,'',('127.0.0.1',443))],[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('10.0.0.1',443))],[]]:
            with self.subTest(addresses=addresses),mock.patch.object(d.socket,'getaddrinfo',return_value=addresses),mock.patch.object(d.urllib.request,'build_opener') as build:
                with self.assertRaises(p.SafeError):self.fetch()
                build.assert_not_called()
        with mock.patch.object(d.socket,'getaddrinfo',side_effect=socket.gaierror()),mock.patch.object(d.urllib.request,'build_opener') as build:
            with self.assertRaises(p.SafeError):self.fetch()
            build.assert_not_called()

    def test_dns_rebinding_to_private_after_response_is_rejected(self):
        answers=[[(2,1,6,'',('8.8.8.8',443))],[(2,1,6,'',('10.0.0.1',443))]]
        with mock.patch.object(d.socket,'getaddrinfo',side_effect=answers),mock.patch.object(d.urllib.request,'build_opener') as build:
            build.return_value.open.return_value=self.response()
            with self.assertRaises(p.SafeError):self.fetch()

    def test_no_opt_in_retains_direct_pinning_and_ignores_ambient_proxy(self):
        del os.environ['MORU_CDN_PROXY_HOST'];del os.environ['MORU_CDN_PROXY_SHA256']
        with mock.patch.object(d,'PinnedHTTPS') as direct,mock.patch.object(d.urllib.request,'build_opener') as build:
            direct.return_value.getresponse.return_value=self.response()
            self.fetch();direct.assert_called_once();build.assert_not_called()

    def test_unsupported_proxy_scheme_and_invalid_destination_block(self):
        value='socks5://unapproved:1080'
        with mock.patch.dict(os.environ,{'HTTPS_PROXY':value,'https_proxy':value,'MORU_CDN_PROXY_SHA256':hashlib.sha256(value.encode()).hexdigest()}):
            with self.assertRaises(p.SafeError):self.fetch()
        for url in ['http://'+self.host+'/x','https://'+self.host+':444/x','https://example.com/x']:
            with self.subTest(url=url),self.assertRaises(p.SafeError):d.remote_bytes(url,len(self.body),self.sha)

if __name__=='__main__':unittest.main()
