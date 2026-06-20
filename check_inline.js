const fs = require('fs');
const html = fs.readFileSync('c:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/index.html', 'utf-8');

const scriptRegex = /<script.*?>([\s\S]*?)<\/script>/g;
let match;
let i = 0;
while ((match = scriptRegex.exec(html)) !== null) {
    const code = match[1];
    if (code.trim().length > 0) {
        try {
            new Function(code);
            console.log(`Script ${i} OK`);
        } catch(e) {
            console.error(`Script ${i} Syntax Error:`, e.message);
            fs.writeFileSync(`error_script_${i}.js`, code);
        }
    }
    i++;
}
