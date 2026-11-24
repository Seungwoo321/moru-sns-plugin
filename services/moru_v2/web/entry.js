import { createAuthClient } from '@neon/auth';
import { mount } from './ui.js';
const config = JSON.parse(document.getElementById('moru-config').textContent);
mount(document.getElementById('app'), config,
  config.identity_ready ? createAuthClient(config.issuer) : null);
