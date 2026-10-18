const fs = require("fs");
const DIR = "C:/Users/ADMIN/AppData/Local/Temp/kilo/bundles/";
const T1 = fs.readFileSync(DIR + "v4-match-1.js", "utf8");
const T2 = fs.readFileSync(DIR + "v4-match-2.js", "utf8");
const T4 = fs.readFileSync(DIR + "v4-match-4.js", "utf8");
const out = [];
const say = s => {
    out.push(s);
    console.log(s.slice(0, 200));
};
const esc = s => s.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&");
function sec(name, t, needle, before, after, limit, show = 3000) {
    say(`\n===== ${name} : ${needle} =====`);
    let i = 0,
        n = 0;
    while (n < limit) {
        const p = t.indexOf(needle, i);
        if (p < 0) break;
        n++;
        say(`--- occurrence #${n} @${p} ---`);
        say(t.slice(Math.max(0, p - before), p + after));
        if (out.length > 420) return;
        i = p + needle.length;
    }
    if (!n) say("(none)");
}

sec("T1 serverStreaming def", T1, "serverStreaming=function", 0, 1200, 2);
sec("T1 serverStreaming def2", T1, "serverStreaming(", 300, 700, 3);
sec("T1 oFa", T1, "oFa=", 100, 600, 3);
sec("T1 l3a", T1, "l3a=", 100, 700, 2);
sec("T1 aya full", T1, "return aya(", 3600, 300, 1, 4000);
sec("T1 thinkingBudget", T1, "thinkingBudget", 200, 400, 2);
sec("T1 mFa", T1, "mFa=", 100, 700, 2);
sec("T1 zFa use", T1, "zFa", 300, 400, 4);
sec("T4 perUserQuota", T4, "generateContentPerUserQuota", 500, 900, 2);
sec("T4 streamGenQuota", T4, "streamGenerateContentPerUserQuota", 400, 800, 2);
fs.writeFileSync(DIR + "analysis2.txt", out.join("\n"));
console.log("\n[saved] analysis2.txt sections=" + out.length);

const out2 = [];
const say2 = s => {
    out2.push(s);
    console.log(s.slice(0, 200));
};
function sec2(name, t, needle, before, after, limit) {
    say2(`\n===== ${name} : ${needle} =====`);
    let i = 0,
        n = 0;
    while (n < limit) {
        const p = t.indexOf(needle, i);
        if (p < 0) break;
        n++;
        say2(`--- occurrence #${n} @${p} ---`);
        say2(t.slice(Math.max(0, p - before), p + after));
        if (out2.length > 400) return;
        i = p + needle.length;
    }
    if (!n) say2("(none)");
}
sec2("T2 default_MakerSuite", T2, "default_MakerSuite", 200, 500, 3);
sec2("T2 nK", T2, ".nK", 120, 200, 5);
sec2("T2 nK2", T2, "nK:", 120, 200, 5);
sec2("T2 Cn:", T2, "Cn:", 100, 250, 3);
sec2("T2 GB:", T2, "GB:", 100, 250, 3);
fs.writeFileSync(DIR + "analysis2b.txt", out2.join("\n"));
console.log("\n[saved] analysis2b.txt sections=" + out2.length);