(payload) => {
    // P3-F deterministic per-account browser fingerprint.
    // Registered through Page.addScriptToEvaluateOnNewDocument, so this runs
    // before any page script on every navigation in the target page/context.
    try {
        if (typeof window === 'undefined' || typeof navigator === 'undefined') return;
        const P = payload || {};
        const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

        const define = (target, key, value) => {
            try {
                Object.defineProperty(target, key, {
                    get: () => value,
                    configurable: true,
                    enumerable: true
                });
                return true;
            } catch (e) {
                try {
                    Object.defineProperty(target, key, {
                        value: value,
                        configurable: true,
                        writable: true
                    });
                    return true;
                } catch (e2) {
                    return false;
                }
            }
        };

        const defineMethod = (target, key, fn) => {
            if (!target) return;
            try {
                Object.defineProperty(target, key, {
                    value: fn,
                    configurable: true,
                    writable: true
                });
            } catch (e) {}
        };

        const nav = navigator;
        const languages = Array.isArray(P.languages) && P.languages.length
            ? P.languages.slice()
            : ['en-US', 'en'];
        const language = languages[0] || 'en-US';

        // --- core navigator identity -------------------------------------
        define(nav, 'webdriver', false);
        define(nav, 'platform', P.platform || 'Win32');
        define(nav, 'vendor', P.vendor || 'Google Inc.');
        define(nav, 'vendorSub', '');
        define(nav, 'productSub', '20030107');
        define(nav, 'appName', 'Netscape');
        define(nav, 'appCodeName', 'Mozilla');
        define(nav, 'appVersion', String(P.userAgent || '').replace(/^Mozilla\//, ''));
        define(nav, 'userAgent', P.userAgent || '');
        define(nav, 'language', language);
        define(nav, 'languages', languages);
        define(nav, 'hardwareConcurrency', Number(P.hardwareConcurrency) || 8);
        define(nav, 'deviceMemory', Number(P.deviceMemory) || 8);
        define(nav, 'maxTouchPoints', 0);
        define(nav, 'cookieEnabled', true);
        define(nav, 'onLine', true);
        define(nav, 'doNotTrack', null);
        define(nav, 'pdfViewerEnabled', true);

        // --- userAgentData / Client Hints ---------------------------------
        const brands = Array.isArray(P.uaBrands) ? P.uaBrands.slice() : [];
        const fullVersionList = Array.isArray(P.uaFullVersionList)
            ? P.uaFullVersionList.slice()
            : [];
        const uaData = {
            brands: brands,
            mobile: false,
            platform: 'Windows',
            getHighEntropyValues: (hints) => Promise.resolve({
                architecture: 'x86',
                bitness: '64',
                brands: brands,
                fullVersionList: fullVersionList,
                mobile: false,
                model: '',
                platform: 'Windows',
                platformVersion: P.platformVersion || '15.0.0',
                uaFullVersion: P.uaFullVersion || '',
                wow64: false
            }),
            toJSON: () => ({ brands: brands, mobile: false, platform: 'Windows' })
        };
        define(nav, 'userAgentData', uaData);

        // --- plugins / mimeTypes -----------------------------------------
        // Real Chrome exposes PluginArray/Plugin/MimeTypeArray/MimeType in the
        // window scope. When present, bind our objects to those prototypes so
        // `instanceof` and prototype introspection match a native browser.
        // When they are absent (older engines/tests) fall back to plain objects
        // that keep the indexed/length/item/namedItem contract.
        const nativeCtor = (name) => {
            try {
                if (window && window[name]) return window[name];
            } catch (e) {}
            try {
                if (typeof globalThis !== 'undefined' && globalThis[name]) {
                    return globalThis[name];
                }
            } catch (e) {}
            return null;
        };
        const nativeProto = (ctor, fallback) => {
            if (ctor && ctor.prototype && typeof ctor.prototype === 'object') {
                return ctor.prototype;
            }
            if (fallback && typeof fallback === 'object') {
                const proto = Object.getPrototypeOf(fallback);
                if (proto && proto !== Object.prototype && proto !== Array.prototype) {
                    return proto;
                }
            }
            return null;
        };
        let nativePlugins = null;
        let nativeMimeTypes = null;
        try { nativePlugins = nav.plugins || null; } catch (e) {}
        try { nativeMimeTypes = nav.mimeTypes || null; } catch (e) {}
        const PluginCtor = nativeCtor('Plugin');
        const PluginArrayCtor = nativeCtor('PluginArray');
        const MimeTypeCtor = nativeCtor('MimeType');
        const MimeTypeArrayCtor = nativeCtor('MimeTypeArray');
        const pluginProto = nativeProto(
            PluginCtor,
            nativePlugins && nativePlugins.length ? nativePlugins[0] : null
        );
        const pluginArrayProto = nativeProto(PluginArrayCtor, nativePlugins);
        const mimeTypeProto = nativeProto(
            MimeTypeCtor,
            nativeMimeTypes && nativeMimeTypes.length ? nativeMimeTypes[0] : null
        );
        const mimeTypeArrayProto = nativeProto(MimeTypeArrayCtor, nativeMimeTypes);

        const createObject = (proto, fields) => {
            let target;
            try {
                target = proto ? Object.create(proto) : {};
            } catch (e) {
                target = {};
            }
            for (const key in fields) {
                if (!hasOwn(fields, key)) continue;
                try {
                    Object.defineProperty(target, key, {
                        value: fields[key],
                        configurable: true,
                        enumerable: true,
                        writable: true
                    });
                } catch (e) {
                    try { target[key] = fields[key]; } catch (e2) {}
                }
            }
            return target;
        };
        const itemAt = (collection, index) => {
            const position = Number(index);
            if (!Number.isFinite(position) || position < 0 || position >= collection.length) {
                return null;
            }
            const value = collection[position];
            return value === undefined || value === null ? null : value;
        };
        const mimeMatches = (mime, name) =>
            !!mime && (mime.type === name || mime.suffixes === name);
        const createCollection = (proto, items, methods) => {
            const target = createObject(proto, {});
            for (let i = 0; i < items.length; i++) {
                try {
                    Object.defineProperty(target, i, {
                        value: items[i],
                        configurable: true,
                        enumerable: true,
                        writable: false
                    });
                } catch (e) {
                    try { target[i] = items[i]; } catch (e2) {}
                }
            }
            try {
                Object.defineProperty(target, 'length', {
                    value: items.length,
                    configurable: true,
                    enumerable: false
                });
            } catch (e) {
                try { target.length = items.length; } catch (e2) {}
            }
            for (const key in methods) {
                if (!hasOwn(methods, key)) continue;
                try {
                    Object.defineProperty(target, key, {
                        value: methods[key],
                        configurable: true,
                        writable: true
                    });
                } catch (e) {
                    try { target[key] = methods[key]; } catch (e2) {}
                }
            }
            if (typeof Symbol !== 'undefined' && Symbol.iterator) {
                try {
                    Object.defineProperty(target, Symbol.iterator, {
                        value: function () {
                            let position = 0;
                            const self = this;
                            return {
                                next: function () {
                                    if (position < self.length) {
                                        return { value: self[position++], done: false };
                                    }
                                    return { value: undefined, done: true };
                                }
                            };
                        },
                        configurable: true,
                        writable: true
                    });
                } catch (e) {}
            }
            return target;
        };

        const pluginSpecs = Array.isArray(P.plugins) ? P.plugins : [];
        const mimeObjects = [];
        const pluginObjects = [];
        const findMime = (type) => {
            for (let i = 0; i < mimeObjects.length; i++) {
                if (mimeObjects[i].type === type) return mimeObjects[i];
            }
            return null;
        };
        for (let pIndex = 0; pIndex < pluginSpecs.length; pIndex++) {
            const spec = pluginSpecs[pIndex] || {};
            const mimeTypes = Array.isArray(spec.mimeTypes) ? spec.mimeTypes : [];
            const pluginMimes = [];
            for (let mIndex = 0; mIndex < mimeTypes.length; mIndex++) {
                const type = String(mimeTypes[mIndex] || '');
                let mime = findMime(type);
                if (!mime) {
                    mime = createObject(mimeTypeProto, {
                        type: type,
                        suffixes: type === 'application/pdf' ? 'pdf' : '',
                        description: String(spec.description || ''),
                        enabledPlugin: null
                    });
                    mimeObjects.push(mime);
                }
                pluginMimes.push(mime);
            }
            const plugin = createCollection(pluginProto, pluginMimes, {
                name: String(spec.name || ''),
                filename: String(spec.filename || ''),
                description: String(spec.description || ''),
                item: function (index) {
                    return itemAt(this, index);
                },
                namedItem: function (name) {
                    for (let i = 0; i < this.length; i++) {
                        if (mimeMatches(this[i], name)) return this[i];
                    }
                    return null;
                }
            });
            for (let mIndex = 0; mIndex < pluginMimes.length; mIndex++) {
                if (!pluginMimes[mIndex].enabledPlugin) {
                    pluginMimes[mIndex].enabledPlugin = plugin;
                }
            }
            pluginObjects.push(plugin);
        }
        const pluginArray = createCollection(pluginArrayProto, pluginObjects, {
            item: function (index) {
                return itemAt(this, index);
            },
            namedItem: function (name) {
                for (let i = 0; i < this.length; i++) {
                    if (this[i] && this[i].name === name) return this[i];
                }
                return null;
            },
            refresh: function () {}
        });
        const mimeArray = createCollection(mimeTypeArrayProto, mimeObjects, {
            item: function (index) {
                return itemAt(this, index);
            },
            namedItem: function (name) {
                for (let i = 0; i < this.length; i++) {
                    if (mimeMatches(this[i], name)) return this[i];
                }
                return null;
            }
        });
        define(nav, 'plugins', pluginArray);
        define(nav, 'mimeTypes', mimeArray);

        // --- screen / viewport -------------------------------------------
        const scr = window.screen;
        if (scr) {
            define(scr, 'width', Number(P.screenWidth) || 1920);
            define(scr, 'height', Number(P.screenHeight) || 1080);
            define(scr, 'availWidth', Number(P.availWidth) || 1920);
            define(scr, 'availHeight', Number(P.availHeight) || 1040);
            define(scr, 'colorDepth', Number(P.colorDepth) || 24);
            define(scr, 'pixelDepth', Number(P.pixelDepth) || 24);
            if (scr.orientation) {
                define(scr.orientation, 'type', 'landscape-primary');
                define(scr.orientation, 'angle', 0);
            }
        }
        define(window, 'devicePixelRatio', Number(P.devicePixelRatio) || 1);
        define(window, 'innerWidth', Number(P.innerWidth) || 1904);
        define(window, 'innerHeight', Number(P.innerHeight) || 952);
        define(window, 'outerWidth', Number(P.outerWidth) || 1920);
        define(window, 'outerHeight', Number(P.outerHeight) || 1040);

        // --- WebGL vendor / renderer -------------------------------------
        const GL_UNMASKED_VENDOR = 0x9245;
        const GL_UNMASKED_RENDERER = 0x9246;
        const patchGL = (proto) => {
            if (!proto || typeof proto.getParameter !== 'function') return;
            const original = proto.getParameter;
            defineMethod(proto, 'getParameter', function (parameter) {
                if (parameter === GL_UNMASKED_VENDOR && P.webglVendor) {
                    return P.webglVendor;
                }
                if (parameter === GL_UNMASKED_RENDERER && P.webglRenderer) {
                    return P.webglRenderer;
                }
                return original.apply(this, arguments);
            });
        };
        patchGL(window.WebGLRenderingContext && window.WebGLRenderingContext.prototype);
        patchGL(window.WebGL2RenderingContext && window.WebGL2RenderingContext.prototype);

        // --- deterministic canvas noise ----------------------------------
        const hash32 = (seed, index) => {
            let h = (seed ^ Math.imul(index + 1, 0x9e3779b1)) >>> 0;
            h ^= h >>> 15;
            h = Math.imul(h, 0x85ebca6b) >>> 0;
            h ^= h >>> 13;
            return h >>> 0;
        };
        const canvasSeed = Number(P.canvasNoiseSeed) >>> 0;
        const addCanvasNoise = (data) => {
            if (!data || !data.length) return;
            for (let i = 0; i < data.length; i += 4) {
                const h = hash32(canvasSeed, i >> 2);
                if ((h & 0x7f) !== 0) continue;
                const channel = i + ((h >>> 9) % 3);
                const value = data[channel];
                if (typeof value !== 'number') continue;
                const delta = (h & 0x100) ? 1 : -1;
                data[channel] = Math.max(0, Math.min(255, value + delta));
            }
        };
        if (window.CanvasRenderingContext2D) {
            const ctx2d = window.CanvasRenderingContext2D.prototype;
            const originalGetImageData = ctx2d.getImageData;
            if (typeof originalGetImageData === 'function') {
                defineMethod(ctx2d, 'getImageData', function () {
                    const imageData = originalGetImageData.apply(this, arguments);
                    try {
                        addCanvasNoise(imageData && imageData.data);
                    } catch (e) {}
                    return imageData;
                });
            }
        }
        if (window.HTMLCanvasElement) {
            const canvasProto = window.HTMLCanvasElement.prototype;
            const originalToDataURL = canvasProto.toDataURL;
            if (typeof originalToDataURL === 'function') {
                defineMethod(canvasProto, 'toDataURL', function () {
                    try {
                        const width = Number(this.width) || 0;
                        const height = Number(this.height) || 0;
                        if (!width || !height || width * height > 4194304) {
                            return originalToDataURL.apply(this, arguments);
                        }
                        const copy = document.createElement('canvas');
                        copy.width = width;
                        copy.height = height;
                        const copyCtx = copy.getContext('2d');
                        if (!copyCtx) return originalToDataURL.apply(this, arguments);
                        copyCtx.drawImage(this, 0, 0);
                        const imageData = copyCtx.getImageData(0, 0, width, height);
                        addCanvasNoise(imageData && imageData.data);
                        copyCtx.putImageData(imageData, 0, 0);
                        return originalToDataURL.apply(copy, arguments);
                    } catch (e) {
                        return originalToDataURL.apply(this, arguments);
                    }
                });
            }
        }

        // --- deterministic audio noise -----------------------------------
        const audioSeed = Number(P.audioNoiseSeed) >>> 0;
        const addAudioNoise = (data) => {
            if (!data || !data.length) return;
            for (let i = 0; i < data.length; i++) {
                const h = hash32(audioSeed, i);
                const delta = (((h & 0xffff) / 65535) - 0.5) * 1e-7;
                data[i] = data[i] + delta;
            }
        };
        if (window.AudioBuffer) {
            const audioProto = window.AudioBuffer.prototype;
            const originalGetChannelData = audioProto.getChannelData;
            if (typeof originalGetChannelData === 'function') {
                defineMethod(audioProto, 'getChannelData', function () {
                    const data = originalGetChannelData.apply(this, arguments);
                    if (data && !hasOwn(data, '__aistudioFpAudio')) {
                        try {
                            addAudioNoise(data);
                            Object.defineProperty(data, '__aistudioFpAudio', {
                                value: true,
                                configurable: true,
                                enumerable: false
                            });
                        } catch (e) {}
                    }
                    return data;
                });
            }
        }
        if (window.AnalyserNode) {
            const analyserProto = window.AnalyserNode.prototype;
            const originalGetFloat = analyserProto.getFloatFrequencyData;
            if (typeof originalGetFloat === 'function') {
                defineMethod(analyserProto, 'getFloatFrequencyData', function (array) {
                    originalGetFloat.apply(this, arguments);
                    try {
                        if (array && array.length) {
                            for (let i = 0; i < array.length; i++) {
                                const h = hash32(audioSeed, i + 0x40000);
                                array[i] = array[i] + (((h & 0xffff) / 65535) - 0.5) * 1e-7;
                            }
                        }
                    } catch (e) {}
                });
            }
        }

        // --- window.chrome presence --------------------------------------
        // Non-destructive merge: keep the host's native chrome object and every
        // property it already exposes; only fill the keys real Chrome has but a
        // headless/patched build may be missing.
        let chromeObject = window.chrome;
        if (!chromeObject || (typeof chromeObject !== 'object' && typeof chromeObject !== 'function')) {
            chromeObject = {};
        }
        const fillChromeKey = (key, factory) => {
            let present = false;
            try {
                present = key in chromeObject;
            } catch (e) {
                present = false;
            }
            if (present) {
                try {
                    if (chromeObject[key] !== undefined && chromeObject[key] !== null) return;
                } catch (e) {
                    return;
                }
            }
            let value;
            try {
                value = factory();
            } catch (e) {
                return;
            }
            try {
                Object.defineProperty(chromeObject, key, {
                    value: value,
                    configurable: true,
                    writable: true,
                    enumerable: true
                });
            } catch (e) {
                try { chromeObject[key] = value; } catch (e2) {}
            }
        };
        fillChromeKey('runtime', () => ({}));
        fillChromeKey('app', () => ({
            isInstalled: false,
            InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
            RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
            getDetails: () => null,
            getIsInstalled: () => false
        }));
        fillChromeKey('csi', () => () => ({
            onloadT: Date.now(),
            startE: Date.now() - 1000,
            pageT: 1000,
            tran: 15
        }));
        fillChromeKey('loadTimes', () => () => {
            const now = Date.now() / 1000;
            return {
                requestTime: now - 0.4,
                startLoadTime: now - 0.35,
                commitLoadTime: now - 0.2,
                finishDocumentLoadTime: now - 0.1,
                finishLoadTime: now,
                firstPaintTime: now - 0.08,
                firstPaintAfterLoadTime: 0,
                navigationType: 'Other',
                wasFetchedViaSpdy: true,
                wasNpnNegotiated: true,
                npnNegotiatedProtocol: 'h2',
                wasAlternateProtocolAvailable: false,
                connectionInfo: 'h2'
            };
        });
        if (chromeObject !== window.chrome) {
            try {
                Object.defineProperty(window, 'chrome', {
                    value: chromeObject,
                    configurable: true,
                    writable: true
                });
            } catch (e) {}
        }

        // --- remove common automation markers ----------------------------
        const automationKeys = [
            'webdriver',
            '__webdriver_evaluate',
            '__selenium_evaluate',
            '__webdriver_script_function',
            '__webdriver_script_func',
            '__webdriver_script_fn',
            '__fxdriver_evaluate',
            '__driver_unwrapped',
            '__webdriver_unwrapped',
            '__driver_evaluate',
            '__selenium_unwrapped',
            '__fxdriver_unwrapped'
        ];
        for (let i = 0; i < automationKeys.length; i++) {
            try {
                delete window[automationKeys[i]];
            } catch (e) {}
        }
    } catch (e) {}
}
