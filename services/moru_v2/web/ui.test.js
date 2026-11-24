import {describe, it, expect, vi, beforeEach} from 'vitest';
import {mount} from './ui.js';
let root, auth, config, fetcher;
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
beforeEach(() => {
  document.body.innerHTML = '<main id="app"></main>'; root = document.getElementById('app');
  history.replaceState({}, '', '/auth/consent?client_id=known&scope=profile&resource=https%3A%2F%2Fmoru.test%2Fmcp');
  auth = {getSession: vi.fn().mockResolvedValue({data:{user:{id:'alice'}}}), oauth2: {
    publicClient: vi.fn().mockResolvedValue({data:{client_name:'Known client',redirect_uris:['https://client.test/callback']}}),
    consent: vi.fn().mockResolvedValue({data:{}}), continue: vi.fn().mockResolvedValue({data:{}})}};
  config = {identity_ready:true, issuer:'https://identity.test',page:'consent',providers:['threads','instagram']};
  fetcher = vi.fn().mockResolvedValue({ok:true,json:async()=>({context:'alice-session',connections:[]})});
});
it('blocks missing issuer without contacting auth or connection services', async()=>{
  await mount(root,{...config,identity_ready:false},auth,{fetcher});
  expect(root.textContent).toContain('준비 중'); expect(auth.getSession).not.toHaveBeenCalled(); expect(fetcher).not.toHaveBeenCalled();
});
it('shows verified client details as text and delegates deny to SDK',async()=>{
  auth.oauth2.publicClient.mockResolvedValue({data:{client_name:'<img src=x onerror=alert(1)>',redirect_uris:['http://127.0.0.1/cb']}});
  await mount(root,config,auth,{fetcher});
  expect(root.querySelector('img')).toBeNull(); expect(root.textContent).toContain('로컬 컴퓨터');
  [...root.querySelectorAll('button')].find(b=>b.textContent==='거절').click(); await settle();
  expect(auth.oauth2.consent).toHaveBeenCalledWith({accept:false}); expect(fetcher).not.toHaveBeenCalled();
});
it('does not consent if user changed after rendering',async()=>{
  await mount(root,config,auth,{fetcher});
  auth.getSession.mockResolvedValue({data:{user:{id:'bob'}}});
  root.querySelector('button').click(); await settle();
  expect(auth.oauth2.consent).not.toHaveBeenCalled(); expect(root.textContent).not.toContain('Known client');
});
it('requires a session before showing client details',async()=>{
  auth.getSession.mockResolvedValue({data:null}); await mount(root,config,auth,{fetcher});
  expect(auth.oauth2.publicClient).not.toHaveBeenCalled(); expect(root.querySelector('button')).toBeNull();
});
it('shows safe retry state on expired signed consent query',async()=>{
  auth.oauth2.consent.mockResolvedValue({error:{message:'SECRET-CANARY'}});
  await mount(root,config,auth,{fetcher}); root.querySelector('button').click(); await settle();
  expect(root.textContent).toContain('다시 시도'); expect(root.textContent).not.toContain('CANARY');
});
it('login resumes through SDK only',async()=>{
  await mount(root,{...config,page:'login'},auth,{fetcher});
  expect(auth.oauth2.continue).toHaveBeenCalledWith({selected:true}); expect(fetcher).not.toHaveBeenCalled();
});
it('does not render prior user connections when session changes during fetch',async()=>{
  auth.getSession.mockResolvedValueOnce({data:{user:{id:'alice'}}}).mockResolvedValue({data:{user:{id:'bob'}}});
  fetcher.mockResolvedValue({ok:true,json:async()=>({context:'alice-session',connections:[{platform:'threads',status:'connected',account_id:'ALICE-PRIVATE'}]})});
  await mount(root,{...config,page:'account'},auth,{fetcher});
  expect(root.textContent).not.toContain('ALICE-PRIVATE'); expect(root.querySelector('button')).toBeNull();
});
it('connection action sends session binding and follows only official provider URL',async()=>{
  const navigate=vi.fn(); await mount(root,{...config,page:'account'},auth,{fetcher,navigate});
  fetcher.mockResolvedValue({ok:true,json:async()=>({authorization_url:'https://evil.test/steal'})});
  root.querySelector('button').click(); await settle();
  expect(fetcher.mock.lastCall[1].headers['X-Moru-Context']).toBe('alice-session');
  expect(navigate).not.toHaveBeenCalled(); expect(root.textContent).toContain('다시 시도');
});
it('accept delegates exactly once while a consent decision is pending',async()=>{
  let finish; auth.oauth2.consent.mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  await mount(root,config,auth,{fetcher});
  const buttons=root.querySelectorAll('button'); buttons[0].click(); await settle(); buttons[1].click(); await settle();
  expect(auth.oauth2.consent).toHaveBeenCalledTimes(1); expect(auth.oauth2.consent).toHaveBeenCalledWith({accept:true});
  finish({data:{}}); await settle(); expect(root.textContent).toContain('동의를 처리했습니다');
});
