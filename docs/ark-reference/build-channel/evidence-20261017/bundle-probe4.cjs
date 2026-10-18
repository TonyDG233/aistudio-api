/**
 * Bundle archaeology probe v4: full resource inventory (no .js filter), longer wait,
 * save every fetched script-like payload whose text mentions the MakerSuite client.
 */
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const repoRequire = createRequire("D:/LLM/Ark_AIStudioToAPI/package.json");
const { firefox } = repoRequire("playwright");

const APP = "https://ai.studio/apps/3b85b4ed-f945-422c-ba25-395d1feef528";
const OUT = "C:/Users/ADMIN/AppData/Local/Temp/kilo/bundles";
fs.mkdirSync(OUT, { recursive: true });
const NEEDLES = ["MakerSuiteService", "default_MakerSuite", "ProxyStreamedCall", "alkalimakersuite", "x-goog-api-key", "generateContent"];

(async () => {
    const browser = await firefox.launch({
        executablePath: "D:/LLM/Ark_AIStudioToAPI/camoufox/camoufox.exe",
        headless: true,
    });
    const context = await browser.newContext({
        proxy: { server: "http://127.0.0.1:10809" },
        storageState: "D:/LLM/Ark_AIStudioToAPI/configs/auth/auth-7.json",
    });
    const page = await context.newPage();
    console.log("[probe] navigating ...");
    await page.goto(APP, { timeout: 90000, waitUntil: "domcontentloaded" });
    await page.waitForTimeout(12000);
    try {
        const btn = page.locator("text=Continue to the app");
        if ((await btn.count()) > 0) {
            console.log("[probe] clicking Continue ...");
            await btn.first().click({ timeout: 5000 });
        }
    } catch (e) {
        console.log("[continue skipped]", String((e && e.message) || e).slice(0, 80));
    }
    await page.waitForTimeout(45000);

    const state = await page.evaluate(() => ({ href: location.href, title: document.title }));
    console.log("[state]", JSON.stringify(state));

    const entries = await page.evaluate(() =>
        [...new Set(performance.getEntriesByType("resource").map(r => `${r.initiatorType}|${r.name}`))]
    );
    fs.writeFileSync(path.join(OUT, "resource-urls-v4.txt"), entries.join("\n"));
    console.log("[probe] resource entries:", entries.length);
    entries.forEach(e => console.log("  " + e.slice(0, 160)));

    // Fetch and scan every non-analytics resource whose URL lives on aistudio/google hosts.
    let saved = 0,
        scanned = 0;
    for (const entry of entries) {
        const url = entry.split("|")[1];
        if (!/aistudio\.google\.com|ai\.studio|gstatic\.com|googleusercontent/.test(url)) continue;
        if (/googletagmanager|analytics|gtm\.js/.test(url)) continue;
        scanned++;
        try {
            const resp = await page.request.get(url, { timeout: 25000 });
            const text = await resp.text();
            if (text.length > 15 * 1024 * 1024) {
                console.log(`[big-skip] ${text.length} ${url.slice(0, 120)}`);
                continue;
            }
            const hits = NEEDLES.filter(n => text.includes(n));
            if (hits.length) {
                saved++;
                const file = path.join(OUT, `v4-match-${saved}.js`);
                fs.writeFileSync(file, text);
                console.log(`[MATCH#${saved}] size=${text.length} hits=${JSON.stringify(hits)}`);
                console.log(`         url=${url.slice(0, 200)}`);
                console.log(`         saved=${file}`);
            }
        } catch (e) {
            console.log("[skip]", url.slice(0, 100), String((e && e.message) || e).slice(0, 80));
        }
    }

    const surface = await page.evaluate(() => {
        const M = window.default_MakerSuite;
        return M ? { present: true, keys: Object.keys(M).join(",") } : { present: false };
    });
    console.log("[surface]", JSON.stringify(surface));
    console.log(`[probe] done: scanned=${scanned} saved=${saved}`);
    await browser.close();
})().catch(e => {
    console.error("[probe] FATAL", e);
    process.exit(1);
});