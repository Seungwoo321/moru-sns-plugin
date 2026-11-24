import asyncio
import time
from types import SimpleNamespace
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from services.moru_v2.issuer import IssuerVerifier

ISSUER='https://identity.test/branch/auth'
RESOURCE='https://moru.test/mcp'
META=dict(issuer=ISSUER, code_challenge_methods_supported=['S256'], response_types_supported=['code'],
          scopes_supported=['profile'], token_endpoint_auth_methods_supported=['none'],
          authorization_endpoint=ISSUER+'/authorize', token_endpoint=ISSUER+'/token', jwks_uri=ISSUER+'/keys')


def verifier():
    return IssuerVerifier(issuer=ISSUER,resource=RESOURCE,metadata=META,clients={'work'},scopes={'profile'})


@pytest.mark.parametrize('change',[{'issuer':'https://other.test'}, {'code_challenge_methods_supported':['plain']},
    {'scopes_supported':[]},{'token_endpoint_auth_methods_supported':[]},{'jwks_uri':'http://unsafe.test/keys'}])
def test_discovery_rejects_incompatible_issuer(change):
    with pytest.raises(ValueError):
        IssuerVerifier(issuer=ISSUER,resource=RESOURCE,metadata=dict(META,**change),clients={'work'},scopes={'profile'})


@pytest.mark.parametrize('change',[{}, {'iss':'https://other-branch.test'}, {'aud':'https://other.test'},
    {'exp':1}, {'client_id':'unknown'}, {'scope':'email'}, {'sub':''}])
def test_real_jwt_signature_issuer_audience_and_scope(change):
    private=Ed25519PrivateKey.generate()
    check=verifier()
    check.jwks=SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key()))
    claims=dict(iss=ISSUER,aud=[RESOURCE,ISSUER+'/userinfo'],exp=int(time.time())+300,
                sub='alice',client_id='work',scope='profile')
    token=jwt.encode(dict(claims,**change),private,algorithm='EdDSA')
    result=asyncio.run(check.verify_token(token))
    assert (result is not None) == (not change)
    forged=jwt.encode(claims,Ed25519PrivateKey.generate(),algorithm='EdDSA')
    assert asyncio.run(check.verify_token(forged)) is None
