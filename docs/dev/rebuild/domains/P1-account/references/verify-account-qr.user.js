// SOURCE: user-provided upstream userscript (油猴脚本.js from Desktop/fsdownload), not part of this repo.
// PURPOSE: research reference for AI Studio verify-account QR short-code extraction.
// NOTE: copied verbatim below this header; original file left untouched.
// ==UserScript==
// @name         reCAPTCHA QR 短码提取助手
// @namespace    https://qr.cuberelay.workers.dev
// @version      1.2.0
// @description  无侵入提取 recaptcha.net/qr/ 短码（不在验证码 iframe 内修改 DOM），支持一键复制、填回中转页及过关提醒。
// @match        https://aistudio.google.com/*
// @match        https://*.recaptcha.net/recaptcha/*
// @match        https://*.google.com/recaptcha/*
// @match        https://recaptcha.net/recaptcha/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  const HUB_ORIGIN = "https://qr.cuberelay.workers.dev";
  const SHORTCODE_REGEX = /recaptcha\.net\/qr\/([A-Za-z0-9_-]{6,32})/;
  const STRICT_CODE_REGEX = /^[A-Za-z0-9_-]{6,32}$/;
  const MSG_TYPE = '__qr_hub_shortcode_v1';

  // 1. Inside reCAPTCHA bframe: strictly read-only, zero DOM mutations, zero MutationObserver.
  if (location.pathname.includes('/bframe') || location.pathname.includes('/fallback')) {
    let lastSent = '';

    function readShortcode() {
      const btn = document.getElementById('rc-mobile-verification-button');
      if (btn && btn.href) {
        const m = btn.href.match(SHORTCODE_REGEX);
        if (m) return m[1];
      }
      const canvas = document.querySelector('canvas.recaptchaJavascriptChallengeQrCanvas');
      if (canvas && canvas.title) {
        const m = canvas.title.match(SHORTCODE_REGEX);
        if (m) return m[1];
      }
      const fallback = document.querySelector('.recaptchaJavascriptChallengeQrFallbackText');
      if (fallback && fallback.textContent) {
        const m = fallback.textContent.match(SHORTCODE_REGEX);
        if (m) return m[1];
      }
      return '';
    }

    function pollAndNotify() {
      const code = readShortcode();
      if (!code || code === lastSent) return;
      lastSent = code;
      try {
        if (window.top && window.top !== window) {
          window.top.postMessage({ type: MSG_TYPE, code: code }, 'https://aistudio.google.com');
        }
      } catch {}
    }

    pollAndNotify();
    setInterval(pollAndNotify, 800);
    return;
  }

  // 2. On aistudio.google.com top page: receive shortcode and show floating bar outside bframe.
  if (location.hostname !== 'aistudio.google.com') {
    return;
  }

  let currentCode = '';
  let barEl = null;
  let sawChallengeOnVerify = false;
  let showedReturnHint = false;
  function isAllowedOrigin(origin) {
    return (
      origin === 'https://www.recaptcha.net' ||
      origin === 'https://recaptcha.net' ||
      origin === 'https://www.google.com'
    );
  }

  function copyText(text, onDone) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(onDone, () => {
        window.prompt('长按复制短码:', text);
      });
    } else {
      window.prompt('长按复制短码:', text);
    }
  }

  function renderTopBar(code) {
    currentCode = code;
    if (location.pathname.includes('/verify-account')) {
      sawChallengeOnVerify = true;
    }
    showedReturnHint = false;
    if (!barEl && document.body) {
      barEl = document.createElement('div');
      barEl.id = '__qr_hub_top_bar';
      barEl.style.cssText = [
        'position:fixed',
        'bottom:12px',
        'left:12px',
        'right:12px',
        'max-width:460px',
        'margin:0 auto',
        'z-index:2147483647',
        'background:#14161b',
        'color:#e4e6eb',
        'border:1px solid #303440',
        'border-radius:8px',
        'padding:8px 10px',
        'font:13px/1.35 system-ui,-apple-system,sans-serif',
        'display:flex',
        'align-items:center',
        'justify-content:space-between',
        'gap:8px',
        'box-shadow:0 6px 20px rgba(0,0,0,0.45)'
      ].join(';');

      const codeSpan = document.createElement('span');
      codeSpan.id = '__qr_code_span';
      codeSpan.style.cssText = 'font-family:ui-monospace,monospace;color:#c9d1d9;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';

      const btnGroup = document.createElement('div');
      btnGroup.style.cssText = 'display:flex;gap:6px;flex-shrink:0;';

      const copyBtn = document.createElement('button');
      copyBtn.type = 'button';
      copyBtn.textContent = '复制';
      copyBtn.style.cssText = 'background:#23262f;color:#e4e6eb;border:1px solid #353945;border-radius:5px;padding:4px 10px;font-size:12px;cursor:pointer;';
      copyBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        copyText(currentCode, () => {
          copyBtn.textContent = '已复制';
          setTimeout(() => { copyBtn.textContent = '复制'; }, 1200);
        });
      });

      const sendBtn = document.createElement('a');
      sendBtn.id = '__qr_send_link';
      sendBtn.target = '_blank';
      sendBtn.rel = 'noopener noreferrer';
      sendBtn.textContent = '填回中转页';
      sendBtn.style.cssText = 'background:#2f3542;color:#e4e6eb;border:1px solid #454c5e;text-decoration:none;border-radius:5px;padding:4px 10px;font-size:12px;';

      const closeBtn = document.createElement('button');
      closeBtn.type = 'button';
      closeBtn.textContent = '×';
      closeBtn.style.cssText = 'background:transparent;color:#8b909a;border:none;padding:2px 6px;font-size:15px;cursor:pointer;';
      closeBtn.addEventListener('click', () => {
        if (barEl) {
          barEl.remove();
          barEl = null;
        }
      });

      btnGroup.appendChild(copyBtn);
      btnGroup.appendChild(sendBtn);
      btnGroup.appendChild(closeBtn);
      barEl.appendChild(codeSpan);
      barEl.appendChild(btnGroup);
      document.body.appendChild(barEl);
    }

    if (barEl) {
      const span = barEl.querySelector('#__qr_code_span');
      const link = barEl.querySelector('#__qr_send_link');
      if (span && span.textContent !== currentCode) {
        span.textContent = currentCode;
      }
      const nextHref = HUB_ORIGIN + '/?code=' + encodeURIComponent(currentCode);
      if (link && link.getAttribute('href') !== nextHref) {
        link.setAttribute('href', nextHref);
      }
    }
  }

  function renderReturnReminder() {
    if (showedReturnHint || !document.body) return;
    showedReturnHint = true;
    if (!barEl) {
      renderTopBar(currentCode);
    }
    if (!barEl) return;
    const span = barEl.querySelector('#__qr_code_span');
    const link = barEl.querySelector('#__qr_send_link');
    const copyBtn = barEl.querySelector('button');
    if (span) {
      span.textContent = '验证似乎已通过，请回中转页点「已通过」';
    }
    if (copyBtn) {
      copyBtn.style.display = 'none';
    }
    if (link) {
      link.textContent = '回到中转页';
      link.setAttribute('href', HUB_ORIGIN + '/');
    }
  }

  setInterval(() => {
    if (location.pathname.includes('/verify-account')) {
      if (currentCode) sawChallengeOnVerify = true;
      return;
    }
    if (sawChallengeOnVerify && currentCode && !showedReturnHint) {
      renderReturnReminder();
    }
  }, 1200);

  window.addEventListener('message', (event) => {
    if (!isAllowedOrigin(event.origin)) return;
    const data = event.data;
    if (!data || data.type !== MSG_TYPE || typeof data.code !== 'string') return;
    if (!STRICT_CODE_REGEX.test(data.code)) return;
    renderTopBar(data.code);
  });
})();
