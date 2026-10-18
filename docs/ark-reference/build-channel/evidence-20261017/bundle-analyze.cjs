const fs = require("fs");
const DIR = "C:/Users/ADMIN/AppData/Local/Temp/kilo/bundles/";
const files = ["v4-match-1.js", "v4-match-2.js", "v4-match-3.js", "v4-match-4.js", "v4-match-5.js"]
    .map(f => DIR + f)
    .filter(f => fs.existsSync(f));
const out = [];
const say = s => {
    out.push(s);
    console.log(s);
};
const texts = files.map(f => ({ f, t: fs.readFileSync(f, "utf8") }));

const methods = new Set();
for (const { t } of texts) for (const m of t.matchAll(/MakerSuiteService\/([A-Za-z0-9]+)/g)) methods.add(m[1]);
say("== MakerSuiteService METHODS ==\n" + [...methods].sort().join("\n"));

const esc = s => s.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&");
function ctx(t, needle, before = 200, after = 280, limit = 3) {
    const res = [];
    let i = 0;
    while (res.length < limit) {
        const p = t.indexOf(needle, i);
        if (p < 0) break;
        res.push(t.slice(Math.max(0, p - before), p + after).replace(/\s+/g, " "));
        i = p + needle.length;
    }
    return res;
}

const NEEDLES = [
    "StreamGenerateContent",
    "streamGenerateContent",
    "ProxyUnaryCall",
    "ProxyStreamedCall",
    "x-goog-api-key",
    "GenerateAccessToken",
    "candidateCount",
    "responseFormat",
    "responseMimeType",
    "maxOutputTokens",
    "safetySettings",
    "getMetadata",
    "isSignedIn",
    "generateContent",
];
for (const needle of NEEDLES) {
    say(`\n== ${needle} ==`);
    for (const { f, t } of texts) {
        const c = (t.match(new RegExp(esc(needle), "g")) || []).length;
        if (!c) continue;
        say(`-- ${f.split("/").pop()}: count=${c}`);
        ctx(t, needle).forEach(s => say("   | " + s.slice(0, 460)));
    }
}
fs.writeFileSync(DIR + "analysis.txt", out.join("\n"));
console.log("\n[saved] analysis.txt lines=" + out.length);