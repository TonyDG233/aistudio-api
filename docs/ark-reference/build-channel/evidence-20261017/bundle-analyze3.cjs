const fs = require("fs");
const DIR = "C:/Users/ADMIN/AppData/Local/Temp/kilo/bundles/";
const T1 = fs.readFileSync(DIR + "v4-match-1.js", "utf8");
const T4 = fs.readFileSync(DIR + "v4-match-4.js", "utf8");
const out = [];
const say = s => {
    out.push(s);
    console.log(s.slice(0, 160));
};
function sec(name, t, needle, before, after, limit) {
    say(`\n===== ${name} : ${needle} =====`);
    let i = 0,
        n = 0;
    while (n < limit) {
        const p = t.indexOf(needle, i);
        if (p < 0) break;
        n++;
        say(`--- #${n} @${p} ---`);
        say(t.slice(Math.max(0, p - before), p + after));
        i = p + needle.length;
    }
    if (!n) say("(none)");
}
sec("T1 tl full", T1, "_.tl=function(a){", 0, 2800, 1);
sec("T1 zu class", T1, "_.zu=class", 0, 3200, 1);
sec("T1 Mv class", T1, "_.Mv=class", 0, 900, 1);
sec("T1 aya full", T1, "Object.assign({},{model:", 300, 2600, 1);
sec("T1 stopSequences", T1, "stopSequences", 150, 300, 3);
sec("T1 responseSchema", T1, "responseSchema", 150, 300, 3);
sec("T1 nK hits", T1, "nK", 80, 160, 6);
sec("T1 functionCallingConfig", T1, "functionCallingConfig", 200, 400, 2);
sec("T1 getMetadata def", T1, "getMetadata(){", 400, 600, 2);
fs.writeFileSync(DIR + "analysis3.txt", out.join("\n"));
console.log("\n[saved] analysis3.txt lines=" + out.length);
const out4 = [];
const say4 = s => {
    out4.push(s);
    console.log(s.slice(0, 160));
};
function sec4(name, t, needle, before, after, limit) {
    say4(`\n===== ${name} : ${needle} =====`);
    let i = 0,
        n = 0;
    while (n < limit) {
        const p = t.indexOf(needle, i);
        if (p < 0) break;
        n++;
        say4(`--- #${n} @${p} ---`);
        say4(t.slice(Math.max(0, p - before), p + after));
        i = p + needle.length;
    }
    if (!n) say4("(none)");
}
sec4("T4 responseFormat", T4, "responseFormat", 150, 300, 3);
sec4("T4 safetySettings enum", T4, "HARM_CATEGORY_HARASSMENT", 300, 400, 1);
fs.writeFileSync(DIR + "analysis3b.txt", out4.join("\n"));
console.log("\n[saved] analysis3b.txt lines=" + out4.length);