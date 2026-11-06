async ({ prompt, hash, warmup }) => {
    const dms = window.default_MakerSuite;
    const aistudio = window.__AISTUDIO__ || (window.__AISTUDIO__ = {});
    const service = aistudio.service;
    const snapKey = aistudio.snapKey;
    if (!dms || !service || !snapKey || typeof dms[snapKey] !== 'function') {
        throw new Error('service_unavailable');
    }

    const isShown = (el) => {
        try { return el.checkVisibility({ visibilityProperty: true }); } catch (e) { return true; }
    };
    const promptBox = () => {
        const boxes = Array.from(document.querySelectorAll('ms-prompt-box textarea')).filter(isShown);
        if (boxes.length) return boxes[0];
        const any = Array.from(document.querySelectorAll('textarea')).filter(isShown);
        return any[0] || null;
    };

    // The prompt only exists to nudge AI Studio's composer/snapshot state; the
    // full 200KB payload must never be materialized in the DOM. Slice by code
    // points so a trailing astral character (emoji) is never split into lone
    // UTF-16 surrogates.
    const promptChars = typeof prompt === 'string' ? Array.from(prompt) : [];
    const tailPrompt = promptChars.length > 0
        ? promptChars.slice(-256).join('')
        : '';
    const box = promptBox();
    const desc = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
    const setValue = (desc && desc.set) ? desc.set : function(val) { this.value = val; };
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const applyPrompt = async () => {
        if (!box || tailPrompt.length === 0) return;
        setValue.call(box, tailPrompt);
        box.dispatchEvent(new window.InputEvent('input', { bubbles: true, inputType: 'insertText', data: tailPrompt }));
        box.dispatchEvent(new window.Event('change', { bubbles: true }));
        await new Promise((resolve) => {
            if (typeof window.requestAnimationFrame === 'function') {
                window.requestAnimationFrame(() => setTimeout(resolve, 40));
            } else {
                setTimeout(resolve, 40);
            }
        });
    };
    const clearPrompt = () => {
        if (!box) return;
        setValue.call(box, '');
        box.dispatchEvent(new window.InputEvent('input', { bubbles: true, inputType: 'deleteContent' }));
        box.dispatchEvent(new window.Event('change', { bubbles: true }));
    };

    const MIN_SNAPSHOT_LENGTH = 1520;
    const PLACEHOLDER_HASH = '0'.repeat(64);
    const isQualitySnapshot = (value) => (
        typeof value === 'string'
        && value.length >= MIN_SNAPSHOT_LENGTH
        && value.startsWith('!')
    );
    const SNAPSHOT_TIMEOUT_MS = 20000;
    const withTimeout = (promise, ms) => {
        let timer;
        const timeout = new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error('snapshot_timeout')), ms);
        });
        return Promise.race([Promise.resolve(promise), timeout]).finally(() => {
            clearTimeout(timer);
        });
    };
    const callSnapshot = async (hashValue) => {
        const result = dms[snapKey](service, hashValue);
        return await withTimeout(result, SNAPSHOT_TIMEOUT_MS);
    };
    const runWarmup = async () => {
        try {
            await callSnapshot(PLACEHOLDER_HASH);
        } catch (e) {
            // Warmup is best-effort and must never mask the real snapshot attempt.
        }
        await wait(40);
    };

    const run = async () => {
        try {
            await applyPrompt();
            let warmupUsed = false;
            let snapshot;
            if (warmup === true) {
                warmupUsed = true;
                await runWarmup();
                snapshot = await callSnapshot(hash);
            } else {
                snapshot = await callSnapshot(hash);
                if (!isQualitySnapshot(snapshot)) {
                    warmupUsed = true;
                    await runWarmup();
                    snapshot = await callSnapshot(hash);
                }
            }
            if (!isQualitySnapshot(snapshot)) {
                throw new Error('low_quality_snapshot');
            }
            aistudio.snapshot = snapshot;
            return { snapshot, warmup_used: warmupUsed, quality_ok: true };
        } finally {
            clearPrompt();
        }
    };

    const prev = aistudio.snapQueue || Promise.resolve();
    const current = prev.catch(() => {}).then(run);
    aistudio.snapQueue = current;
    return await current;
}
