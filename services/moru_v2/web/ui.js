// OAuth query validation, PKCE, signing and redirects belong to the issuer SDK.
export async function mount(root, config, auth, {fetcher = fetch, navigate = url => location.assign(url)} = {}) {
  let epoch = 0;
  let context;
  const text = (tag, value) => { const el = document.createElement(tag); el.textContent = value; return el; };
  const clear = title => { epoch++; root.replaceChildren(text('h1', title)); };
  const message = value => root.append(text('p', value));
  const button = (label, action) => {
    const el = text('button', label); el.type = 'button';
    el.onclick = async () => {
      const current = epoch; el.disabled = true;
      try { await action(); } catch { if (current === epoch) message('요청을 완료하지 못했습니다. 연결을 시작한 앱에서 다시 시도해 주세요.'); }
      finally { if (current === epoch) el.disabled = false; }
    };
    root.append(el); return el;
  };
  const session = async () => {
    const result = await auth.getSession();
    if (result.error || !result.data?.user?.id) throw Error('session required');
    return result.data.user.id;
  };
  const sameUser = async user => {
    if (await session() !== user) { clear('계정이 변경되었습니다'); message('이 화면을 새로 열어 다시 확인해 주세요.'); throw Error('account changed'); }
  };
  const api = async (path, method = 'GET') => {
    const response = await fetcher(path, {method, credentials: 'same-origin', headers: {'Accept': 'application/json', ...(method === 'POST' ? {'X-Moru-Context': context} : {})}});
    if (!response.ok) throw Error('connection request failed');
    return response.json();
  };
  clear('Moru 계정 연결');
  if (!config.identity_ready || !auth) { message('로그인 제공자 연결을 준비 중입니다. 현재 실제 계정 연결은 사용할 수 없습니다.'); return; }
  try {
    const current = epoch;
    const user = await session();
    if (current !== epoch) return;
    if (config.page === 'login') {
      message('로그인되었습니다. 요청한 앱으로 계속합니다.');
      const result = await auth.oauth2.continue({selected: true});
      if (result.error) throw Error('expired authorization');
      return;
    }
    if (config.page === 'consent') {
      const params = new URLSearchParams(location.search);
      if (!params.get('client_id') || params.getAll('client_id').length !== 1) throw Error('invalid client');
      const result = await auth.oauth2.publicClient({query: {client_id: params.get('client_id')}});
      await sameUser(user);
      if (result.error || !result.data) throw Error('client unavailable');
      clear('Moru 연결 권한 확인');
      message(`요청 앱: ${result.data.client_name || result.data.name || '이름 없음'}`);
      // Display only; the issuer verifies the signed authorization query on consent.
      message(`요청 범위: ${params.get('scope') || '없음'}`);
      message(`대상 서비스: ${params.get('resource') || '없음'}`);
      const redirects = result.data.redirect_uris;
      if (!Array.isArray(redirects) || !redirects.length) throw Error('missing redirects');
      const hosts = redirects.map(uri => new URL(uri).hostname);
      message(`돌아갈 호스트: ${hosts.join(', ')}`);
      if (hosts.every(host => ['localhost', '127.0.0.1', '[::1]'].includes(host))) message('이 앱은 로컬 컴퓨터로 돌아갑니다. 본인이 시작한 연결인지 확인해 주세요.');
      message('Moru 접근 동의와 Threads·Instagram 계정 연결은 별개입니다. 게시 권한은 부여하지 않습니다.');
      let deciding = false;
      const decide = async accept => {
        if (deciding) return;
        deciding = true;
        try {
        await sameUser(user);
        const reply = await auth.oauth2.consent({accept});
        if (reply.error) throw Error('consent rejected');
        clear(accept ? '동의를 처리했습니다' : '동의를 거절했습니다');
        message('요청한 앱으로 돌아가는 중입니다.');
        } finally { deciding = false; }
      };
      button('허용', () => decide(true)); button('거절', () => decide(false));
      return;
    }
    const data = await api('/connections');
    if (!data.context) throw Error('missing session binding');
    context = data.context;
    await sameUser(user);
    clear('연결된 계정');
    for (const platform of ['threads', 'instagram']) {
      const row = data.connections.find(item => item.platform === platform);
      const status = {connected: '연결됨', disconnected: '연결 해제됨', pending: '승인 대기', exchanging: '확인 중', expired: '연결 만료'}[row?.status] || '연결 안 됨';
      message(`${platform === 'threads' ? 'Threads' : 'Instagram'} — ${status}${row?.account_id ? ` · ${row.account_id}` : ''}`);
      if (!config.providers.includes(platform)) { message('이 제공자는 아직 설정되지 않았습니다.'); continue; }
      button(row?.status === 'connected' ? `${platform} 연결 해제` : `${platform} 연결`, async () => {
        await sameUser(user);
        if (row?.status === 'connected') {
          await api(`/connections/${platform}/disconnect`, 'POST');
          clear('Moru 연결이 해제되었습니다');
          message('Moru에 저장된 토큰은 삭제됐습니다. Meta 측 권한은 아직 철회하지 않았습니다.');
          return;
        }
        const reply = await api(`/connections/${platform}/start`, 'POST');
        await sameUser(user);
        const url = new URL(reply.authorization_url);
        const expected = platform === 'threads' ? 'https://threads.com' : 'https://www.instagram.com';
        if (url.origin !== expected || url.pathname !== '/oauth/authorize') throw Error('invalid provider redirect');
        navigate(url.href);
      });
    }
  } catch {
    clear('로그인 또는 연결 확인이 필요합니다');
    message('로그인 세션이나 요청이 만료됐을 수 있습니다. 연결을 시작한 앱에서 다시 시도해 주세요.');
    // No password collection or home-grown login/authorization fallback.
  }
}
